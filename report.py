"""评测报告 —— 单条明细 + 汇总（任务单阶段 3 第 4 条）。

报告必带 n；稳定性波动区间 + 极差如实报。
"""
import json


def build_report(scores, *, n):
    """scores: [{'model_output_id', 'accuracy', 'relevance', 'format',
                 'hallucination', 'total', 'run_index'}]（Score 表行）"""
    assert len(scores) >= n, f'报告样本不足：需要 {n}，实际 {len(scores)}'

    per_dim = {d: [] for d in ('accuracy', 'relevance', 'format', 'hallucination')}
    totals = []
    for s in scores:
        totals.append(s['total'])
        for d in ('accuracy', 'relevance', 'format'):
            per_dim[d].append(s[d])
        # hallucination 可能 None（N/A）—— 只统计有值的
        if s['hallucination'] is not None:
            per_dim['hallucination'].append(s['hallucination'])

    def summarize(vals):
        if not vals:
            return {'n': 0, 'mean': None, 'min': None, 'max': None, 'range': None}
        return {
            'n': len(vals),
            'mean': round(sum(vals) / len(vals), 1),
            'min': min(vals),
            'max': max(vals),
            'range': round(max(vals) - min(vals), 1),
        }

    return {
        'n': n,
        'total': summarize(totals),
        'dimensions': {d: summarize(per_dim[d]) for d in per_dim},
        'per_sample': [
            {
                'model_output_id': s['model_output_id'],
                'accuracy': s['accuracy'],
                'relevance': s['relevance'],
                'format': s['format'],
                'hallucination': s['hallucination'],
                'total': s['total'],
                'run_index': s['run_index'],
            }
            for s in scores
        ],
    }


def top_failures(report, k=10):
    """失败样本 Top10：total 最低的 k 条（n 小就写 n 小）。"""
    samples = sorted(report['per_sample'], key=lambda s: s['total'])
    return samples[:k]
