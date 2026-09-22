"""
test_formula_normalize.py - 公式规范化（C：只把公式送去模型）

背景（2026-09-22 用户要求）：
- A（本地排版）解决页眉/空行/代码围栏，但**公式是否被 `$...$` 正确包裹**它不做；
- C 的做法是把公式片段抽出来单独送一次模型（输出几十 token，而不是像润色那样
  重写整篇约 10K），再填回原位。**正文一个字符都不经过模型**。

本文件锁定的契约：
1. 有公式才调、没公式不调（省一次往返）；
2. 片段总量过小不调（收益 < 一次网络往返）；
3. 返回值数量与输入不一致 → **保留原公式**（错位填回会把公式安到别的题上，比不规范化危险）；
4. 调用异常 → 保留原公式，且**绝不让解题失败**；
5. 只有公式位置发生变化，正文逐字不变；
6. 关掉开关（`FORMULA_NORMALIZE=false`）时一次模型都不调。

运行：pytest tests/test_formula_normalize.py -v
"""

from __future__ import annotations

import json

import pytest

from problem_solver_agent import config, core_pipeline, prompts
from problem_solver_agent.cancel import CancelToken


@pytest.fixture()
def pipeline(tmp_path):
    return core_pipeline.SolutionPipeline(
        solution_dir=tmp_path / "solutions",
        write_failure_log=False,
    )


@pytest.fixture()
def formula_calls(monkeypatch):
    """打桩辅助调用，把 prompt 与返回内容记下来。

    只拦"公式规范化"这一类调用（按 prompt 前缀识别）。若被测代码绕过它去调润色，
    这里会返回一个普通字符串，使公式解析失败 —— 从而暴露"调错了阶段"。
    """
    calls: list[dict] = []

    def fake_ask(prompt, provider, model):
        is_formula = prompt.startswith(prompts.FORMULA_NORMALIZE_PROMPT.split("{formulas_json}")[0])
        calls.append({"is_formula": is_formula, "prompt": prompt})
        if not is_formula:
            return "润色结果"
        return json.dumps(_next_response["value"], ensure_ascii=False)

    monkeypatch.setattr(core_pipeline.solver_client, "ask_for_analysis", fake_ask)
    _next_response["value"] = []
    return calls


_next_response: dict = {"value": []}


def _cancel() -> CancelToken:
    return CancelToken()


# ---------------------------------------------------------------------------
# 1. 什么时候调、什么时候不调
# ---------------------------------------------------------------------------


def test_no_formula_means_no_call(pipeline, formula_calls):
    text = "这是一道纯文字题，没有任何公式。"
    out, elapsed = pipeline._normalize_formulas(text, _cancel())  # noqa: SLF001

    assert out == text
    assert elapsed == 0
    assert formula_calls == [], "没有公式就不该发起调用"


def test_tiny_formula_is_skipped(pipeline, formula_calls):
    """单个 `$x$` 不值得一次网络往返。"""
    out, elapsed = pipeline._normalize_formulas("设 $x$ 为未知数", _cancel())  # noqa: SLF001

    assert out == "设 $x$ 为未知数"
    assert elapsed == 0
    assert formula_calls == []


def test_disabled_switch_makes_no_call(pipeline, formula_calls, monkeypatch):
    monkeypatch.setattr(config, "FORMULA_NORMALIZE", False)
    text = r"求 $\frac{a}{b}$ 与 $$\int_0^1 x\,dx = \frac{1}{2}$$ 的值"
    out, elapsed = pipeline._normalize_formulas(text, _cancel())  # noqa: SLF001

    assert out == text
    assert elapsed == 0
    assert formula_calls == []


# ---------------------------------------------------------------------------
# 2. 正常路径：只有公式变，正文一字不动
# ---------------------------------------------------------------------------


def test_formulas_are_normalized_and_body_is_untouched(pipeline, formula_calls):
    original = (
        r"设 $\begin{cases} a & b \\ c & d \end{cases}$ 与 $\frac{a}{b}=1$，"
        r"求 $\theta$ 的值。已知条件如下，请计算。"
    )
    _next_response["value"] = [
        r"$$\begin{cases} a & b \\ c & d \end{cases}$$",
        r"$\dfrac{a}{b}=1$",
        r"$\theta$",
    ]

    out, _ = pipeline._normalize_formulas(original, _cancel())  # noqa: SLF001

    assert len(formula_calls) == 1 and formula_calls[0]["is_formula"]
    # 公式被替换
    assert r"\dfrac{a}{b}" in out
    assert r"$$\begin{cases}" in out
    # 正文（非公式部分）逐字保留
    assert out.startswith("设 ")
    assert out.endswith(" 的值。已知条件如下，请计算。")
    assert "，求 " in out
    assert " 与 " in out


