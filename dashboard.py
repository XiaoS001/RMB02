"""看板聚合 —— 任务列表 / 通过率 / 各维度趋势 / 标注一致率（阶段 4）。

🔴 所有数字带 n；invalid 用例不参与统计。
"""
from models import Annotation, Case, CaseRun, ModelOutput, Score, db


def task_pass_rate(task_id):
    """任务通过率：valid 用例里 passed 的比例（带 n）。"""
    cases = Case.query.filter_by(task_id=task_id).all()
    valid = [c for c in cases if c.status == 'valid']
    if not valid:
        return {'n': 0, 'passed': 0, 'pass_rate': None}

    passed = 0
    for c in valid:
        run = CaseRun.query.filter_by(case_id=c.id).first()
        if run is not None and run.passed:
            passed += 1
    return {
        'n': len(valid),
        'passed': passed,
        'pass_rate': round(passed / len(valid) * 100, 1),
    }


def dimension_trend(task_id):
    """各维度平均分趋势（accuracy/relevance/format/hallucination，带 n）。"""
    scores = (
        db.session.query(Score)
        .join(ModelOutput, ModelOutput.id == Score.model_output_id)
        .filter(ModelOutput.task_id == task_id)
        .all()
    )
    if not scores:
        return {'n': 0, 'dimensions': {}}

    dims = {d: [] for d in ('accuracy', 'relevance', 'format', 'hallucination')}
    for s in scores:
        dims['accuracy'].append(s.accuracy)
        dims['relevance'].append(s.relevance)
        dims['format'].append(s.format)
        if s.hallucination is not None:   # N/A 不计
            dims['hallucination'].append(s.hallucination)

    return {
        'n': len(scores),
        'dimensions': {
            d: {
                'mean': round(sum(v) / len(v), 1) if v else None,
                'n': len(v),
            }
            for d, v in dims.items()
        },
    }


def annotation_agreement_trend(annotator_id=None):
    """标注一致率（带 n 与标注人）。"""
    from annotation import agreement_rate

    rep = agreement_rate(annotator_id)
    rep['annotator_id'] = annotator_id
    return rep


def annotator_workload():
    """后台查每人工作量与应付金额（Settlement 汇总）。"""
    from models import Settlement, User

    rows = db.session.query(Settlement).all()
    by_user = {}
    for s in rows:
        user = db.session.get(User, s.annotator_id)
        name = user.username if user else f'#{s.annotator_id}'
        cur = by_user.setdefault(s.annotator_id, {
            'annotator_id': s.annotator_id, 'username': name,
            'total_cases': 0, 'total_amount': 0.0, 'settlements': 0,
        })
        cur['total_cases'] += s.case_count
        cur['total_amount'] = round(cur['total_amount'] + s.amount, 2)
        cur['settlements'] += 1
    return list(by_user.values())
