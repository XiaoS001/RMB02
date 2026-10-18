"""稳定性实测 —— n=5 条样本 × 3 遍 = 15 次调用（任务单阶段 3）。

🔴 复验意见风险 3 / 4：
- n 写死：5 条 × 3 遍 = 15 次
- 每条报**分数波动区间**（min/max），不只整体均值
- 报**极差**（max - min）
- `run_index` 是**独立的三次完整评测**：Score 表里同一 model_output_id
  真有 3 条不同 run_index（不许一次评测塞 3 个样本冒充）
"""
import json

from models import ModelOutput, Score, db
from scorer import score_from_model_output


def run_stability_test(model_outputs, *, run_count=3):
    """对 n 条模型输出跑 run_count 遍打分（默认 3 遍 = n×3 次）。

    model_outputs: [{'prompt': str, 'output': str, 'reference': str|None,
                     'has_evidence': bool, 'citations': list|None}]

    返回：
      - scores: 落库的 Score 记录（每 output × 每 run_index 一条）
      - report: {n, run_count, per_sample: [{prompt, scores_by_run, min, max, range}],
                 overall_range, overall_mean}
    """
    assert len(model_outputs) == 5, f'稳定性 n 必须=5，实际 {len(model_outputs)}'
    assert run_count == 3, f'稳定性遍数必须=3，实际 {run_count}'

    all_scores = []
    per_sample = []

    for sample_idx, m in enumerate(model_outputs):
        mo = ModelOutput(
            task_id=m.get('task_id'),
            prompt=m['prompt'],
            output=m['output'],
            model=m.get('model', 'local-rule'),
            run_index=0,   # ModelOutput 本体 run_index 占位；Score 才有真 run_index
        )
        db.session.add(mo)
        db.session.flush()

        sample_scores = []
        for run_index in range(run_count):
            # 🔴 独立 3 遍：每遍重新打分（本地规则打分确定性高；真实模型每遍会不同）
            result = score_from_model_output(
                _model_score_for(m, run_index),
                m['output'],
                has_evidence=m.get('has_evidence', False),
                citations=m.get('citations'),
            )
            if not result['ok']:
                # 打分解析失败：不落 Score（不污染统计），记录原因
                sample_scores.append({'parse_failed': True, 'reason': result['reason']})
                continue
            score = result['score']
            rec = Score(
                model_output_id=mo.id,
                accuracy=score['accuracy'],
                relevance=score['relevance'],
                format=score['format'],
                hallucination=score['hallucination'],
                total=score['total'],
                run_index=run_index,
            )
            db.session.add(rec)
            sample_scores.append({'total': score['total'], 'run_index': run_index})
            all_scores.append(score['total'])

        db.session.commit()

        totals = [s['total'] for s in sample_scores if 'total' in s]
        per_sample.append({
            'prompt': m['prompt'][:60],
            'scores_by_run': sample_scores,
            'min': min(totals) if totals else None,
            'max': max(totals) if totals else None,
            'range': (max(totals) - min(totals)) if totals else None,
        })

    valid_totals = [s for s in all_scores if isinstance(s, (int, float))]
    return {
        'scores': all_scores,
        'report': {
            'n': len(model_outputs),
            'run_count': run_count,
            'total_calls': len(model_outputs) * run_count,
            'per_sample': per_sample,
            'overall_min': min(valid_totals) if valid_totals else None,
            'overall_max': max(valid_totals) if valid_totals else None,
            'overall_range': (max(valid_totals) - min(valid_totals)) if valid_totals else None,
            'overall_mean': round(sum(valid_totals) / len(valid_totals), 1) if valid_totals else None,
        },
    }


def _model_score_for(m, run_index):
    """模拟模型输出的结构化分数（带轻微波动，模拟真实模型的非确定性）。

    真实场景由模型返回；这里规则生成，且允许注入 parse_failed 场景测试兜底。
    """
    if m.get('force_parse_failed'):
        return m['force_parse_failed'] if isinstance(m['force_parse_failed'], str) \
            else '不是 JSON'
    base = {
        'accuracy': 80 + (run_index * 2),
        'relevance': 85,
        'format': 90,
        'hallucination': 100 if m.get('has_evidence') else None,
    }
    return json.dumps(base, ensure_ascii=False)
