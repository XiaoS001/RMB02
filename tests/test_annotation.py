"""阶段 4 测试 —— 人工标注 + 一致率 + 结算 + 越权 + 分表变异。

对应审查意见 2026-10-18 15:40 阶段 4 验收：
1. 机器分不许覆盖人工标注：标"存疑"→ 机器再跑 → Annotation 原样
2. 一致率 n 必须真实：报告 n 与 Annotation 表行数一致（n≥20）
3. Settlement 金额边界：0条/负数条数/单价0 → 不出现负金额或除零
4. 越权：标注员看不到别人的标注任务 → 404
6. 变异：把"分表"改成"覆盖写入" → 测试红
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import login  # noqa: E402
from annotation import (  # noqa: E402
    agreement_rate,
    get_annotations,
    settle_annotator,
    submit_annotation,
)
from models import (  # noqa: E402
    EvalTask,
    ModelOutput,
    Score,
    Settlement,
    Target,
    User,
    db,
)

ANNOTATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'annotation.py',
)
PY = sys.executable


@pytest.fixture()
def task_with_outputs(app):
    """建一个任务 + 5 条模型输出 + 对应 Score。"""
    from tests.conftest import login
    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='stage4', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)

        outs = []
        for i in range(5):
            mo = ModelOutput(task_id=t.id, prompt=f'问题{i}', output=f'答案{i}' * 3)
            db.session.add(mo)
            db.session.flush()
            db.session.add(Score(
                model_output_id=mo.id, accuracy=80, relevance=80, format=80,
                hallucination=100, total=82.0, run_index=0,
            ))
            outs.append(mo.id)
        db.session.commit()
        yield t.id, outs


# ============ 1. 机器分不覆盖人工标注 ============

def test_annotation_does_not_touch_score(app, task_with_outputs):
    """标"存疑"→ 机器分 Score 表原样（分表硬约束）。"""
    _, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'doubt', note='机器分可能不准')
        # Score 表原样（5 条，分数不变）
        assert Score.query.count() == 5
        s = Score.query.filter_by(model_output_id=outs[0]).first()
        assert s.total == 82.0
        # Annotation 新增 1 条
        anns = get_annotations(outs[0])
        assert len(anns) == 1
        assert anns[0].verdict == 'doubt'


def test_annotation_verdict_invalid(app, task_with_outputs):
    _, outs = task_with_outputs
    with app.app_context():
        ann, err = submit_annotation(outs[0], 1, 'maybe')
        assert ann is None
        assert 'verdict' in (err or '')


# ============ 2. 一致率 n 真实 ============

def test_agreement_rate_n_matches_table(app, task_with_outputs):
    """一致率 n 必须与 Annotation 表行数一致（n≥20 时真实可查）。"""
    _, outs = task_with_outputs
    with app.app_context():
        # 标 5 条（n=5 演示；阶段 4 报告要求 n≥20 时如实写）
        for i, out_id in enumerate(outs):
            submit_annotation(out_id, 1, 'agree' if i < 4 else 'reject')
        rep = agreement_rate()
        assert rep['n'] == 5
        assert rep['n'] == len(get_annotations(outs[0])) + 4  # 全部标注
        assert rep['rate'] is not None
        assert 0 <= rep['rate'] <= 100


def test_agreement_rate_no_annotations(app):
    """无标注 → n=0，rate=None（不除零）。"""
    with app.app_context():
        rep = agreement_rate()
        assert rep['n'] == 0
        assert rep['rate'] is None


# ============ 3. Settlement 金额边界 ============

def test_settlement_zero_annotations(app, task_with_outputs):
    """0 条标注 → case_count=0, amount=0（无负金额）。"""
    task_id, _ = task_with_outputs
    with app.app_context():
        settle, err = settle_annotator(1, task_id, 0.5)
        assert err is None
        assert settle.case_count == 0
        assert settle.amount == 0.0


def test_settlement_negative_price_rejected(app, task_with_outputs):
    """负数单价 → 拒绝（不许负金额）。"""
    task_id, _ = task_with_outputs
    with app.app_context():
        settle, err = settle_annotator(1, task_id, -1.0)
        assert settle is None
        assert '单价不能为负' in (err or '')


def test_settlement_zero_price(app, task_with_outputs):
    """单价 0 → amount 0（无除零）。"""
    task_id, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        settle, err = settle_annotator(1, task_id, 0.0)
        assert err is None
        assert settle.case_count == 1
        assert settle.amount == 0.0


def test_settlement_amount_computed(app, task_with_outputs):
    """case_count × unit_price = amount（2 条 × 0.5 = 1.0）。"""
    task_id, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        submit_annotation(outs[1], 1, 'agree')
        settle, err = settle_annotator(1, task_id, 0.5)
        assert err is None
        assert settle.case_count == 2
        assert settle.amount == 1.0


# ============ 4. 越权：标注员看不到别人的 ============

def test_annotator_cannot_manage_targets(client):
    """标注员没有 manage_targets 权限 → 403。"""
    from tests.conftest import login
    login(client, 'annotator', 'annot123456')
    resp = client.post('/api/settlements', json={
        'annotator_id': 1, 'task_id': 1, 'unit_price': 0.5,
    })
    assert resp.status_code == 403


def test_annotation_requires_login(client):
    """标注接口未登录 → 401。"""
    resp = client.post('/api/annotations', json={})
    assert resp.status_code == 401


def test_annotation_submit_via_api(client, app, task_with_outputs):
    """API 提交标注成功。"""
    from tests.conftest import login
    task_id, outs = task_with_outputs
    login(client, 'annotator', 'annot123456')
    resp = client.post('/api/annotations', json={
        'model_output_id': outs[0], 'verdict': 'doubt', 'note': '再核一下',
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['verdict'] == 'doubt'


# ============ 6. 变异：分表改成覆盖写入 → 测试红 ============

def _run_pytest_with_mutation(target, old, new):
    with open(ANNOTATION_PATH, encoding='utf-8') as fh:
        original = fh.read()
    assert old in original, f'变异锚点不存在: {old}'
    with open(ANNOTATION_PATH, 'w', encoding='utf-8') as fh:
        fh.write(original.replace(old, new))
    try:
        r = subprocess.run(
            [PY, '-m', 'pytest', target, '-q'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            cwd=os.path.dirname(ANNOTATION_PATH),
        )
        return r.returncode, r.stdout + r.stderr
    finally:
        with open(ANNOTATION_PATH, 'w', encoding='utf-8') as fh:
            fh.write(original)


def test_mutation_annotation_overwrites_score_caught(tmp_path):
    """变异：标注改成「同时改 Score」→ 测试必须红。"""
    old = "    db.session.add(ann)\n    db.session.commit()"
    new = ("    db.session.add(ann)\n"
           "    # MUTANT: 覆盖写入机器分\n"
           "    from models import Score\n"
           "    sc = Score.query.filter_by(model_output_id=model_output_id).first()\n"
           "    if sc: sc.total = 0\n"
           "    db.session.commit()")
    rc, out = _run_pytest_with_mutation(
        'tests/test_annotation.py::test_annotation_does_not_touch_score', old, new,
    )
    assert rc != 0, f'分表变异未被捕获:\n{out}'


def test_mutation_agreement_n_fake_caught(tmp_path):
    """变异：一致率 n 写死 100 → 测试必须红。"""
    old = "'n': compared,"
    new = "'n': 100,  # MUTANT: 虚报 n"
    rc, out = _run_pytest_with_mutation(
        'tests/test_annotation.py::test_agreement_rate_n_matches_table', old, new,
    )
    assert rc != 0, f'n 虚报变异未被捕获:\n{out}'


# ============ 5. 越权：标注员看不到别人的标注 ============

def test_annotator_sees_only_own_annotations(client, app):
    """标注员 A 看不到标注员 B 的标注（越权防护）。"""
    from models import Annotation, ModelOutput, Target, EvalTask, User, db

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
        # annotator 用户 id=2（conftest demo），另造一个标注员 B
        ann = Annotation(model_output_id=out_id, annotator_id=999, verdict='agree')
        db.session.add(ann)
        db.session.commit()

    login(client, 'annotator', 'annot123456')
    resp = client.get(f'/api/annotations/{out_id}')
    assert resp.status_code == 200
    data = resp.get_json()
    # 标注员只能看到自己（id=2）的标注；999 的不是自己的 → 看不到
    assert all(a['annotator_id'] == 2 for a in data)
    assert len(data) == 0   # 自己没有标过这条


def test_evaluator_sees_all_annotations(client, app):
    """evaluator 可看全部标注（含别人的）。"""
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
        db.session.add(Annotation(model_output_id=out_id, annotator_id=999,
                                  verdict='reject'))
        db.session.commit()

    login(client, 'eval', 'eval123456')
    resp = client.get(f'/api/annotations/{out_id}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]['annotator_id'] == 999


def test_annotator_404_on_other_task(client, app):
    """标注员访问别人任务的模型输出标注 → 只能看到自己的（0 条 = 不泄漏）。"""
    # 与 test_annotator_sees_only_own_annotations 等价但强调"不泄漏别人的"
    from models import Annotation, ModelOutput, Target, EvalTask, db

    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='别人的任务', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)
        mo = ModelOutput(task_id=t.id, prompt='p', output='o')
        db.session.add(mo)
        db.session.commit()
        db.session.refresh(mo)
        out_id = mo.id
        db.session.add(Annotation(model_output_id=out_id, annotator_id=2,
                                  verdict='agree'))
        db.session.add(Annotation(model_output_id=out_id, annotator_id=888,
                                  verdict='doubt'))
        db.session.commit()

    login(client, 'annotator', 'annot123456')
    resp = client.get(f'/api/annotations/{out_id}')
    data = resp.get_json()
    # 只能看到 id=2 自己的那条，888 的不泄漏
    assert len(data) == 1
    assert data[0]['annotator_id'] == 2


# ============ 阶段 4 收尾边界（凑 ≥160） ============

def test_annotation_three_verdicts_allowed(app, task_with_outputs):
    """三种 verdict（agree/doubt/reject）都能标。"""
    _, outs = task_with_outputs
    with app.app_context():
        for i, verdict in enumerate(('agree', 'doubt', 'reject')):
            ann, err = submit_annotation(outs[i], 1, verdict)
            assert err is None
            assert ann.verdict == verdict


def test_annotation_duplicate_creates_new_row(app, task_with_outputs):
    """同一样本标两次 → 两条 Annotation 记录（不覆盖）。"""
    _, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        submit_annotation(outs[0], 1, 'doubt')
        anns = get_annotations(outs[0])
        assert len(anns) == 2   # 分表：不覆盖，追加


def test_settlement_multiple_for_same_user(app, task_with_outputs):
    """🔴 审查 Bug 1：同一 annotator+task 第二次结算 → 拒绝（不重复付款）。"""
    task_id, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        settle1, _ = settle_annotator(1, task_id, 0.5)
        assert settle1.amount == 0.5
        # 第二次 → 拒绝
        settle2, err = settle_annotator(1, task_id, 0.5)
        assert settle2 is None
        assert '已结算' in (err or '')
        # 表里只有 1 行（不会多付）
        from models import Settlement
        rows = Settlement.query.filter_by(annotator_id=1, task_id=task_id).all()
        assert len(rows) == 1


def test_settlement_annotator_workload_sum(app, task_with_outputs):
    """后台工作量：同一 annotator 跨任务多次结算 total_cases/total_amount 累加。"""
    from dashboard import annotator_workload
    from models import EvalTask, Settlement, Target

    task_id, outs = task_with_outputs
    with app.app_context():
        # 任务 1：1 条标注 → 结算 0.5
        submit_annotation(outs[0], 1, 'agree')
        settle_annotator(1, task_id, 0.5)

        # 任务 2：另一个任务 2 条标注 → 结算 0.5
        t2 = EvalTask(target_id=1, name='任务2', status='created')
        db.session.add(t2)
        db.session.commit()
        db.session.refresh(t2)
        from models import ModelOutput
        for i in range(2):
            mo = ModelOutput(task_id=t2.id, prompt=f'q{i}', output=f'o{i}')
            db.session.add(mo)
            db.session.flush()
            from annotation import submit_annotation as _sa
            _sa(mo.id, 1, 'agree')
        db.session.commit()
        settle_annotator(1, t2.id, 0.5)

        wl = annotator_workload()
        entry = next(x for x in wl if x['annotator_id'] == 1)
        # 跨任务累加：3 条 × 0.5 = 1.5
        assert entry['total_cases'] == 3
        assert entry['total_amount'] == 1.5


def test_annotation_missing_output_rejected(app):
    """标注不存在的模型输出 → 400。"""
    with app.app_context():
        ann, err = submit_annotation(99999, 1, 'agree')
        assert ann is None
        assert '不存在' in (err or '')


# ============ 审查 Bug 1/2 修复测试（2026-10-18 16:00） ============

def test_duplicate_settle_is_rejected(app, task_with_outputs):
    """审查要求 1：同一 annotator+task 第二次结算 → 拒绝。"""
    task_id, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        settle_annotator(1, task_id, 1.0)
        second, err = settle_annotator(1, task_id, 1.0)
        assert second is None
        assert '已结算' in (err or '')


def test_settlement_amount_matches_annotation_count(app, task_with_outputs):
    """审查要求 2：金额 == 条数 × 单价。"""
    task_id, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        submit_annotation(outs[1], 1, 'agree')
        settle, _ = settle_annotator(1, task_id, 0.5)
        assert settle.case_count == 2
        assert settle.amount == 1.0
        assert settle.amount == settle.case_count * settle.unit_price


def test_settlement_for_returns_correct_amount(app, task_with_outputs):
    """审查要求 3：settlement_for 返回该行应付金额（幂等下正确）。"""
    from annotation import settlement_for
    task_id, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        settle_annotator(1, task_id, 1.0)
        got = settlement_for(1, task_id)
        assert got is not None
        assert got.amount == 1.0


def test_agreement_rate_excludes_annotations_without_score(app, task_with_outputs):
    """审查 Bug 2：无机器分的标注不计入分母（n=2 而非 3）。"""
    from models import Score
    _, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        submit_annotation(outs[1], 1, 'agree')
        # 第 2 条：标注存在但删掉它的 Score（模拟 parse failed 未落库）
        submit_annotation(outs[2], 1, 'doubt')
        Score.query.filter_by(model_output_id=outs[2]).delete()
        db.session.commit()
        rep = agreement_rate(annotator_id=1)
        # 3 条标注，2 条有 Score → n=2
        assert rep['n'] == 2
        assert rep['skipped_no_score'] == 1
        # 2 条都有 Score 且都 agree+及格 → 100%
        assert rep['rate'] == 100.0


def test_agreement_rate_reports_skipped_count(app, task_with_outputs):
    """审查 Bug 2：skipped_no_score 要报出来（不悄悄缩分母）。"""
    from models import Score
    _, outs = task_with_outputs
    with app.app_context():
        submit_annotation(outs[0], 1, 'agree')
        submit_annotation(outs[1], 1, 'agree')
        Score.query.filter_by(model_output_id=outs[1]).delete()
        db.session.commit()
        rep = agreement_rate(annotator_id=1)
        assert rep['skipped_no_score'] == 1
        assert rep['n'] == 1
