"""阶段 2 变异测试 —— validator 三层判定逐个改坏，测试必须红。

对应 WorkBuddy 复验意见第 4 条：变异测试，把 validator 的三层判定逐个改坏 →
看测试红不红。

🔴 改坏方式：对 validator 源码做文本变异，跑 test_stage2 里对应的测试，
断言「测试必须失败」。这证明测试真的在守三层校验。
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VALIDATOR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'validator.py',
)
PY = sys.executable


def _run_pytest(target, tmp_path, mutation_old, mutation_new):
    """把 validator.py 的 mutation_old 替换成 mutation_new，跑 target 测试。

    返回 (returncode, output)。替换后恢复原文件。
    """
    with open(VALIDATOR_PATH, encoding='utf-8') as fh:
        original = fh.read()
    assert mutation_old in original, f'变异锚点不存在: {mutation_old}'

    mutated = original.replace(mutation_old, mutation_new)
    with open(VALIDATOR_PATH, 'w', encoding='utf-8') as fh:
        fh.write(mutated)

    try:
        result = subprocess.run(
            [PY, '-m', 'pytest', target, '-q'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            cwd=os.path.dirname(VALIDATOR_PATH),
        )
        return result.returncode, result.stdout + result.stderr
    finally:
        with open(VALIDATOR_PATH, 'w', encoding='utf-8') as fh:
            fh.write(original)


# 变异锚点：validator 三层判定的关键行

def test_mutation_endpoint_not_found_caught(tmp_path):
    """变异：层 1 改成「找不到接口也返回 valid」→ 测试必须红。"""
    old = "return 'invalid', INVALID_LAYER_ENDPOINT, 'endpoint_not_found'"
    new = "pass  # MUTANT: endpoint 校验失效"
    rc, out = _run_pytest(
        'tests/test_stage2.py::test_fake_path_rejected', tmp_path, old, new,
    )
    assert rc != 0, f'变异未被测试捕获:\n{out}'


def test_mutation_field_not_in_contract_caught(tmp_path):
    """变异：层 2 改成「字段不在契约也返回 valid」→ 测试必须红。"""
    old = "return 'invalid', INVALID_LAYER_FIELD, 'field_not_in_contract'"
    new = "pass  # MUTANT: field 校验失效"
    rc, out = _run_pytest(
        'tests/test_stage2.py::test_fake_field_rejected', tmp_path, old, new,
    )
    assert rc != 0, f'变异未被测试捕获:\n{out}'


def test_mutation_assert_tautology_caught(tmp_path):
    """变异：层 3 改成「恒真断言也放行」→ 测试必须红。"""
    old = "return 'invalid', INVALID_LAYER_ASSERT, 'assert_tautology'"
    new = "pass  # MUTANT: tautology 校验失效"
    rc, out = _run_pytest(
        'tests/test_stage2.py::test_fake_assert_tautology_rejected', tmp_path, old, new,
    )
    assert rc != 0, f'变异未被测试捕获:\n{out}'


def test_mutation_assert_unknown_field_caught(tmp_path):
    """变异：层 3 的 assert_unknown_field 判定改成放行 → 测试必须红。"""
    old = "return 'invalid', INVALID_LAYER_ASSERT, 'assert_unknown_field'"
    new = "pass  # MUTANT: unknown field 校验失效"
    rc, out = _run_pytest(
        'tests/test_stage2.py::test_assert_unknown_field_rejected', tmp_path, old, new,
    )
    assert rc != 0, f'变异未被测试捕获:\n{out}'
