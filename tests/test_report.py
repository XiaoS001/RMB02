"""评测报告测试（阶段 3）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import build_report, top_failures  # noqa: E402

SCORES = [
    {'model_output_id': i, 'accuracy': 90, 'relevance': 85, 'format': 80,
     'hallucination': 100, 'total': 87.5, 'run_index': 0}
    for i in range(5)
]
# 一条 N/A 幻觉
SCORES_NA = [
    {'model_output_id': 9, 'accuracy': 70, 'relevance': 75, 'format': 80,
     'hallucination': None, 'total': 74.4, 'run_index': 1},
]


def test_report_summary():
    rep = build_report(SCORES, n=5)
    assert rep['n'] == 5
    assert rep['total']['mean'] == 87.5
    assert rep['total']['min'] == 87.5
    assert rep['total']['max'] == 87.5
    assert rep['total']['range'] == 0.0
    assert len(rep['per_sample']) == 5


def test_report_dimension_summary():
    rep = build_report(SCORES, n=5)
    assert rep['dimensions']['accuracy']['mean'] == 90.0
    assert rep['dimensions']['hallucination']['n'] == 5


def test_report_hallucination_na_excluded():
    """N/A 的 hallucination 不计入维度统计。"""
    rep = build_report(SCORES_NA, n=1)
    assert rep['dimensions']['hallucination']['n'] == 0
    assert rep['dimensions']['hallucination']['mean'] is None


def test_top_failures_returns_lowest():
    scores = [
        {'model_output_id': i, 'accuracy': 90, 'relevance': 85, 'format': 80,
         'hallucination': 100, 'total': float(i), 'run_index': 0}
        for i in range(10)
    ]
    rep = build_report(scores, n=10)
    fails = top_failures(rep, k=3)
    assert [s['total'] for s in fails] == [0.0, 1.0, 2.0]


def test_report_n_is_explicit():
    """报告必带 n。"""
    rep = build_report(SCORES, n=5)
    assert 'n' in rep
    assert rep['n'] == 5