def test_prompt_carries_all_formulas_as_json(pipeline, formula_calls):
    original = (
        r"甲 $\begin{cases} a & b \\ c & d \end{cases}$ 乙 $\frac{3}{4}$ "
        r"丙 $\sqrt{5}$ 丁 $\theta$ 说明文字"
    )
    _next_response["value"] = [
        r"$\begin{cases} a & b \\ c & d \end{cases}$",
        r"$\frac{3}{4}$",
        r"$\sqrt{5}$",
        r"$\theta$",
    ]

    pipeline._normalize_formulas(original, _cancel())  # noqa: SLF001

    prompt = formula_calls[0]["prompt"]
    # prompt 里是 JSON 编码后的形式：反斜杠被转义成 `\\`，且**四条公式都在**。
    # 这里只断言"编码形态 + 条目齐全"，不锁死转义细节（那是 json.dumps 的实现）。
    assert '"$\\\\begin{cases} a & b \\\\\\\\ c & d \\\\end{cases}$"' in prompt, \
        "公式应以 JSON 数组形式给出"
    assert '"$\\\\frac{3}{4}$"' in prompt
    assert '"$\\\\sqrt{5}$"' in prompt
    assert '"$\\\\theta$"' in prompt


def test_usage_event_is_emitted_for_the_formula_stage(pipeline, formula_calls):
    """这次调用是真金白银的，必须在用量里可见（与 T4 同样的理由）。"""
    events: list[dict] = []
    pipeline.on_event = events.append
    original = (
        r"求 $\begin{cases} a & b \\ c & d \end{cases}$ 与 $\frac{a}{b}$ 的解，"
        r"并写出完整推理过程与结论说明"
    )
    _next_response["value"] = [r"$$\begin{cases} a & b \\ c & d \end{cases}$$", r"$\dfrac{a}{b}$"]

    pipeline._normalize_formulas(original, _cancel())  # noqa: SLF001

    usage = [event for event in events if event.get("type") == "usage"]
    assert [event["stage"] for event in usage] == ["formula"]
    assert usage[0]["images"] == 0, "纯文本调用不该带图片 token"


# ---------------------------------------------------------------------------
# 3. 失败路径：一律保留原公式（排版降级可以接受，丢内容不可以）
# ---------------------------------------------------------------------------


def test_count_mismatch_keeps_original(pipeline, formula_calls):
    """数量不一致意味着"哪条对应哪条"不可知 —— 硬填会把公式安到别的题上。"""
    original = r"甲：$\frac{1}{2}$ 乙：$\frac{3}{4}$ 丙：$\sqrt{5}$ 丁：$\theta$ 补充说明文字"
    _next_response["value"] = [r"$\frac{1}{2}$"]      # 少给了两条

    out, _ = pipeline._normalize_formulas(original, _cancel())  # noqa: SLF001

    assert out == original


def test_non_json_response_keeps_original(pipeline, formula_calls, monkeypatch):
    original = r"求 $\frac{a}{b}$ 与 $\theta$ 与 $\sqrt{5}$ 的值，附带一段足够长的说明文字"
    monkeypatch.setattr(
        core_pipeline.solver_client, "ask_for_analysis", lambda *a, **k: "抱歉，我无法处理。"
    )

    out, _ = pipeline._normalize_formulas(original, _cancel())  # noqa: SLF001

    assert out == original


def test_call_exception_keeps_original_and_does_not_raise(pipeline, monkeypatch):
    """模型调用异常绝不能让解题失败。"""
    original = r"求 $\frac{a}{b}$ 与 $\theta$ 与 $\sqrt{5}$ 的值，附带一段足够长的说明文字"

    def boom(*args, **kwargs):
        raise RuntimeError("网关 502")

    monkeypatch.setattr(core_pipeline.solver_client, "ask_for_analysis", boom)

    out, _ = pipeline._normalize_formulas(original, _cancel())  # noqa: SLF001

    assert out == original


def test_empty_item_falls_back_to_original_formula(pipeline, formula_calls):
    """模型某条返回空串时保留那一条的原样，而不是把公式删掉。"""
    original = r"甲 $\frac{1}{2}$ 乙 $\frac{3}{4}$ 丙 $\sqrt{5}$ 丁 $\theta$ 说明文字够长了"
    _next_response["value"] = [r"$\frac{1}{2}$", "", r"$\sqrt{5}$", r"$\theta$"]

    out, _ = pipeline._normalize_formulas(original, _cancel())  # noqa: SLF001

    assert r"\frac{3}{4}" in out, "空返回必须回退成原公式，不能丢"


def test_parser_accepts_fenced_json_and_rejects_wrong_length():
    """解析器容忍代码围栏与客套话，但**数量必须严格相等**。"""
    assert core_pipeline._parse_formula_array('```json\n["$a$"]\n```', expected=1) == ["$a$"]
    assert core_pipeline._parse_formula_array('好的：["$a$", "$b$"]', expected=2) == ["$a$", "$b$"]
    assert core_pipeline._parse_formula_array('["$a$"]', expected=2) is None
    assert core_pipeline._parse_formula_array("不是数组", expected=1) is None
    assert core_pipeline._parse_formula_array(None, expected=1) is None
    assert core_pipeline._parse_formula_array('[1, 2]', expected=2) is None, "元素必须是字符串"
