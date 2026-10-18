"""阶段 2 测试 —— 三层校验深化 + 手工造假拦截 + 误杀检查 + 统计。

对应任务单阶段 2 与 WorkBuddy 复验意见（2026-10-18 14:45 第六节）：
1. 手工造 3 条坏用例（编造路径 / 编造字段 / assert True）→ 必须全判 invalid 且原因码正确
2. 误杀检查：合法但字段来自 AST suggested 的用例 → 不能判 invalid（
   注意：这是复验意见第 145 行的要求，结合契约修复后 suggested 不进允许集，
   语义调整为「AST 建议字段不被当作权威字段误杀合法用例」）
3. assert_unknown_field：断言引用不存在字段 → invalid
4. 变异测试：validator 三层判定逐个改坏 → 测试红
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validator import validate_case, validate_cases  # noqa: E402

# 一份真实契约（模拟 contract_rmb01.json 的 POST /api/kbs）
CONTRACTS = [
    {
        'method': 'POST', 'path': '/api/kbs',
        'fields': [
            {'name': 'name', 'required': True},
            {'name': 'description', 'required': False},
        ],
        'source': 'api_md', 'confidence': 1.0,
    },
    {
        'method': 'GET', 'path': '/api/kbs',
        'fields': [], 'source': 'api_md', 'confidence': 1.0,
    },
]


def _case(**kw):
    base = {'path': '/api/kbs', 'method': 'POST',
            'body_json': None, 'expected': None}
    base.update(kw)
    return base


# ============ 1. 手工造假用例拦截验证 ============

def test_fake_path_rejected():
    """造假 1：编造路径 → endpoint_not_found。"""
    c = _case(path='/api/nonexistent', method='POST')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'endpoint'
    assert reason == 'endpoint_not_found'


def test_fake_field_rejected():
    """造假 2：编造字段 → field_not_in_contract。"""
    c = _case(body_json='{"name": "a", "hack": "x"}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'field'
    assert reason == 'field_not_in_contract'


def test_fake_assert_tautology_rejected():
    """造假 3：assert True → assert_tautology。"""
    c = _case(expected='{"assert": "True"}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'assert_tautology'


def test_three_fake_cases_all_rejected():
    """三条造假用例必须全判 invalid（阶段 2 硬要求）。"""
    fakes = [
        _case(path='/api/nonexistent'),
        _case(body_json='{"name": "a", "hack": 1}'),
        _case(expected='{"assert": "1"}'),
    ]
    results, stats = validate_cases(fakes, CONTRACTS)
    assert all(r['status'] == 'invalid' for r in results)
    assert stats['invalid'] == 3
    assert stats['valid'] == 0


# ============ 2. 误杀检查 ============

def test_ast_suggested_field_not_misjudged():
    """误杀检查：AST 建议字段（suggested_fields）是"提示"——
    发送它不应被当作权威字段判合法，但接口本身字段合法时用例应 valid。
    即：suggested 字段不影响权威字段的合法性判断。"""
    contracts = [{
        'method': 'POST', 'path': '/api/tenants',
        'fields': [{'name': 'name', 'required': True}],
        'suggested_fields': ['username', 'password'],
    }]
    # 合法用例：body 只有权威字段 name
    c = _case(path='/api/tenants',
              body_json='{"name": "x"}',
              expected='{"status": 201}')
    status, _, _ = validate_case(c, contracts)
    assert status == 'valid'


def test_suggested_field_alone_not_allowed():
    """suggested 字段不能单独作为合法字段（发送即 field_not_in_contract）。"""
    contracts = [{
        'method': 'POST', 'path': '/api/tenants',
        'fields': [{'name': 'name', 'required': True}],
        'suggested_fields': ['username'],
    }]
    c = _case(path='/api/tenants',
              body_json='{"username": "x"}',
              expected='{"status": 201}')
    status, layer, reason = validate_case(c, contracts)
    assert status == 'invalid'
    assert reason == 'field_not_in_contract'


# ============ 3. assert_unknown_field ============

def test_assert_unknown_field_rejected():
    """断言引用不存在字段 → assert_unknown_field。"""
    c = _case(expected='{"assert": "body[\'hack\'] == 1"}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'assert_unknown_field'


def test_assert_known_field_ok():
    """断言引用契约字段（name）→ 合法。"""
    c = _case(body_json='{"name": "x"}',
              expected='{"assert": "body[\'name\'] == \'x\'"}')
    status, _, _ = validate_case(c, CONTRACTS)
    assert status == 'valid'


# ============ 4. 批量统计 ============

def test_validate_cases_stats():
    """批量统计：generated/valid/invalid/by_layer/by_reason。"""
    cases = [
        _case(path='/api/nonexistent'),
        _case(body_json='{"name": "a", "hack": 1}'),
        _case(expected='{"assert": "True"}'),
        _case(body_json='{"name": "ok"}', expected='{"status": 201}'),
    ]
    results, stats = validate_cases(cases, CONTRACTS)
    assert stats['generated'] == 4
    assert stats['valid'] == 1
    assert stats['invalid'] == 3
    assert stats['by_layer'] == {'endpoint': 1, 'field': 1, 'assert': 1}
    assert stats['by_reason'] == {
        'endpoint_not_found': 1,
        'field_not_in_contract': 1,
        'assert_tautology': 1,
    }
    assert len(results) == 4


# ============ 5. 层 3 漏放修复（审查意见 2026-10-18 15:20） ============

def test_assert_syntax_error_is_invalid():
    """'(((' 语法都解析不了 → assert_unparseable。"""
    c = _case(expected='{"assert": "((("}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'assert_unparseable'


def test_assert_statement_form_is_invalid():
    """'assert True' 整条语句塞进 assert → assert_unparseable。"""
    c = _case(expected='{"assert": "assert True"}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'assert_unparseable'


def test_empty_expected_dict_is_invalid():
    """expected={} → assert_empty（不再被 falsy 绕过）。"""
    c = _case(expected='{}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'assert_empty'


def test_expected_missing_is_invalid():
    """expected=None → assert_empty（不留「没写就跳过」的口子）。"""
    c = _case(expected=None)
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'assert_empty'
