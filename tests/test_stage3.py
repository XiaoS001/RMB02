"""阶段 3 测试 —— 评测引擎四维度 + 格式漂移兜底 + 稳定性 + 变异。

对应复验意见 2026-10-18 15:25 的 4 风险 7 验收：
1. 打分格式漂移兜底：坏 JSON → score_parse_failed，不许当 0 分
2. 无证据幻觉维度 N/A（None），不是 0/100
3. 稳定性 n=5×3=15，每条波动区间 + 极差
4. run_index 独立 3 遍：Score 表同 model_output_id 真有 3 条不同 run_index
6. 变异测试：权重改坏 / N/A 逻辑去掉 → 测试红
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorer import (  # noqa: E402
    HALLUCINATION_NA,
    WEIGHTS,
    parse_model_score,
    score_from_model_output,
    score_output,
)

SCORER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scorer.py',
)
PY = sys.executable


# ============ 风险 1：打分格式漂移兜底 ============

def test_parse_clean_json():
    r = parse_model_score('{"accuracy":85,"relevance":90,"format":88,"hallucination":100}')
    assert r['ok'] is True
    assert r['score']['accuracy'] == 85.0


def test_parse_markdown_fence():
    r = parse_model_score('```json\n{"accuracy":85,"relevance":90,"format":88,"hallucination":100}\n```')
    assert r['ok'] is True


def test_parse_chinese_prefix():
    r = parse_model_score('分数如下：{"accuracy":85,"relevance":90,"format":88,"hallucination":100}')
    assert r['ok'] is True


def test_parse_score_with_unit():
    r = parse_model_score('{"accuracy":"85分","relevance":90,"format":88,"hallucination":100}')
    assert r['ok'] is True
    assert r['score']['accuracy'] == 85.0


def test_parse_missing_dimension_fails():
    r = parse_model_score('{"accuracy":85,"relevance":90}')
    assert r['ok'] is False
    assert r['reason'] == 'score_parse_failed'


def test_parse_bad_json_fails_not_zero():
    r = parse_model_score('不是 JSON')
    assert r['ok'] is False
    assert r['reason'] == 'score_parse_failed'
    # 🔴 不许静默当 0 分：没有 score 字段，只有 raw
    assert 'score' not in r
    assert 'raw' in r


def test_parse_out_of_range_fails():
    r = parse_model_score('{"accuracy":150,"relevance":90,"format":88,"hallucination":100}')
    assert r['ok'] is False


# ============ 风险 2：无证据幻觉 N/A ============

def test_hallucination_na_without_evidence():
    """无证据 → hallucination=None（N/A），不是 0 也不是 100。"""
    s = score_output('随便生成', has_evidence=False)
    assert s['hallucination'] is None
    assert s['hallucination'] != 0
    assert s['hallucination'] != 100


def test_hallucination_na_not_in_total():
    """无证据：幻觉维不计入总分（归一后总分仍 0~100）。"""
    s = score_output('一段合理长度的回答内容', has_evidence=False)
    assert 0 <= s['total'] <= 100


def test_model_score_hallucination_forced_na():
    """模型给了 hallucination 数字，但无证据 → 强置 None。"""
    r = score_from_model_output(
        '{"accuracy":85,"relevance":90,"format":88,"hallucination":100}',
        '输出', has_evidence=False,
    )
    assert r['ok'] is True
    assert r['score']['hallucination'] is None


def test_hallucination_na_sentinel_value():
    assert HALLUCINATION_NA is None


# ============ 风险 3/4：稳定性 n=5×3=15 + run_index ============

def _make_5_samples(task_id=None):
    return [
        {'prompt': f'问题{i}', 'output': f'答案内容{i}' * 3,
         'reference': f'参考答案{i}' * 2,
         'has_evidence': True, 'citations': [1],
         'task_id': task_id}
        for i in range(5)
    ]


@pytest.fixture()
def eval_task(app):
    """建一个 EvalTask（ModelOutput.task_id NOT NULL，需先有 Target）。"""
    from models import EvalTask, Target, db
    with app.app_context():
        tgt = Target(name='demo', base_url='offline://demo', kind='offline')
        db.session.add(tgt)
        db.session.commit()
        db.session.refresh(tgt)
        t = EvalTask(target_id=tgt.id, name='stability', status='created')
        db.session.add(t)
        db.session.commit()
        db.session.refresh(t)
        yield t.id


def test_stability_requires_n5(app, eval_task):
    """n 必须=5。"""
    from stability import run_stability_test
    with app.app_context():
        with pytest.raises(AssertionError):
            run_stability_test(_make_5_samples(eval_task)[:3])


def test_stability_requires_3_runs(app, eval_task):
    """遍数必须=3。"""
    from stability import run_stability_test
    with app.app_context():
        with pytest.raises(AssertionError):
            run_stability_test(_make_5_samples(eval_task), run_count=2)


def test_stability_n5x3_report(app, eval_task):
    """稳定性报告：n=5 × 3 遍 = 15 次，含每条波动区间 + 极差。"""
    from stability import run_stability_test
    with app.app_context():
        result = run_stability_test(_make_5_samples(eval_task))
        rep = result['report']
        assert rep['n'] == 5
        assert rep['run_count'] == 3
        assert rep['total_calls'] == 15
        assert len(rep['per_sample']) == 5
        for sample in rep['per_sample']:
            assert sample['min'] is not None
            assert sample['max'] is not None
            assert sample['range'] is not None
        assert rep['overall_range'] is not None
        assert rep['overall_mean'] is not None


def test_score_table_has_3_run_index_per_output(app, eval_task):
    """🔴 风险 4：Score 表里同一 model_output_id 真有 3 条不同 run_index。"""
    from models import ModelOutput, Score
    from stability import run_stability_test
    with app.app_context():
        run_stability_test(_make_5_samples(eval_task))
        for mo in ModelOutput.query.all():
            run_indexes = {s.run_index for s in Score.query.filter_by(model_output_id=mo.id)}
            assert run_indexes == {0, 1, 2}, f'应 3 条不同 run_index，实际 {run_indexes}'


def test_stability_parse_failed_not_scored(app, eval_task):
    """parse failed 的样本不落 Score（不污染统计）。"""
    from models import Score
    from stability import run_stability_test
    samples = _make_5_samples(eval_task)
    samples[0]['force_parse_failed'] = '坏分数输出'
    with app.app_context():
        run_stability_test(samples)
        # 第 0 条 3 遍全 parse failed → 不落 Score；其余 4 条各 3 条 = 12
        assert Score.query.count() == 4 * 3


# ============ 变异测试：权重 / N/A 逻辑改坏 → 红 ============

def _run_pytest_with_mutation(target, old, new):
    with open(SCORER_PATH, encoding='utf-8') as fh:
        original = fh.read()
    assert old in original, f'变异锚点不存在: {old}'
    with open(SCORER_PATH, 'w', encoding='utf-8') as fh:
        fh.write(original.replace(old, new))
    try:
        r = subprocess.run(
            [PY, '-m', 'pytest', target, '-q'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            cwd=os.path.dirname(SCORER_PATH),
        )
        return r.returncode, r.stdout + r.stderr
    finally:
        with open(SCORER_PATH, 'w', encoding='utf-8') as fh:
            fh.write(original)


def test_mutation_na_logic_removed_caught(tmp_path):
    """变异：无证据幻觉 N/A 逻辑去掉 → 测试必须红。"""
    old = "hallucination = None"
    new = "hallucination = 0  # MUTANT: N/A 逻辑失效"
    rc, out = _run_pytest_with_mutation(
        'tests/test_scorer.py::test_hallucination_na_without_evidence', old, new,
    )
    assert rc != 0, f'N/A 变异未被捕获:\n{out}'


def test_mutation_weight_accuracy_caught(tmp_path):
    """变异：accuracy 权重改坏 → 测试必须红。"""
    old = "'accuracy': 0.4"
    new = "'accuracy': 0.9  # MUTANT: 权重改坏"
    rc, out = _run_pytest_with_mutation(
        'tests/test_scorer.py::test_format_json', old, new,
    )
    assert rc != 0, f'权重变异未被捕获:\n{out}'


def test_mutation_parse_failed_to_zero_caught(tmp_path):
    """变异：parse failed 改成当 0 分 → 测试必须红。"""
    old = "return {'ok': False, 'reason': 'score_parse_failed', 'raw': raw}"
    new = "return {'ok': True, 'score': {'accuracy': 0}}  # MUTANT: 静默当0分"
    rc, out = _run_pytest_with_mutation(
        'tests/test_scorer.py::test_parse_bad_json_fails_not_zero', old, new,
    )
    assert rc != 0, f'静默0分变异未被捕获:\n{out}'
