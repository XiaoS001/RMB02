"""契约抽取测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract_extractor import (  # noqa: E402
    extract_from_api_md,
    extract_from_ast,
    extract_from_url_map,
    merge_contracts,
)

API_MD = r'D:\WbDshXM\RMB01\docs\API.md'
ADMIN_API = r'D:\WbDshXM\RMB01\admin_api.py'
KB_API = r'D:\WbDshXM\RMB01\kb_api.py'
AUTH_API = r'D:\WbDshXM\RMB01\auth.py'


def test_api_md_parses_endpoints():
    endpoints = extract_from_api_md(API_MD)
    assert len(endpoints) >= 20
    methods = {e['method'] for e in endpoints}
    assert {'GET', 'POST', 'PUT', 'DELETE'} & methods


def test_api_md_register_fields():
    endpoints = extract_from_api_md(API_MD)
    reg = next(e for e in endpoints if e['path'] == '/api/auth/register')
    names = [f['name'] for f in reg['fields']]
    assert 'username' in names
    assert 'password' in names
    # username/password 必填（字段表 ✅）
    req = {f['name']: f['required'] for f in reg['fields']}
    assert req['username'] is True
    assert req['password'] is True


def test_api_md_login_fields_from_json_example():
    endpoints = extract_from_api_md(API_MD)
    login = next(e for e in endpoints if e['path'] == '/api/auth/login')
    names = [f['name'] for f in login['fields']]
    assert 'username' in names
    assert 'password' in names


def test_api_md_ask_fields():
    endpoints = extract_from_api_md(API_MD)
    ask = next(e for e in endpoints if e['path'] == '/api/kbs/<kb_id>/ask')
    names = [f['name'] for f in ask['fields']]
    assert 'question' in names
    assert 'top_k' in names
    assert 'use_agent' in names


def test_api_md_query_string_stripped():
    endpoints = extract_from_api_md(API_MD)
    paths = [e['path'] for e in endpoints]
    assert '/api/platform/usage-ranking' in paths
    assert not any('?' in p for p in paths)


def test_merge_dedupes_api_md_and_url_map():
    api = extract_from_api_md(API_MD)
    url = [{'method': 'POST', 'path': '/api/auth/register', 'source': 'url_map'}]
    merged = merge_contracts(api, url, [])
    reg = next(c for c in merged if c['path'] == '/api/auth/register')
    assert reg['source'] == 'api_md'  # 权威覆盖骨架
    assert reg['confidence'] == 1.0


def test_ast_function_scope_isolates_tenants_fields():
    """🔴 审查缺陷 1：AST 按函数作用域 —— /api/tenants 不再混入别的函数字段。"""
    ast = extract_from_ast([ADMIN_API])
    tenants = [a for a in ast if a['path'] == '/api/tenants' and a['method'] == 'POST']
    assert len(tenants) == 1, f'应只有 1 个 /api/tenants 处理函数，实际 {len(tenants)}'
    fields = tenants[0]['fields']
    # api_create_tenant 只读 name（见 admin_api.py）
    assert 'name' in fields
    # 别的函数的字段（role / plan / question…）绝不能混进来
    assert 'role' not in fields
    assert 'plan' not in fields
    assert 'question' not in fields
    assert 'password' not in fields


def test_ast_function_scope_register_fields():
    """auth.py 的 api_register 只读 username/password/tenant_name。"""
    ast = extract_from_ast([AUTH_API])
    reg = [a for a in ast if a['path'] == '/api/auth/register' and a['method'] == 'POST']
    assert len(reg) == 1
    fields = reg[0]['fields']
    assert 'username' in fields
    assert 'password' in fields
    assert 'tenant_name' in fields
    assert 'role' not in fields      # 🔴 修复后不再读 role
    assert 'tenant_id' not in fields  # 🔴 修复后不再读 tenant_id


def test_ast_suggested_fields_not_in_allowed_set():
    """AST 字段进 suggested_fields，不进 fields（不进允许集）。"""
    ast = extract_from_ast([ADMIN_API])
    tenants = [a for a in ast if a['path'] == '/api/tenants' and a['method'] == 'POST'][0]
    merged = merge_contracts(
        [{'method': 'POST', 'path': '/api/tenants', 'fields': [], 'source': 'api_md'}],
        [{'method': 'POST', 'path': '/api/tenants', 'source': 'url_map'}],
        [tenants],
    )
    c = merged[0]
    # fields 保持权威（这里 api_md 没给字段 → 空），AST 只进 suggested
    assert 'name' in c['suggested_fields']
    assert all('name' != f['name'] for f in c['fields'])
    assert c['confidence'] == 0.2
    assert c['source'] == 'ast'


def test_url_map_excludes_html_pages():
    """🔴 审查缺陷 2：页面路由不进契约。用假 app 验证过滤逻辑。"""
    from flask import Flask

    fake = Flask(__name__)

    @fake.get('/')
    def index(): ...

    @fake.get('/login')
    def login(): ...

    @fake.get('/kbs')
    def kbs(): ...

    @fake.get('/api/kbs')
    def api_kbs(): ...

    @fake.get('/healthz')
    def healthz(): ...

    out = extract_from_url_map(fake)
    paths = [e['path'] for e in out]
    assert '/api/kbs' in paths
    assert '/healthz' in paths
    assert '/' not in paths
    assert '/login' not in paths
    assert '/kbs' not in paths


def test_real_rmb01_url_map_has_no_pages():
    """真实 RMB01：url_map 抽取的契约不含页面路由（读契约产物验证）。"""
    import json

    with open(r'D:\WbDshXM\RMB02\data\contract_rmb01.json', encoding='utf-8') as fh:
        data = json.load(fh)
    page_paths = {'/', '/login', '/kbs', '/org', '/platform'}
    for c in data:
        assert c['path'] not in page_paths, f'页面路由 {c["path"]} 混进契约'


def test_all_contracts_have_confidence_and_source():
    """所有契约必须有 source + confidence（审查验收）。"""
    import json

    with open(r'D:\WbDshXM\RMB02\data\contract_rmb01.json', encoding='utf-8') as fh:
        data = json.load(fh)
    for c in data:
        assert 'source' in c
        assert 'confidence' in c
        assert c['confidence'] in (1.0, 0.5, 0.2)
