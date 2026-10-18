"""评测引擎执行器 —— 对靶子发起请求跑用例。

🔴 任务单约束：
- 全程 mock（pytest 零消耗）+ 1 条 @pytest.mark.live 默认 skip
- 靶子只读：不写库、不改造
- 离线靶子 demo_target 直接 import 起测试客户端（无需起服务）
"""
import json
import time
import urllib.error
import urllib.request


def run_case(case, base_url, offline_app=None):
    """执行一条用例。返回 {status_code, response_body, passed, error, duration_ms}。

    - offline_app: 传入 demo_target 的 Flask app → 用 test_client 跑（离线）
    - 否则走 HTTP（base_url）
    """
    path = case.path
    method = case.method
    body = None
    if case.body_json:
        try:
            body = json.loads(case.body_json)
        except json.JSONDecodeError:
            body = None
    expected = None
    if case.expected:
        try:
            expected = json.loads(case.expected)
        except json.JSONDecodeError:
            expected = None

    start = time.monotonic()
    try:
        if offline_app is not None:
            status_code, response_body = _run_offline(offline_app, method, path, body)
        else:
            status_code, response_body = _run_http(base_url, method, path, body)
        duration_ms = int((time.monotonic() - start) * 1000)
        passed = _judge(status_code, response_body, expected)
        return {
            'status_code': status_code,
            'response_body': response_body,
            'passed': passed,
            'error': None,
            'duration_ms': duration_ms,
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            'status_code': None,
            'response_body': None,
            'passed': False,
            'error': str(exc)[:500],
            'duration_ms': duration_ms,
        }


def _run_offline(app, method, path, body):
    client = app.test_client()
    resp = client.open(path, method=method, json=body) \
        if body is not None else client.open(path, method=method)
    return resp.status_code, resp.get_data(as_text=True)


def _run_http(base_url, method, path, body):
    url = base_url.rstrip('/') + path
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json'} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace')


def _judge(status_code, response_body, expected):
    """断言：期望 status 与实际一致；若有 assert 表达式则求值。"""
    if expected is None:
        return False
    if isinstance(expected, dict):
        want = expected.get('status')
        if want is not None and status_code != want:
            return False
        a = expected.get('assert')
        if a:
            try:
                return bool(eval(a, {'__builtins__': {}}, {'status': status_code, 'body': response_body}))
            except Exception:
                return False
        return want is not None
    return False
