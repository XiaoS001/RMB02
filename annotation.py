"""人工标注 + 一致率 + 按条结算（任务单阶段 4）。

🔴 三条硬约束：
1. **Annotation 与 Score 分表**（任务单约束 6）—— 机器分不许覆盖人工标注
2. **一致率 n ≥ 20 真实**：报告里的 n 必须与 Annotation 表行数对得上
3. **Settlement 金额边界**：case_count ≥ 0、unit_price ≥ 0，不许负金额/除零
"""
from models import (
    ALL_VERDICTS,
    Annotation,
    ModelOutput,
    Score,
    Settlement,
    User,
    db,
)


def submit_annotation(model_output_id, annotator_id, verdict, note=None):
    """标注员提交一条标注（新增，不覆盖机器分）。

    🔴 Annotation 与 Score 分表：这里**只写 Annotation**，绝不碰 Score。
    机器分在 Score 表，人工标注在 Annotation 表，两条线独立。
    返回 (annotation, None) 或 (None, error)。
    """
    if verdict not in ALL_VERDICTS:
        return None, f'verdict 必须是 {"/".join(ALL_VERDICTS)} 之一'
    mo = db.session.get(ModelOutput, model_output_id)
    if mo is None:
        return None, '模型输出不存在'

    ann = Annotation(
        model_output_id=model_output_id,
        annotator_id=annotator_id,
        verdict=verdict,
        note=note,
    )
    db.session.add(ann)
    db.session.commit()
    return ann, None


def get_annotations(model_output_id, viewer_id=None):
    """查某条模型输出的人工标注（不查机器分）。

    🔴 越权防护（审查验收 #5）：普通标注员只能看**自己标过**的样本的标注；
    evaluator / platform_admin（有 view_all）可看全部。上层结合角色决定传不传 viewer_id。
    """
    anns = Annotation.query.filter_by(model_output_id=model_output_id).all()
    if viewer_id is None:
        return anns
    return [a for a in anns if a.annotator_id == viewer_id]


def agreement_rate(annotator_id=None):
    """机器分 vs 人工标注一致率。

    🔴 一致率的 n 必须真实：分母 = Annotation 表行数（有对应 Score 的），
    报告里的 n 必须与表行数对得上。
    """
    query = Annotation.query
    if annotator_id is not None:
        query = query.filter_by(annotator_id=annotator_id)
    anns = query.all()

    if not anns:
        return {'n': 0, 'agreed': 0, 'rate': None}

    agreed = 0
    for ann in anns:
        score = Score.query.filter_by(model_output_id=ann.model_output_id).first()
        if score is None:
            continue   # 该样本没有机器分（如 parse failed 未落 Score）—— 不计入
        # 一致定义：标注 agree 且机器总分 ≥ 60（及格线），或标注 doubt/reject 且总分 < 60
        machine_pass = score.total >= 60
        human_pass = ann.verdict in ('agree',)
        if machine_pass == human_pass:
            agreed += 1

    compared = 0
    agreed = 0
    for ann in anns:
        score = Score.query.filter_by(model_output_id=ann.model_output_id).first()
        if score is None:
            continue   # 该样本没有机器分（如 parse failed 未落 Score）—— 不计入
        compared += 1
        # 一致定义：标注 agree 且机器总分 ≥ 60（及格线），或标注 doubt/reject 且总分 < 60
        machine_pass = score.total >= 60
        human_pass = ann.verdict in ('agree',)
        if machine_pass == human_pass:
            agreed += 1

    return {
        # 🔴 分母只算「有机器分」的标注（审查 Bug 2）—— 没 Score 的样本
        #    无法比较，不算进 n，但报出 skipped_no_score 让人看见
        'n': compared,
        'agreed': agreed,
        'rate': round(agreed / compared * 100, 1) if compared else None,
        'skipped_no_score': len(anns) - compared,
    }


def settle_annotator(annotator_id, task_id, unit_price):
    """按条结算：case_count = 该标注员标注的、属于 task_id 的条数。

    🔴 金额边界（审查验收 #4）：
    - case_count 必须 ≥ 0（查询得到，不可能负）
    - unit_price 必须 ≥ 0（负数单价拒绝）
    - amount = case_count * unit_price（0 条 → 0 元，单价 0 → 0 元，无除零）

    🔴 幂等（审查 Bug 1 —— 重复结算会多付一倍）：
    同一 (annotator_id, task_id) **只允许结算一次**。第二次调用直接拒绝，
    数据库唯一约束 uq_settle_annotator_task 是最后一道保险。
    返回 (settlement, None) 或 (None, error)。
    """
    if unit_price is None or unit_price < 0:
        return None, '单价不能为负'

    existing = Settlement.query.filter_by(
        annotator_id=annotator_id, task_id=task_id,
    ).first()
    if existing is not None:
        return None, '该标注员在此任务已结算，如需补结请先撤销'

    # Annotation 没有 task_id —— 通过 model_output 归属任务来数
    anns = (
        db.session.query(Annotation)
        .join(ModelOutput, ModelOutput.id == Annotation.model_output_id)
        .filter(Annotation.annotator_id == annotator_id,
                ModelOutput.task_id == task_id)
        .all()
    )
    count = len(anns)

    # 计算金额（0 条 → 0，单价 0 → 0）
    amount = round(count * unit_price, 2)

    settle = Settlement(
        annotator_id=annotator_id,
        task_id=task_id,
        case_count=count,
        unit_price=unit_price,
        amount=amount,
    )
    db.session.add(settle)
    db.session.commit()
    return settle, None


def settlement_for(annotator_id, task_id):
    """查某标注员在某任务的结算。

    🔴 幂等保证下同一 (annotator, task) 至多一行 —— 返回该行（即应付金额）。
    """
    return Settlement.query.filter_by(
        annotator_id=annotator_id, task_id=task_id,
    ).first()
