"""离线靶子 demo_target 测试 —— 不依赖外部项目独立演示全链路。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo_target.app import app as demo_app  # noqa: E402
from runner import run_case  # noqa: E402


def test_demo_healthz():
    client = demo_app.test_client()
    resp = client.get('/healthz')
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ok'


def test_demo_echo_valid():
    client = demo_app.test_client()
    resp = client.post('/api/echo', json={'name': 'hello'})
    assert resp.status_code == 200
    assert resp.get_json()['name'] == 'hello'


def test_demo_echo_missing_name():
    client = demo_app.test_client()
    resp = client.post('/api/echo', json={})
    assert resp.status_code == 400


def test_demo_items_404():
    client = demo_app.test_client()
    resp = client.get('/api/items/999')
    assert resp.status_code == 404


def test_demo_login_and_secure():
    client = demo_app.test_client()
    login = client.post('/api/login', json={
        'username': 'demo', 'password': 'pass123456',
    })
    assert login.status_code == 200
    token = login.get_json()['token']
    stats = client.get('/api/secure/stats',
                       headers={'Authorization': f'Bearer {token}'})
    assert stats.status_code == 200
    assert 'items' in stats.get_json()


def test_demo_secure_unauthorized():
    client = demo_app.test_client()
    resp = client.get('/api/secure/stats')
    assert resp.status_code == 401


def test_runner_offline_echo():
    from models import Case

    case = Case(path='/api/echo', method='POST',
                body_json='{"name": "hi"}',
                expected='{"status": 200}')
    result = run_case(case, base_url='', offline_app=demo_app)
    assert result['passed'] is True
    assert result['status_code'] == 200


def test_runner_offline_404():
    from models import Case

    case = Case(path='/api/items/999', method='GET',
                body_json=None, expected='{"status": 404}')
    result = run_case(case, base_url='', offline_app=demo_app)
    assert result['passed'] is True
    assert result['status_code'] == 404
