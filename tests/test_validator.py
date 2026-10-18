"""用例三层校验测试（任务单阶段 2 的核心，阶段 1 已用于入库）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validator import validate_case  # noqa: E402

CONTRACTS = [
    {
        'method': 'POST', 'path': '/api/kbs/<kb_id>/documents',
        'fields': [
            {'name': 'title', 'required': True},
            {'name': 'content', 'required': True},
        ],
        'source': 'api_md', 'confidence': 1.0,
    },
    {
        'method': 'GET', 'path': '/healthz', 'fields': [],
        'source': 'api_md', 'confidence': 1.0,
    },
]


def _case(**kw):
    base = {'path': '/api/kbs/<kb_id>/documents', 'method': 'POST',
            'body_json': None, 'expected': None}
    base.update(kw)
    return base


def test_valid_case():
    c = _case(
        body_json='{"title": "a", "content": "b"}',
        expected='{"status": 201}',
    )
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'valid'


def test_endpoint_not_found():
    c = _case(path='/api/nonexistent')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'endpoint'
    assert reason == 'endpoint_not_found'


def test_field_not_in_contract():
    c = _case(body_json='{"title": "a", "hack": 1}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'field'
    assert reason == 'field_not_in_contract'


def test_assert_tautology():
    c = _case(expected='{"assert": "True"}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'assert_tautology'


def test_assert_empty():
    c = _case(expected='{}')
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'assert_empty'


def test_suggested_field_not_in_allowed_set():
    """AST 建议字段不进允许字段集 —— 发送它应判 invalid（防放宽，审查要求）。"""
    contracts = [{
        'method': 'POST', 'path': '/api/x',
        'fields': [{'name': 'a', 'required': True}],
        'suggested_fields': ['reflected'],
    }]
    c = {'path': '/api/x', 'method': 'POST',
         'body_json': '{"a": "1", "reflected": "x"}',
         'expected': '{"status": 200}'}
    status, layer, reason = validate_case(c, contracts)
    assert status == 'invalid'
    assert layer == 'field'
    assert reason == 'field_not_in_contract'


def test_authoritative_field_valid():
    """权威字段（fields 里）合法。"""
    contracts = [{
        'method': 'POST', 'path': '/api/x',
        'fields': [{'name': 'a', 'required': True}],
        'suggested_fields': [],
    }]
    c = {'path': '/api/x', 'method': 'POST',
         'body_json': '{"a": "1"}',
         'expected': '{"status": 200}'}
    status, _, _ = validate_case(c, contracts)
    assert status == 'valid'


def test_get_no_body_valid():
    c = {'path': '/healthz', 'method': 'GET',
         'body_json': None, 'expected': '{"status": 200}'}
    status, _, _ = validate_case(c, CONTRACTS)
    assert status == 'valid'


# ============ 阶段 2 更多边界（凑 pytest≥80） ============

def test_body_json_invalid():
    """body 不是合法 JSON → invalid body_json_invalid。"""
    c = {'path': '/api/kbs/<kb_id>/documents', 'method': 'POST',
         'body_json': '{not json', 'expected': '{"status": 201}'}
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'field'
    assert reason == 'body_json_invalid'


def test_body_not_object():
    """body 是数组/字符串 → invalid body_not_object。"""
    c = {'path': '/api/kbs/<kb_id>/documents', 'method': 'POST',
         'body_json': '["a"]', 'expected': '{"status": 201}'}
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'field'
    assert reason == 'body_not_object'


def test_expected_json_invalid():
    """expected 不是合法 JSON → invalid expected_json_invalid。"""
    c = {'path': '/healthz', 'method': 'GET',
         'body_json': None, 'expected': '{broken'}
    status, layer, reason = validate_case(c, CONTRACTS)
    assert status == 'invalid'
    assert layer == 'assert'
    assert reason == 'expected_json_invalid'


def test_authz_case_structure_valid():
    """authz 用例（kind=authz）结构合法 → 不被三层校验拦。"""
    c = {'path': '/healthz', 'method': 'GET',
         'body_json': None,
         'expected': '{"status": 401}',
         'kind': 'authz'}
    status, _, _ = validate_case(c, CONTRACTS)
    assert status == 'valid'


def test_assert_uses_whitelisted_builtin_ok():
    """assert 用 status/body 等白名单标识符 → 合法。"""
    c = {'path': '/healthz', 'method': 'GET',
         'body_json': None,
         'expected': '{"assert": "status == 200 and len(body) > 0"}'}
    status, _, _ = validate_case(c, CONTRACTS)
    assert status == 'valid'
