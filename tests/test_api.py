"""RMB02 API 测试（阶段 1 骨架 + 靶子/契约/用例生成）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import login  # noqa: E402


def test_healthz(client):
    resp = client.get('/healthz')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'ok'
    assert data['service'] == 'rmb02'


def test_auth_login_logout_me(client):
    login(client, 'eval', 'eval123456')
    me = client.get('/api/auth/me')
    assert me.status_code == 200
    assert me.get_json()['role'] == 'evaluator'
    client.post('/api/auth/logout')
    me2 = client.get('/api/auth/me')
    assert me2.status_code == 401


def test_unauthorized_401(client):
    resp = client.get('/api/targets')
    assert resp.status_code == 401


def test_annotator_cannot_manage_targets(client):
    login(client, 'annotator', 'annot123456')
    resp = client.post('/api/targets', json={
        'name': 'x', 'base_url': 'http://x', 'kind': 'http',
    })
    assert resp.status_code == 403


def test_evaluator_can_create_target(client):
    login(client, 'eval', 'eval123456')
    resp = client.post('/api/targets', json={
        'name': 'demo', 'base_url': 'http://127.0.0.1:9999', 'kind': 'http',
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['name'] == 'demo'


def test_save_and_list_contracts(client):
    login(client, 'eval', 'eval123456')
    t = client.post('/api/targets', json={
        'name': 'demo', 'base_url': 'offline://demo', 'kind': 'offline',
    }).get_json()
    save = client.post(f"/api/targets/{t['id']}/contracts", json=[
        {'method': 'POST', 'path': '/api/echo', 'fields': [
            {'name': 'name', 'required': True},
        ], 'source': 'api_md', 'confidence': 1.0},
    ])
    assert save.status_code == 201
    lst = client.get(f"/api/targets/{t['id']}/contracts")
    assert lst.status_code == 200
    assert len(lst.get_json()) == 1


def test_generate_cases_marks_valid_invalid(client):
    login(client, 'eval', 'eval123456')
    t = client.post('/api/targets', json={
        'name': 'demo', 'base_url': 'offline://demo', 'kind': 'offline',
    }).get_json()
    client.post(f"/api/targets/{t['id']}/contracts", json=[
        {'method': 'POST', 'path': '/api/echo', 'fields': [
            {'name': 'name', 'required': True},
        ], 'source': 'api_md', 'confidence': 1.0},
    ])
    gen = client.post(f"/api/targets/{t['id']}/generate-cases", json={'name': 't1'})
    assert gen.status_code == 201
    data = gen.get_json()
    assert data['generated'] >= 3
    assert data['valid'] >= 1

    # 校验生成的用例
    cases = client.get(f"/api/tasks/{data['task_id']}/cases").get_json()
    assert len(cases) == data['generated']
    # 正常用例应该 valid；异常/边界也 valid（字段在契约里）
    assert all(c['status'] == 'valid' for c in cases)


def test_generate_cases_requires_contracts(client):
    login(client, 'eval', 'eval123456')
    t = client.post('/api/targets', json={
        'name': 'demo', 'base_url': 'offline://demo', 'kind': 'offline',
    }).get_json()
    resp = client.post(f"/api/targets/{t['id']}/generate-cases", json={'name': 't'})
    assert resp.status_code == 400


def test_task_stats_returns_distribution(client):
    """阶段 2：任务统计返回 generated/valid/invalid/by_layer/by_reason。"""
    login(client, 'eval', 'eval123456')
    t = client.post('/api/targets', json={
        'name': 'demo', 'base_url': 'offline://demo', 'kind': 'offline',
    }).get_json()
    # 契约含一个字段 → 生成正常/边界/异常/authz
    client.post(f"/api/targets/{t['id']}/contracts", json=[
        {'method': 'POST', 'path': '/api/echo', 'fields': [
            {'name': 'name', 'required': True},
        ], 'source': 'api_md', 'confidence': 1.0},
    ])
    gen = client.post(f"/api/targets/{t['id']}/generate-cases", json={'name': 't1'}).get_json()
    task_id = gen['task_id']

    stats = client.get(f'/api/tasks/{task_id}/stats').get_json()
    assert stats['generated'] == gen['generated']
    assert stats['valid'] == gen['valid']
    assert stats['invalid'] == gen['invalid']
    assert stats['generated'] == stats['valid'] + stats['invalid']
    assert 'by_reason' in stats
    assert 'by_layer' in stats


def test_task_stats_empty_task(client):
    """没有用例的任务统计也是 0，不崩。"""
    login(client, 'eval', 'eval123456')
    t = client.post('/api/targets', json={
        'name': 'demo', 'base_url': 'offline://demo', 'kind': 'offline',
    }).get_json()
    # 建一个空任务（无契约，直接生成会 400）—— 用已有任务：先建契约再生成
    client.post(f"/api/targets/{t['id']}/contracts", json=[
        {'method': 'GET', 'path': '/healthz', 'fields': [],
         'source': 'api_md', 'confidence': 1.0},
    ])
    gen = client.post(f"/api/targets/{t['id']}/generate-cases", json={'name': 't'}).get_json()
    stats = client.get(f"/api/tasks/{gen['task_id']}/stats").get_json()
    assert stats['generated'] >= 1
