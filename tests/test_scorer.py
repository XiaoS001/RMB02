"""评测打分测试（任务单阶段 3 的四维度 + N/A 幻觉规则）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorer import parse_model_score, score_from_model_output, score_output  # noqa: E402


def test_accuracy_with_reference():
    s = score_output('连接池大小建议按 CPU 核数配置',
                     reference='连接池大小建议按 CPU 核数配置')
    assert s['accuracy'] == 100.0


def test_relevance_nonempty():
    s = score_output('这是一个合理长度的回答内容')
    assert s['relevance'] > 50


def test_hallucination_na_when_no_evidence():
    """无证据来源 → hallucination=N/A（None），不计入总分。"""
    s = score_output('随便生成的内容', has_evidence=False)
    assert s['hallucination'] is None
    assert 0 <= s['total'] <= 100


def test_hallucination_with_evidence_valid_citations():
    s = score_output('答案[doc:1]和[doc:2]',
                     has_evidence=True, citations=[1, 2])
    assert s['hallucination'] == 100.0


def test_hallucination_with_evidence_missing_citation():
    s = score_output('答案[doc:9]', has_evidence=True, citations=[1, 2])
    assert s['hallucination'] < 100


def test_hallucination_with_evidence_no_citation():
    """有证据但答案没引用 → 不合格（RMB01 引用校验精神）。"""
    s = score_output('没有引用的答案', has_evidence=True, citations=[1])
    assert s['hallucination'] == 0.0


def test_format_json():
    s = score_output('{"a": 1}', expected_format='json')
    assert s['format'] == 100.0


def test_format_json_invalid():
    s = score_output('not json', expected_format='json')
    assert s['format'] == 0.0


def test_accuracy_no_reference():
    """无参考答案 → accuracy 0（没有参照不能乱给分）。"""
    s = score_output('答案内容', reference=None)
    assert s['accuracy'] == 0.0


def test_accuracy_exact_match():
    s = score_output('参考答案内容', reference='参考答案内容')
    assert s['accuracy'] == 100.0


def test_accuracy_partial_overlap():
    s = score_output('参考答案一部分', reference='参考答案完整内容')
    assert 0 < s['accuracy'] < 100


def test_relevance_empty_output():
    s = score_output('')
    assert s['relevance'] == 0.0


def test_relevance_short_output():
    s = score_output('短')
    assert s['relevance'] == 20.0


def test_relevance_medium_output():
    s = score_output('中等长度回答内容')
    assert s['relevance'] == 60.0


def test_relevance_normal_output():
    s = score_output('这是一个足够长的正常回答内容，表达清晰不含糊，超过二十字')
    assert s['relevance'] == 90.0


def test_total_weighted_with_evidence():
    """有证据：total 是四维加权（含幻觉 0.1）。"""
    s = score_output('答案[doc:1]', has_evidence=True, citations=[1])
    expected = round(
        s['accuracy'] * 0.4 + s['relevance'] * 0.3
        + s['format'] * 0.2 + s['hallucination'] * 0.1, 1,
    )
    assert s['total'] == expected


def test_total_normalized_without_evidence():
    """无证据：total 归一（不含幻觉维）。"""
    s = score_output('正常长度的回答', has_evidence=False)
    expected = round(
        (s['accuracy'] * 0.4 + s['relevance'] * 0.3 + s['format'] * 0.2) / 0.9, 1,
    )
    assert s['total'] == expected


def test_parse_json_with_trailing_text():
    r = parse_model_score('{"accuracy":85,"relevance":90,"format":88,"hallucination":100} 说明完毕')
    assert r['ok'] is True


def test_parse_empty_string_fails():
    r = parse_model_score('')
    assert r['ok'] is False
    assert r['reason'] == 'score_parse_failed'


def test_parse_none_fails():
    r = parse_model_score(None)
    assert r['ok'] is False
    assert r['reason'] == 'score_parse_failed'


def test_parse_hallucination_null_ok():
    """模型显式给 hallucination=null（N/A）→ 解析成功且为 None。"""
    r = parse_model_score('{"accuracy":85,"relevance":90,"format":88,"hallucination":null}')
    assert r['ok'] is True
    assert r['score']['hallucination'] is None


def test_score_from_model_parse_failed_no_quiet_zero():
    """模型分数 parse failed → 返回 ok=false，不带 score（不许当 0 分）。"""
    r = score_from_model_output('坏JSON', '输出', has_evidence=True)
    assert r['ok'] is False
    assert r['reason'] == 'score_parse_failed'
    assert 'score' not in r
