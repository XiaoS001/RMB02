"""阶段 4 · 一致率 n≥20 真实样本测试（任务单硬要求）。

🔴 一致率的 n 必须真实：报告里的 n 与 Annotation 表行数一致（n≥20）。
这里建 20+ 条真实样本（模型输出 + Score + 人工标注）→ 验证一致率。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from annotation import agreement_rate, submit_annotation  # noqa: E402
from models import (  # noqa: E402
    EvalTask,
    ModelOutput,
    Score,
    Target,
    db,
)


def _make_20_samples(app):
    """建 20 条模型输出 + Score + 人工标注（n=20 真实）。"""
    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='一致率20', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)

        out_ids = []
        for i in range(20):
            mo = ModelOutput(task_id=t.id, prompt=f'问题{i}', output=f'答案{i}' * 3)
            db.session.add(mo)
            db.session.flush()
            # 前 16 条机器分高（≥60 → 通过），后 4 条低（<60 → 不通过）
            high = i < 16
            total = 85.0 if high else 30.0
            db.session.add(Score(
                model_output_id=mo.id, accuracy=total, relevance=80, format=80,
                hallucination=100, total=total, run_index=0,
            ))
            out_ids.append(mo.id)
        db.session.commit()

        # 人工标注：前 16 条 agree（与机器一致），后 4 条 agree（与机器不一致）
        for i, out_id in enumerate(out_ids):
            submit_annotation(out_id, 1, 'agree')
        return out_ids


def test_agreement_rate_n20_real(app):
    """一致率 n=20 真实：n 与 Annotation 表行数一致。"""
    _make_20_samples(app)
    with app.app_context():
        rep = agreement_rate(annotator_id=1)
        # n 必须 = 20（表行数）
        assert rep['n'] == 20
        # 16 条 agree(机器高) 一致 + 4 条 agree(机器低) 不一致 = 16/20 = 80%
        assert rep['agreed'] == 16
        assert rep['rate'] == 80.0


def test_agreement_rate_per_annotator(app):
    """一致率按标注人：不同标注人 n 独立。"""
    _make_20_samples(app)
    with app.app_context():
        # 标注人 2 没标过 → n=0
        rep2 = agreement_rate(annotator_id=2)
        assert rep2['n'] == 0
        assert rep2['rate'] is None


def test_agreement_n_matches_db_rows(app):
    """🔴 硬校验：报告 n 必须与 Annotation 表行数对得上。"""
    from models import Annotation
    _make_20_samples(app)
    with app.app_context():
        rows = Annotation.query.filter_by(annotator_id=1).count()
        rep = agreement_rate(annotator_id=1)
        assert rep['n'] == rows == 20


def test_agreement_with_doubt_verdicts(app):
    """存疑/打回：机器分高但标注 doubt → 不一致。"""
    _make_20_samples(app)
    with app.app_context():
        # 把第 0 条改成 doubt（机器 85 通过，人存疑 → 不一致）
        from models import Annotation
        ann = Annotation.query.filter_by(annotator_id=1).first()
        ann.verdict = 'doubt'
        db.session.commit()
        rep = agreement_rate(annotator_id=1)
        assert rep['n'] == 20
        assert rep['agreed'] == 15   # 16 - 1
