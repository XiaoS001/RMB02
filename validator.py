"""用例校验 —— 三层（任务单阶段 2 的核心）。

| 层 | 校验什么 | 不合格原因码 |
|---|---|---|
| 接口存在性 | path + method 必须在契约里 | `endpoint_not_found` |
| 字段合法性 | body 里的字段必须在该接口的契约字段表里 | `field_not_in_contract` |
| 断言可执行性 | 不许恒真（`assert True` / `assert 1`）/ 空断言 / 引用不存在字段 | `assert_tautology` / `assert_empty` / `assert_unknown_field` |

🔴 规则（审查意见 2026-10-18 14:45）：
- AST 的 suggested_fields 只作提示，**不进允许字段集**（两个方向都会错）
- invalid 的用例不参与统计
"""
import ast
import builtins
import json
import re


INVALID_LAYER_ENDPOINT = 'endpoint'
INVALID_LAYER_FIELD = 'field'
INVALID_LAYER_ASSERT = 'assert'

# 恒真断言识别（任务单：assert True / assert 1 / pass）
_TAUTOLOGY_RE = re.compile(
    r'^\s*(true|1|pass|none)\s*$', re.IGNORECASE,
)

# assert 表达式里允许的「运行时环境」标识符（内置函数 + 白名单）
_ASSERT_BUILTINS = set(dir(builtins))
_ASSERT_WHITELIST = {'status', 'body', 'response'}
_ASSERT_ALLOWED = _ASSERT_BUILTINS | _ASSERT_WHITELIST


def validate_case(case, contracts):
    """校验一条用例（Case 对象 / dict）。返回 (status, layer, reason)。

    返回 status: 'valid' 或 'invalid'。
    """
    path = case.get('path') if isinstance(case, dict) else case.path
    method = case.get('method') if isinstance(case, dict) else case.method

    # 层 1：接口存在性
    contract = _find_contract(contracts, method, path)
    if contract is None:
        return 'invalid', INVALID_LAYER_ENDPOINT, 'endpoint_not_found'

    # 层 2：字段合法性（只对有 body 的用例）
    body = case.get('body_json') if isinstance(case, dict) else case.body_json
    known = set()
    if body:
        try:
            body_obj = json.loads(body) if isinstance(body, str) else body
        except json.JSONDecodeError:
            return 'invalid', INVALID_LAYER_FIELD, 'body_json_invalid'
        if not isinstance(body_obj, dict):
            return 'invalid', INVALID_LAYER_FIELD, 'body_not_object'

        known = {f['name'] for f in contract['fields']}
        for key in body_obj:
            if key not in known:
                # 🔴 只认权威字段集（api_md/url_map）。AST 的 suggested_fields
                #    只作提示，**不进允许字段集**。
                return 'invalid', INVALID_LAYER_FIELD, 'field_not_in_contract'

    # 层 3：断言可执行性
    # 🔴 expected 判空用 `is not None`，不用 `if expected`（{} 是 falsy，
    #    会整层跳过 —— 审查发现的 falsy 绕过）。expected 缺失/空也判 invalid。
    expected = case.get('expected') if isinstance(case, dict) else case.expected
    if expected is not None:
        try:
            exp = json.loads(expected) if isinstance(expected, str) else expected
        except json.JSONDecodeError:
            return 'invalid', INVALID_LAYER_ASSERT, 'expected_json_invalid'
        if not isinstance(exp, dict):
            return 'invalid', INVALID_LAYER_ASSERT, 'assert_empty'
        if exp == {}:
            # 显式空对象：没有期望值，既不能判过也不能判失败 → invalid
            return 'invalid', INVALID_LAYER_ASSERT, 'assert_empty'
        if 'status' not in exp and 'assert' not in exp:
            return 'invalid', INVALID_LAYER_ASSERT, 'assert_empty'
        # 恒真断言
        a = exp.get('assert')
        if isinstance(a, str) and _TAUTOLOGY_RE.match(a.strip()):
            return 'invalid', INVALID_LAYER_ASSERT, 'assert_tautology'
        # 断言引用不存在字段：assert 里出现的标识符必须都在
        # 契约字段表 / 白名单（status/body/response）/ 内置函数里
        if isinstance(a, str):
            try:
                ids = _referenced_identifiers(a)
            except SyntaxError:
                # 🔴 语法错不再当「没问题」—— 平台的意义就是抓格式漂移
                return 'invalid', INVALID_LAYER_ASSERT, 'assert_unparseable'
            for ident in ids:
                if ident in _ASSERT_ALLOWED:
                    continue
                if ident not in known:
                    return 'invalid', INVALID_LAYER_ASSERT, 'assert_unknown_field'
    else:
        # expected 缺失：没有期望值的用例不能判"通过"（审查要求不留口子）
        return 'invalid', INVALID_LAYER_ASSERT, 'assert_empty'

    return 'valid', None, None


def _referenced_identifiers(expr):
    """提取 assert 表达式里引用的标识符（变量名/字段名/下标键）。

    🔴 用 ast 解析，避免把字符串字面量里的内容（如 'x'）误当标识符；
    但要识别 `body['hack']` 这种下标键 —— 它是字段引用。
    🔴 不再 catch SyntaxError —— 语法错误由上层判 assert_unparseable，
       绝不当作「没有引用任何标识符」（那会让恒真/未知字段检查全跳过）。
    """
    tree = ast.parse(expr, mode='eval')
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Subscript):
            # body['hack'] / body["name"] —— 字符串下标是字段引用
            if isinstance(node.slice, ast.Constant) \
                    and isinstance(node.slice.value, str):
                names.add(node.slice.value)
    return names


def validate_cases(cases, contracts):
    """批量校验。返回 (results, stats)。

    stats: {generated, valid, invalid, by_layer, by_reason}
    """
    results = []
    counts = {'generated': 0, 'valid': 0, 'invalid': 0,
              'by_layer': {}, 'by_reason': {}}
    for case in cases:
        counts['generated'] += 1
        status, layer, reason = validate_case(case, contracts)
        results.append({'status': status, 'layer': layer, 'reason': reason})
        if status == 'invalid':
            counts['invalid'] += 1
            counts['by_layer'][layer] = counts['by_layer'].get(layer, 0) + 1
            counts['by_reason'][reason] = counts['by_reason'].get(reason, 0) + 1
        else:
            counts['valid'] += 1
    return results, counts


def _find_contract(contracts, method, path):
    for c in contracts:
        if c['method'] == method and c['path'] == path:
            return c
    return None
