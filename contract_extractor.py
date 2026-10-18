"""契约抽取 —— 从靶子抽出「path + method + 字段表」。

🔴 三级来源（任务单第二节优先级，v2 解决 v1 第 3 个问题）：
1. 显式契约：靶子的 docs/API.md（字段的权威来源）
2. url_map 补全：import 靶子 app 拿完整路由表（只读，不起服务不写库）
3. AST 扫描：源码里 `request.get_json().get('x')` 字面量（只作补充，标注低置信度）

校验规则（阶段 2 用）：字段不在契约里 → invalid，原因 `field_not_in_contract`。
反射来的字段只补充已知字段表，不能单独作为否决依据。
"""
import ast
import json
import os
import re

# ============ 来源 1：解析 API.md ============

# 匹配：### `POST /api/auth/register`
_ENDPOINT_RE = re.compile(r'^###\s+`(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s`]+)`')

# 匹配请求体 JSON 里的字段行：| `username` | ✅ | 长度有下限 |（三列，说明列忽略）
_FIELD_ROW_RE = re.compile(
    r'^\|\s*`([a-zA-Z_][a-zA-Z0-9_]*)`\s*\|\s*([^|]*?)\s*\|\s*[^|]*\|?$'
)


def _is_required_hint(text: str) -> bool:
    t = text.strip()
    return t.startswith('✅') or '必填' in t


