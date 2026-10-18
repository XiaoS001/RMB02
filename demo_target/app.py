"""RMB02 离线靶子 demo_target —— 内置小 Flask 服务。

🔴 作用（任务单 v2 第 6 个问题）：保证不依赖外部项目也能演示全链路。
3~5 个接口，含鉴权 + 参数校验，字段契约在 docs/demo_target_api.md。

接口：
  GET  /healthz                 → 200 {"status":"ok"}
  POST /api/echo                → 参数校验：name 必填；返回 {"name": ...}
  GET  /api/items/<item_id>     → 200 或 404（不存在）
  POST /api/login               → 鉴权：username/password 正确才 200
  GET  /api/secure/stats        → 需 Bearer token（/api/login 发的）

只读约束：本靶子独立可起，不依赖任何外部项目。
"""
from flask import Flask, jsonify, request

app = Flask(__name__)

# 演示用「内存账本」：不写库
_ITEMS = {1: 'alpha', 2: 'beta'}
_TOKENS = set()
_VALID_LOGIN = {'demo': 'pass123456'}


@app.get('/healthz')
def healthz():
    return jsonify({'status': 'ok'})


@app.post('/api/echo')
def echo():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': '缺少参数'}), 400
    name = (payload.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name 不能为空'}), 400
    if len(name) > 50:
        return jsonify({'error': 'name 过长'}), 400
    return jsonify({'name': name}), 200


@app.get('/api/items/<int:item_id>')
def get_item(item_id):
    if item_id not in _ITEMS:
        return jsonify({'error': 'item 不存在'}), 404
    return jsonify({'id': item_id, 'name': _ITEMS[item_id]}), 200


@app.post('/api/login')
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    password = (payload.get('password') or '').strip()
    if _VALID_LOGIN.get(username) != password:
        return jsonify({'error': '用户名或密码错误'}), 401
    token = f'tok-{username}-{len(_TOKENS)}'
    _TOKENS.add(token)
    return jsonify({'token': token}), 200


@app.get('/api/secure/stats')
def secure_stats():
    auth = request.headers.get('Authorization', '')
    token = auth.replace('Bearer ', '') if auth.startswith('Bearer ') else ''
    if token not in _TOKENS:
        return jsonify({'error': '未授权'}), 401
    return jsonify({'items': len(_ITEMS), 'tokens': len(_TOKENS)}), 200


if __name__ == '__main__':
    app.run(debug=True, port=5202)
