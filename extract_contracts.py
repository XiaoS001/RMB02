"""阶段 1 · 契约抽取执行脚本。

用法：
    python extract_contracts.py

从 RMB01 抽取契约 → data/contract_rmb01.json

🔴 只读约束：import RMB01 的 app 拿 url_map，**不起服务、不写库**。
   import 会执行 RMB01/app.py 顶层 `app = create_app()`，这会建表（写库）——
   所以这里用子进程 import，且注入 SQLALCHEMY_DATABASE_URI 指向临时内存库，
   保证不碰 RMB01 的 data/rmb01.db。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RMB01_DIR = r'D:\WbDshXM\RMB01'
OUT = os.path.join(HERE, 'data', 'contract_rmb01.json')

# 从 API.md 解析
API_MD = os.path.join(RMB01_DIR, 'docs', 'API.md')

# AST 扫描的源码（补充字段用）
AST_SOURCES = [
    os.path.join(RMB01_DIR, 'app.py'),
    os.path.join(RMB01_DIR, 'auth.py'),
    os.path.join(RMB01_DIR, 'kb_api.py'),
    os.path.join(RMB01_DIR, 'admin_api.py'),
]

# 需要在 RMB01 环境里跑 url_map 抽取 —— 用 RMB01 自带 venv 的 python
RMB01_PY = os.path.join(RMB01_DIR, 'venv', 'Scripts', 'python.exe')


def run_url_map_harness():
    """子进程 import RMB01 app，打印 url_map 路由表（JSON）。"""
    code = r'''
import json, sys, os
sys.stdout.reconfigure(encoding="utf-8")   # 中文安全
sys.stderr.reconfigure(encoding="utf-8")   # import 时的 warning 也走 utf-8
sys.path.insert(0, r"D:\WbDshXM\RMB01")
os.environ["RMB01_DATABASE_URL"] = "sqlite:///:memory:"
import config
config.DATABASE_URL = "sqlite:///:memory:"
from app import app
rules = []
for rule in app.url_map.iter_rules():
    for method in rule.methods:
        if method in ("HEAD", "OPTIONS"):
            continue
        rules.append({"method": method, "path": rule.rule})
print(json.dumps(rules, ensure_ascii=False))
'''
    result = subprocess.run(
        [RMB01_PY, '-c', code],
        capture_output=True, text=True, encoding='utf-8',
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'url_map harness failed: {result.stderr[:2000]}'
        )
    return json.loads(result.stdout)


def main():
    sys.path.insert(0, HERE)
    # 主进程 stdout 用 UTF-8（避免 Windows GBK 控制台解码中文崩）
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
    from contract_extractor import (
        contract_to_json,
        extract_from_api_md,
        extract_from_ast,
        extract_from_url_map,
        merge_contracts,
    )

    # 1. API.md 显式契约
    api_endpoints = extract_from_api_md(API_MD)
    print(f'[api_md] {len(api_endpoints)} endpoints')

    # 2. url_map（子进程，只读）
    try:
        url_rules = run_url_map_harness()
        from contract_extractor import _normalize_path

        # 🔴 修复审查缺陷 2：只收 API 接口（/api* + /healthz），排除 HTML 页面
        url_endpoints = [
            {'method': r['method'], 'path': _normalize_path(r['path']), 'source': 'url_map'}
            for r in url_rules
            if not r['path'].startswith('/static')
            and (r['path'].startswith('/api') or r['path'] == '/healthz')
        ]
        print(f'[url_map] {len(url_endpoints)} endpoints（已排除页面路由）')
    except Exception as exc:
        print(f'[url_map] FAILED: {exc}  → 只用 API.md + AST')
        url_endpoints = []

    # 3. AST 补充（按函数作用域 → 每接口字段提示）
    ast_path_fields = extract_from_ast(AST_SOURCES)
    ast_total = sum(len(f['fields']) for f in ast_path_fields)
    print(f'[ast] {len(ast_path_fields)} 个接口有字段提示, 共 {ast_total} 字段')

    # 合并
    contracts = merge_contracts(api_endpoints, url_endpoints, ast_path_fields)
    n = contract_to_json(contracts, OUT)

    # 统计
    with open(OUT, encoding='utf-8') as fh:
        data = json.load(fh)
    api_only = [c for c in data if c['source'] == 'api_md']
    print(f'\n[merge] {n} contracts → {OUT}')
    print(f'[merge] api_md 权威 {len(api_only)} / url_map 骨架 {len([c for c in data if c["source"]=="url_map"])} / ast 提示 {len([c for c in data if c["source"]=="ast"])}')
    total_fields = sum(len(c['fields']) for c in data)
    total_suggest = sum(len(c['suggested_fields']) for c in data)
    print(f'[merge] 允许字段集总数 {total_fields} / AST 提示字段 {total_suggest}（不进允许集）')

    # 贴出样例（3 条）
    print('\n[样例 3 条]')
    for c in data[:3]:
        names = [f['name'] for f in c['fields']]
        sugg = c['suggested_fields']
        print(f"  {c['method']} {c['path']}  fields={names}  sugg={sugg}  src={c['source']} conf={c['confidence']}")


if __name__ == '__main__':
    main()
