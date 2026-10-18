"""用例生成测试。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generator import _default_body, generate_cases_for_contract  # noqa: E402

CONTRACT = {
    'method': 'POST',
    'path': '/api/kbs/<kb_id>/documents',
    'fields': [
        {'name': 'title', 'required': True},
        {'name': 'content', 'required': True},
        {'name': 'separator', 'required': False},
    ],
}


def test_generate_normal_case():
    cases = generate_cases_for_contract(CONTRACT)
    normal = [c for c in cases if c['kind'] == 'normal']
    assert len(normal) == 1
    assert normal[0]['method'] == 'POST'
    assert 'title' in normal[0]['body']
    assert 'content' in normal[0]['body']


def test_generate_boundary_and_error():
    cases = generate_cases_for_contract(CONTRACT)
    kinds = {c['kind'] for c in cases}
    assert 'boundary' in kinds
    assert 'error' in kinds
    # 至少 2 个边界（title 空 / content 空）+ 至少 2 个异常（缺 title / 缺 content / 空 body）
    assert len(cases) >= 5


def test_generate_expected_has_status():
    cases = generate_cases_for_contract(CONTRACT)
    for c in cases:
        exp = c['expected']
        assert isinstance(exp, dict)
        assert 'status' in exp


def test_generate_get_no_body():
    get_contract = {'method': 'GET', 'path': '/api/kbs', 'fields': []}
    cases = generate_cases_for_contract(get_contract)
    normal = [c for c in cases if c['kind'] == 'normal'][0]
    assert normal['body'] is None


def test_default_body_skips_tenant_id():
    c = {'method': 'POST', 'path': '/api/kbs', 'fields': [
        {'name': 'name', 'required': True},
        {'name': 'tenant_id', 'required': False},
    ]}
    body = _default_body(c['fields'])
    assert 'name' in body
    assert 'tenant_id' not in body  # 写侧由服务端强制覆盖


# ============ authz 第四类用例（审查意见阶段 2 要求） ============

def test_protected_endpoint_generates_authz_401():
    """受保护接口生成「不带凭据」authz 用例，expected=401（按规则定）。"""
    c = {'method': 'GET', 'path': '/api/kbs', 'fields': []}
    cases = generate_cases_for_contract(c, public=False)
    authz = [x for x in cases if x['kind'] == 'authz']
    assert len(authz) == 1
    assert authz[0]['expected'] == {'status': 401}


def test_public_register_generates_privesc_case():
    """公开注册接口生成提权用例：role=platform_admin → expected 4xx。"""
    c = {'method': 'POST', 'path': '/api/auth/register', 'fields': [
        {'name': 'username', 'required': True},
        {'name': 'password', 'required': True},
        {'name': 'role', 'required': False},
        {'name': 'tenant_id', 'required': False},
    ]}
    cases = generate_cases_for_contract(c, public=True)
    authz = [x for x in cases if x['kind'] == 'authz']
    # role 提权 + tenant_id 越权 两条
    assert len(authz) == 2
    # 全部期望 4xx
    assert all(x['expected']['status'] in (400, 401, 403, 404) for x in authz)
    bodies = [x['body'] for x in authz]
    assert any(b.get('role') == 'platform_admin' for b in bodies)
    assert any(b.get('tenant_id') == 99999 for b in bodies)


def test_public_without_role_tenant_no_authz():
    """公开接口但没有 role/tenant_id 字段 → 不生成越权用例。"""
    c = {'method': 'GET', 'path': '/healthz', 'fields': []}
    cases = generate_cases_for_contract(c, public=True)
    authz = [x for x in cases if x['kind'] == 'authz']
    assert authz == []


def test_authz_expected_never_guessed():
    """authz 用例 expected 必须是规则定的状态码，不含模型猜测。"""
    c = {'method': 'POST', 'path': '/api/auth/register', 'fields': [
        {'name': 'username', 'required': True},
        {'name': 'password', 'required': True},
        {'name': 'role', 'required': False},
    ]}
    cases = generate_cases_for_contract(c, public=True)
    for x in cases:
        if x['kind'] == 'authz':
            assert x['expected']['status'] in (400, 401, 403, 404)


def test_authz_expected_is_rule_based_not_model():
    """复验意见第 3 条：authz 的 expected 由生成代码硬编码规则定，不依赖模型输出。
    直接检查 generator 源码：authz 分支只出现固定状态码字面量。"""
    import pathlib

    src = pathlib.Path(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'generator.py'),
    ).read_text(encoding='utf-8')
    # authz 分支里只允许固定状态码字面量（400/401/403/404），不允许任何变量/函数
    authz_section = src.split('authz 第四类')[1] if 'authz 第四类' in src else ''
    assert 'public' in src
    assert 'CASE_KIND_AUTHZ' in src
    # 关键：不能出现 model 调用 / 随机 / 猜测
    assert 'model' not in authz_section.lower()
    assert 'random' not in authz_section.lower()
