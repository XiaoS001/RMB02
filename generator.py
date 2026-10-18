"""用例生成 —— 对契约里的每个接口生成 正常/边界/异常 三类用例。

🔴 任务单约束：
- 生成的用例是「结构化 JSON」，先入库**未经校验**（阶段 2 校验）
- 用例的 expected 用可执行断言（可被阶段 2 的"断言可执行性"校验）
- 不引第三方 LLM 依赖 —— 模型生成由 runner 调，这里是**规则模板生成** + 可选模型增强
"""
import json
import os

from models import (
    CASE_KIND_AUTHZ,
    CASE_KIND_BOUNDARY,
    CASE_KIND_ERROR,
    CASE_KIND_NORMAL,
    db,
)


# ============ 规则模板：按接口类型生成三类用例 ============

# 每个接口的字段「合理值」模板。key 是字段名 → 值生成器。
_FIELD_SAMPLES = {
    'username': lambda: 'alice',
    'password': lambda: 'pass123456',
    'role': lambda: 'org_admin',
    'tenant_id': lambda: 1,
    'name': lambda: '新知识库',
    'description': lambda: '演示用描述',
    'question': lambda: '连接池大小怎么配',
    'top_k': lambda: 3,
    'use_agent': lambda: False,
    'title': lambda: '运维手册',
    'content': lambda: '第一段。\n\n第二段。\n\n第三段。',
    'plan': lambda: 'pro',
    'limit': lambda: 10,
}

# 边界值：空字符串、超大、类型错
_BOUNDARY_VALUES = {
    'username': ['', 'a' * 300],
    'password': ['', '123'],
    'name': ['', 'x' * 300],
    'description': ['x' * 600],
    'question': ['', 'q' * 501],
    'top_k': [0, -1, 9999],
    'title': [''],
    'content': [''],
    'role': ['invalid_role'],
    'plan': ['invalid_plan'],
    'tenant_id': [0, -1],
}


def _default_body(fields):
    """按字段表生成一个合理请求体（必填字段给值）。"""
    body = {}
    for f in fields:
        name = f['name']
        if name in ('tenant_id',):  # 写侧 tenant_id 由服务端强制覆盖，不放进 body
            continue
        if name in _FIELD_SAMPLES:
            body[name] = _FIELD_SAMPLES[name]()
        else:
            body[name] = 'demo'
    return body


def _boundary_bodies(fields):
    """为每个字段生成边界/异常变体。"""
    out = []
    for f in fields:
        name = f['name']
        if name == 'tenant_id':
            continue
        if name in _BOUNDARY_VALUES:
            for bad in _BOUNDARY_VALUES[name]:
                body = _default_body(fields)
                body[name] = bad
                out.append(('boundary', body))
    return out


def _error_bodies(fields):
    """异常类：缺必填字段 / 完全空 body。"""
    out = []
    required = [f['name'] for f in fields if f.get('required')]
    if required:
        for name in required:
            body = _default_body(fields)
            body.pop(name, None)
            out.append(('error', body))
    out.append(('error', {}))
    return out


def generate_cases_for_contract(contract, public=False):
    """对一个接口生成 [{path, method, body, kind, expected}]。

    public=True 表示该接口无需登录（公开注册/健康检查等）。
    🔴 authz 第四类用例（审查意见阶段 2 要求）：
    - 公开接口 → 生成「提权/越权」用例（传 role / tenant_id），expected 4xx
    - 受保护接口 → 生成「不带凭据」用例，expected 401/404
    expected 一律按规则定（不是模型猜）。
    """
    path = contract['path']
    method = contract['method']
    fields = contract['fields']

    cases = []

    # 正常（请求体合理；GET 不带 body）
    if method == 'GET':
        cases.append({
            'path': path, 'method': method, 'body': None,
            'kind': CASE_KIND_NORMAL,
            'expected': {'status': 200},
        })
    else:
        cases.append({
            'path': path, 'method': method,
            'body': _default_body(fields),
            'kind': CASE_KIND_NORMAL,
            'expected': {'status': 200},
        })

    # 边界 / 异常
    for kind, body in _boundary_bodies(fields):
        cases.append({
            'path': path, 'method': method, 'body': body,
            'kind': CASE_KIND_BOUNDARY if kind == 'boundary' else CASE_KIND_ERROR,
            'expected': {'status': 400},
        })
    for kind, body in _error_bodies(fields):
        cases.append({
            'path': path, 'method': method, 'body': body,
            'kind': CASE_KIND_ERROR,
            'expected': {'status': 400},
        })

    # 🔴 authz 第四类：越权/提权（expected 按规则定，不由模型猜）
    if public:
        # 公开接口：尝试提权（role）或越权（tenant_id）→ 期望 4xx
        if 'role' in {f['name'] for f in fields}:
            body = _default_body(fields)
            body['role'] = 'platform_admin'
            cases.append({
                'path': path, 'method': method, 'body': body,
                'kind': CASE_KIND_AUTHZ,
                'expected': {'status': 400},   # 非法提权应被拒
            })
        if 'tenant_id' in {f['name'] for f in fields}:
            body = _default_body(fields)
            body['tenant_id'] = 99999
            cases.append({
                'path': path, 'method': method, 'body': body,
                'kind': CASE_KIND_AUTHZ,
                'expected': {'status': 400},   # 越权加入应被拒
            })
    else:
        # 受保护接口：不带凭据 → 期望 401（未登录）
        cases.append({
            'path': path, 'method': method,
            'body': _default_body(fields) if method != 'GET' else None,
            'kind': CASE_KIND_AUTHZ,
            'expected': {'status': 401},
        })

    return cases


# 公开接口（无需登录）—— 依 RMB01 实测：注册/建租户/健康检查
PUBLIC_PATHS = {
    '/api/auth/register',
    '/api/tenants',
    '/healthz',
}


def generate_all(contracts):
    """对全部契约生成用例，返回 Case 对象列表（未入库）。"""
    from models import Case, EvalTask

    cases = []
    for contract in contracts:
        public = contract['path'] in PUBLIC_PATHS
        for spec in generate_cases_for_contract(contract, public=public):
            cases.append(Case(
                path=spec['path'],
                method=spec['method'],
                body_json=json.dumps(spec['body'], ensure_ascii=False) if spec['body'] is not None else None,
                expected=json.dumps(spec['expected'], ensure_ascii=False),
                kind=spec['kind'],
            ))
    return cases
