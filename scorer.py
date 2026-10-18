"""评测打分 —— 四维度（任务单阶段 3）。

维度：准确性 / 相关性 / 格式合规 / 幻觉检测
🔴 幻觉检测只适用于「有证据来源」的输出；无证据 → N/A，不计入总分。
每维 0~100，加权总分（权重写进 docs/评测维度定义.md）。

🔴 打分格式漂移兜底（复验意见风险 1）：模型输出结构化分数时可能
返回坏 JSON（markdown 代码块 / 中文前缀 / "85分" / 缺维度）。
必须解析失败 → {"ok": false, "reason": "score_parse_failed", "raw": "..."}，
**不许静默当 0 分**（0 分会污染统计）。
"""
import json
import re

# 权重（写进 docs/评测维度定义.md，说清为什么）
WEIGHTS = {
    'accuracy': 0.4,
    'relevance': 0.3,
    'format': 0.2,
    'hallucination': 0.1,
}

# 幻觉维度无证据时的占位值（N/A）
HALLUCINATION_NA = None


def parse_model_score(raw):
    """解析模型输出的结构化分数（带格式漂移兜底）。

    返回：
      - 成功: {"ok": True, "score": {...}}
      - 失败: {"ok": False, "reason": "score_parse_failed", "raw": raw}
        （🔴 绝不静默当 0 分）
    """
    if raw is None:
        return {'ok': False, 'reason': 'score_parse_failed', 'raw': None}

    text = str(raw).strip()
    # 剥 markdown 代码块：```json ... ``` 或 ``` ... ```
    fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # 剥中文前缀：取第一个 { 之后的内容
    if '{' in text:
        text = text[text.index('{'):]
    # 剥中文后缀：最后一个 } 之前的内容
    if '}' in text:
        text = text[:text.rindex('}') + 1]

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {'ok': False, 'reason': 'score_parse_failed', 'raw': raw}

    if not isinstance(obj, dict):
        return {'ok': False, 'reason': 'score_parse_failed', 'raw': raw}

    # 缺维度 / 类型错 → parse failed（不许用默认值填充后当 0 分）
    required = {'accuracy', 'relevance', 'format', 'hallucination'}
    missing = required - set(obj.keys())
    if missing:
        return {'ok': False, 'reason': 'score_parse_failed', 'raw': raw}

    clean = {}
    for key in required:
        val = obj[key]
        # 🔴 hallucination 允许 null（无证据时模型标 N/A）—— 不判 parse failed
        if key == 'hallucination' and val is None:
            clean[key] = None
            continue
        # "85分" / "85" → 数字；数字直接过
        if isinstance(val, str):
            m = re.search(r'(\d+(?:\.\d+)?)', val)
            if not m:
                return {'ok': False, 'reason': 'score_parse_failed', 'raw': raw}
            val = float(m.group(1))
        if not isinstance(val, (int, float)) or not (0 <= val <= 100):
            return {'ok': False, 'reason': 'score_parse_failed', 'raw': raw}
        clean[key] = round(float(val), 1)

    return {'ok': True, 'score': clean}


def score_output(output_text, *, reference=None, has_evidence=False, citations=None, expected_format=None):
    """对一条模型输出打分。返回 dict(accuracy, relevance, format, hallucination, total)。

    - hallucination: has_evidence=False → None（N/A 不计入总分）
    - citations: 答案里的 [N] 引用（有证据时校验）
    """
    accuracy = _score_accuracy(output_text, reference)
    relevance = _score_relevance(output_text)
    format_score = _score_format(output_text, expected_format)

    if has_evidence:
        hallucination = _score_hallucination(output_text, citations)
        total = (
            accuracy * WEIGHTS['accuracy']
            + relevance * WEIGHTS['relevance']
            + format_score * WEIGHTS['format']
            + hallucination * WEIGHTS['hallucination']
        )
    else:
        hallucination = None
        # 无证据：幻觉 N/A，总分从其余三维归一
        total = (
            accuracy * WEIGHTS['accuracy']
            + relevance * WEIGHTS['relevance']
            + format_score * WEIGHTS['format']
        ) / (WEIGHTS['accuracy'] + WEIGHTS['relevance'] + WEIGHTS['format'])

    return {
        'accuracy': round(accuracy, 1),
        'relevance': round(relevance, 1),
        'format': round(format_score, 1),
        'hallucination': hallucination,
        'total': round(total, 1),
    }


def score_from_model_output(raw_score, output_text, *, has_evidence=False, citations=None):
    """模型输出分数 + 本地兜底打分融合。

    模型分数解析成功 → 用模型的（但 hallucination 无证据仍强置 N/A）；
    解析失败 → 返回 {"ok": false, "reason": "score_parse_failed", "raw": raw_score}，
    **不计入统计**（调用方决定是否降级为本地打分，但本地打分不能冒充模型分）。
    """
    parsed = parse_model_score(raw_score)
    if not parsed['ok']:
        return parsed

    score = parsed['score']
    if not has_evidence:
        score['hallucination'] = None
        # 无证据：重新归一总分（不含幻觉维）
        total = (
            score['accuracy'] * WEIGHTS['accuracy']
            + score['relevance'] * WEIGHTS['relevance']
            + score['format'] * WEIGHTS['format']
        ) / (WEIGHTS['accuracy'] + WEIGHTS['relevance'] + WEIGHTS['format'])
        score['total'] = round(total, 1)
    else:
        score['total'] = round(
            score['accuracy'] * WEIGHTS['accuracy']
            + score['relevance'] * WEIGHTS['relevance']
            + score['format'] * WEIGHTS['format']
            + (score['hallucination'] or 0) * WEIGHTS['hallucination'],
            1,
        )
    return {'ok': True, 'score': score}


def _score_accuracy(output, reference):
    if not reference:
        return 0.0
    # 简单相似度：参考文本里的关键片段出现在输出里给分
    ref = reference.strip()
    out = output.strip()
    if not out:
        return 0.0
    if ref in out or out in ref:
        return 100.0
    # 重叠字比例
    ref_chars = set(ref)
    out_chars = set(out)
    if not ref_chars:
        return 0.0
    overlap = len(ref_chars & out_chars) / len(ref_chars)
    return round(overlap * 100, 1)


def _score_relevance(output):
    if not output.strip():
        return 0.0
    # 相关性的简易信号：输出长度适中（非空、非废话）
    length = len(output.strip())
    if length < 5:
        return 20.0
    if length < 20:
        return 60.0
    return 90.0


def _score_format(output, expected_format=None):
    if expected_format is None:
        # 默认要求：非空
        return 100.0 if output.strip() else 0.0
    if expected_format == 'json':
        try:
            json.loads(output)
            return 100.0
        except json.JSONDecodeError:
            return 0.0
    if expected_format == 'list':
        return 100.0 if output.strip().startswith('-') or output.strip().startswith('[') else 50.0
    return 100.0


def _score_hallucination(output, citations):
    """有证据时：答案里的 [N] 必须都在证据里，否则扣分。"""
    if citations is None or not citations:
        # 有证据但没引用 → 算不合格（照 RMB01 引用校验精神）
        return 0.0
    cited = set(re.findall(r'\[doc:(\d+)\]', output)) or set(re.findall(r'\[(\d+)\]', output))
    if not cited:
        return 0.0
    ok = cited.issubset(set(str(c) for c in citations))
    return 100.0 if ok else 30.0