# 匹配请求体 JSON 示例里的字段名（从 { ... } 里抽键）
_JSON_KEYS_RE = re.compile(r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:')


def _extract_keys_from_json_block(line):
    """从一行 JSON 示例提取字段名。"""
    return _JSON_KEYS_RE.findall(line)


def extract_from_api_md(md_path):
    """解析 API.md → [{method, path, fields:[...], source:'api_md'}]"""
    with open(md_path, encoding='utf-8') as fh:
        lines = fh.readlines()

    endpoints = []
    current = None
    in_request_table = False
    in_json_block = False
    seen_response_marker = False   # 遇到「响应/错误」字样后不再把 JSON 当请求体
    for line in lines:
        m = _ENDPOINT_RE.match(line)
        if m:
            current = {
                'method': m.group(1),
                'path': m.group(2).split('?')[0],   # 剥掉 query（?limit=10）
                'fields': [],
                'source': 'api_md',
            }
            endpoints.append(current)
            in_request_table = False
            in_json_block = False
            seen_response_marker = False
            continue

        if current is None:
            continue

        # 响应/错误标记：之后的 JSON 块是响应体，不再当请求字段
        if re.search(r'(响应|错误|状态码|错误码)', line):
            seen_response_marker = True

        # ``` 开闭：进入/退出 JSON 示例块
        if line.strip().startswith('```'):
            if not in_json_block:
                in_json_block = True
            else:
                in_json_block = False
            continue

        # 在 JSON 示例块里且未到响应段：提取字段名（请求体）
        # 🔴 只有 POST/PUT/PATCH 有请求体；GET/DELETE 的 JSON 块是响应体
        if in_json_block and not seen_response_marker \
                and current['method'] in ('POST', 'PUT', 'PATCH'):
            for name in _extract_keys_from_json_block(line):
                if name not in {f['name'] for f in current['fields']}:
                    current['fields'].append({
                        'name': name,
                        'required': False,   # JSON 示例不标必填；有字段表的用表覆盖
                    })
            continue

        # 进入字段说明表：| 字段 | 必填 | 说明 | 的表头分隔行（|---|...|---|）
        if re.match(r'^\|[\s\-|]+\|?$', line.strip()):
            in_request_table = True
            continue

        if in_request_table:
            fm = _FIELD_ROW_RE.match(line)
            if fm:
                required_hint = fm.group(2).strip()
                # 字段表是权威：覆盖同名（required 以表为准），去重
                name = fm.group(1)
                for f in current['fields']:
                    if f['name'] == name:
                        f['required'] = _is_required_hint(required_hint)
                        break
                else:
                    current['fields'].append({
                        'name': name,
                        'required': _is_required_hint(required_hint),
                    })

    return endpoints


# ============ 来源 2：url_map ============
# 需要排除的非业务路由
_NON_BUSINESS_PREFIXES = ('/static',)


def _normalize_path(path: str) -> str:
    """路径归一：<int:kb_id> 与 <kb_id> 统一为 <kb_id>（同一接口两种写法）。"""
    return re.sub(r'<int:([^>]+)>', r'<\1>', path)


def extract_from_url_map(target_app):
    """从 Flask app 的 url_map 抽取路由 → [{method, path, source:'url_map'}]

    🔴 修复审查缺陷 2：**排除 HTML 页面路由**，契约只收 API 接口。
    判据：路径以 /api 开头，或等于 /healthz（健康检查）。
    （页面路由 /  /login  /kbs  /org  /platform 渲染 HTML，不是 API）
    """
    out = []
    for rule in target_app.url_map.iter_rules():
        if rule.rule.startswith(_NON_BUSINESS_PREFIXES):
            continue
        if not (rule.rule.startswith('/api') or rule.rule == '/healthz'):
            continue
        for method in rule.methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            out.append({
                'method': method,
                'path': _normalize_path(rule.rule),
                'source': 'url_map',
            })
    return out


# ============ 来源 3：AST 扫描（按函数作用域） ============
# 匹配 .get('x') 字面量（payload / data / body / request.get_json()）
_GETJSON_GET_RE = re.compile(
    r"(?:request\.get_json\([^)]*\)|payload|data|body)\.get\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]",
)

# 匹配 route 装饰器：@app.post('/api/x') / @application.get(...)
_ROUTE_DECORATOR_RE = re.compile(
    r"@(?:app|application|bp)\.(get|post|put|delete|patch)\(\s*['\"](/[^'\"]+)['\"]",
)


def extract_from_ast(source_paths):
    """扫源码 → {normalized_path: [字段名]}（**按函数作用域隔离**）。

    🔴 修复审查缺陷 1：AST 不能跨函数混字段。
    定位每个 route 处理函数的 def 块，只扫该函数体内的 .get('x')。
    返回按 path 归并的字段集 —— 每个接口只含「自己处理函数」里读的字段。
    """
    per_path = {}   # path -> set(fields)

    for path in source_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue

        tree = None
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        # 先收集每个 def 函数名 → 行号区间
        funcs = {}   # name -> (start_line, end_line)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, 'end_lineno', node.lineno)
                funcs[node.name] = (node.lineno, end)

        # 再收集 route 装饰器 → 函数名
        route_of = {}   # func_name -> (method, path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                        if dec.func.attr in ('get', 'post', 'put', 'delete', 'patch'):
                            if dec.args and isinstance(dec.args[0], ast.Constant):
                                route_of[node.name] = (
                                    dec.func.attr.upper(), dec.args[0].value,
                                )

        # 对每个函数，扫函数体内 .get('x')
        for name, (start, end) in funcs.items():
            if name not in route_of:
                continue
            method, route_path = route_of[name]
            body_lines = text.splitlines()[start - 1:end]
            body_text = '\n'.join(body_lines)
            fields = set(_GETJSON_GET_RE.findall(body_text))
            if not fields:
                continue
            norm = _normalize_path(route_path)
            per_path.setdefault((method, norm), set()).update(fields)

    # 转成可合并结构
    return [
        {'method': m, 'path': p, 'fields': sorted(fs)}
        for (m, p), fs in per_path.items()
    ]


# ============ 合并 ============
def merge_contracts(api_md_endpoints, url_map_endpoints, ast_path_fields):
    """合并三级来源 → 契约 JSON。

    - api_md 有字段表 → 权威（confidence=1.0, source=api_md）
    - url_map 补 path/method（confidence=0.5, source=url_map）
    - ast 按函数作用域给出每接口的字段**提示**（confidence=0.2, source=ast）
      🔴 审查要求：AST 只作提示（suggested_fields），**不进允许字段集** ——
      低置信来源两个方向都会错（误杀 + 放宽），不能参与字段校验。
    """
    merged = {}

    # 先 url_map 全量（作为骨架，保证接口存在性校验有依据）
    for ep in url_map_endpoints:
        key = (ep['method'], _normalize_path(ep['path']))
        merged[key] = {
            'method': ep['method'],
            'path': _normalize_path(ep['path']),
            'fields': [],
            'suggested_fields': [],
            'source': 'url_map',
            'confidence': 0.5,
        }

    # api_md 覆盖（字段表 + 更高置信度）
    for ep in api_md_endpoints:
        key = (ep['method'], _normalize_path(ep['path']))
        if key in merged:
            merged[key]['fields'] = ep['fields']
            merged[key]['source'] = 'api_md'
            merged[key]['confidence'] = 1.0
        else:
            merged[key] = {
                'method': ep['method'],
                'path': _normalize_path(ep['path']),
                'fields': ep['fields'],
                'suggested_fields': [],
                'source': 'api_md',
                'confidence': 1.0,
            }

    # ast 按 (method, path) 合并为 suggested_fields（**不进 fields**）
    # 🔴 代码真相校正：AST 函数作用域扫到的是「代码实际读取的字段」。
    #    若 api_md 字段表里某字段代码已不读（安全修复后文档过时），移除之 ——
    #    否则允许集虚高，阶段 2 校验会放行已不存在的字段（审查验收第 4 条）。
    for ep in ast_path_fields:
        key = (ep['method'], _normalize_path(ep['path']))
        if key not in merged:
            continue
        contract = merged[key]
        code_reads = set(ep['fields'])

        # api_md 权威字段里代码不读的 → 移除（文档过时）
        if contract['fields']:
            contract['fields'] = [
                f for f in contract['fields'] if f['name'] in code_reads
            ]

        # AST 额外字段（api_md 没有的）→ suggested，不进允许集
        known = {f['name'] for f in contract['fields']}
        extra = [n for n in ep['fields'] if n not in known]
        contract['suggested_fields'] = extra
        if extra:
            contract['source'] = 'ast'
            contract['confidence'] = 0.2

    return list(merged.values())


def contract_to_json(contracts, target_path):
    """写 data/contract_*.json。"""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, 'w', encoding='utf-8') as fh:
        json.dump(contracts, fh, ensure_ascii=False, indent=2)
    return len(contracts)
