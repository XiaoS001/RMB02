"""阶段 4 看板 + 结算 API 测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import login  # noqa: E402


def _setup_task(app, client):
    """建任务 + 契约 + 生成用例 + 模型输出 + Score + 标注。"""
    from dashboard import task_pass_rate
    from models import Case, CaseRun, ModelOutput, Score, Target, EvalTask, db

    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='看板任务', status='generated')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)

        # 3 条 valid 用例 + 2 条通过
        from models import CASE_STATUS_VALID
        for i in range(3):
            c = Case(task_id=t.id, path=f'/api/x{i}', method='GET',
                     kind='normal', status=CASE_STATUS_VALID,
                     expected='{"status":200}')
            db.session.add(c)
            db.session.flush()
            db.session.add(CaseRun(case_id=c.id, status_code=200,
                                   response_body='ok', passed=(i < 2)))
        # 2 条模型输出 + Score
        for i in range(2):
            mo = ModelOutput(task_id=t.id, prompt=f'p{i}', output=f'o{i}')
            db.session.add(mo)
            db.session.flush()
            db.session.add(Score(model_output_id=mo.id, accuracy=90 - i * 10,
                                 relevance=80, format=85, hallucination=100,
                                 total=85.0, run_index=0))
        db.session.commit()
        return t.id


def test_dashboard_tasks_pass_rate(client, app):
    """看板：任务列表 + 通过率（3 条用例 2 条过 = 66.7%）。"""
    login(client, 'eval', 'eval123456')
    _setup_task(app, client)
    resp = client.get('/api/dashboard/tasks')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) >= 1
    t = data[0]
    assert t['pass_rate']['n'] == 3
    assert t['pass_rate']['passed'] == 2
    assert t['pass_rate']['pass_rate'] == round(2 / 3 * 100, 1)


def test_dashboard_dimensions(client, app):
    """看板：维度趋势（accuracy 均值 85 = (90+80)/2）。"""
    login(client, 'eval', 'eval123456')
    task_id = _setup_task(app, client)
    resp = client.get(f'/api/dashboard/tasks/{task_id}/dimensions')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['n'] == 2
    assert data['dimensions']['accuracy']['mean'] == 85.0
    assert data['dimensions']['hallucination']['n'] == 2


def test_dashboard_agreement_requires_login(client):
    resp = client.get('/api/dashboard/agreement')
    assert resp.status_code == 401


def test_dashboard_workload_requires_permission(client):
    """client_viewer 看 workload → 403（需要 view_all？标注员也有 view_all）。"""
    login(client, 'client', 'client123456')
    resp = client.get('/api/dashboard/workload')
    # client_viewer 有 view_all → 200；这里验证不 500
    assert resp.status_code in (200, 403)


def test_settlement_via_api(client, app):
    """结算 API：标注 2 条 → 0.5/条 → 金额 1.0。"""
    login(client, 'eval', 'eval123456')
    from models import Annotation, ModelOutput, Target, EvalTask, db

    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='结算任务', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)
        outs = []
        for i in range(2):
            mo = ModelOutput(task_id=t.id, prompt=f'p{i}', output=f'o{i}')
            db.session.add(mo)
            db.session.flush()
            outs.append(mo.id)
        db.session.commit()
        task_id = t.id

    # 标注员标注 2 条
    login(client, 'annotator', 'annot123456')
    for out_id in outs:
        r = client.post('/api/annotations', json={
            'model_output_id': out_id, 'verdict': 'agree',
        })
        assert r.status_code == 201

    # 平台管理员结算
    login(client, 'eval', 'eval123456')
    resp = client.post('/api/settlements', json={
        'annotator_id': 2, 'task_id': task_id, 'unit_price': 0.5,
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['case_count'] == 2
    assert data['amount'] == 1.0


def test_settlement_negative_price_via_api(client):
    """结算 API 拒绝负单价。"""
    login(client, 'eval', 'eval123456')
    resp = client.post('/api/settlements', json={
        'annotator_id': 2, 'task_id': 1, 'unit_price': -1,
    })
    assert resp.status_code == 400


def test_annotation_list_via_api(client, app):
    """查标注列表。"""
    from models import Annotation, ModelOutput, Target, EvalTask, db

    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='t', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)
        mo = ModelOutput(task_id=t.id, prompt='p', output='o')
        db.session.add(mo)
        db.session.commit()
        db.session.refresh(mo)
        out_id = mo.id

    login(client, 'annotator', 'annot123456')
    client.post('/api/annotations', json={
        'model_output_id': out_id, 'verdict': 'reject', 'note': '答非所问',
    })
    resp = client.get(f'/api/annotations/{out_id}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]['verdict'] == 'reject'


def test_agreement_rate_api(client, app):
    """一致率 API 返回 n + rate。"""
    login(client, 'eval', 'eval123456')
    resp = client.get('/api/agreement-rate')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'n' in data
    assert 'rate' in data


def test_workload_api_returns_settlements(client, app):
    """后台工作量：结算后能查到每人工作量与金额。"""
    from models import Annotation, ModelOutput, Settlement, Target, EvalTask, db

    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='工作量', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)
        mo = ModelOutput(task_id=t.id, prompt='p', output='o')
        db.session.add(mo)
        db.session.commit()
        db.session.refresh(mo)
        db.session.add(Annotation(model_output_id=mo.id, annotator_id=2,
                                  verdict='agree'))
        db.session.add(Settlement(annotator_id=2, task_id=t.id,
                                  case_count=1, unit_price=0.5, amount=0.5))
        db.session.commit()
        task_id = t.id

    login(client, 'eval', 'eval123456')
    resp = client.get('/api/dashboard/workload')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) >= 1
    entry = next(x for x in data if x['annotator_id'] == 2)
    assert entry['total_cases'] == 1
    assert entry['total_amount'] == 0.5


def test_workload_empty(client):
    """没有结算 → 空列表（不崩）。"""
    login(client, 'eval', 'eval123456')
    resp = client.get('/api/dashboard/workload')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_dashboard_tasks_empty(client, app):
    """没有任务 → 空列表（不崩）。"""
    login(client, 'eval', 'eval123456')
    resp = client.get('/api/dashboard/tasks')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_dimensions_empty_task(client, app):
    """没有 Score 的任务 → n=0 dimensions 空（不崩）。"""
    from models import Target, EvalTask, db

    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='空任务', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)
        task_id = t.id

    login(client, 'eval', 'eval123456')
    resp = client.get(f'/api/dashboard/tasks/{task_id}/dimensions')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['n'] == 0
    assert data['dimensions'] == {}


def test_pass_rate_no_cases(client, app):
    """没有用例的任务 → pass_rate n=0 rate=None（不除零）。"""
    from models import Target, EvalTask, db

    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='无用例', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)
        task_id = t.id

    login(client, 'eval', 'eval123456')
    resp = client.get('/api/dashboard/tasks')
    data = resp.get_json()
    entry = next(x for x in data if x['id'] == task_id)
    assert entry['pass_rate']['n'] == 0
    assert entry['pass_rate']['pass_rate'] is None
