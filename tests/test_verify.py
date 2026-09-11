"""
test_verify.py - 核对模式测试

覆盖：JSON 解析容错、verdict 归一化、一致性保护（声称有错却不给理由时降级）、
Markdown 渲染、异常兜底。

运行：pytest tests/test_verify.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from problem_solver_agent import verify


def test_parse_clean_agree():
    result = verify.parse_verification('{"verdict":"agree","issues":[],"corrections":""}', model="m1")
    assert result.verdict == "agree"
    assert result.issues == []
    assert result.has_problems is False
    assert result.model == "m1"


def test_parse_disagree_with_issues():
    raw = '{"verdict":"disagree","issues":["选项 B 与题目要求的\\"不正确\\"矛盾"],"corrections":"应选 A"}'
    result = verify.parse_verification(raw)
    assert result.verdict == "disagree"
    assert result.has_problems is True
    assert len(result.issues) == 1
    assert result.corrections == "应选 A"


def test_parse_handles_fenced_json():
    raw = '```json\n{"verdict": "agree", "issues": []}\n```'
    assert verify.parse_verification(raw).verdict == "agree"


def test_parse_handles_surrounding_prose():
    raw = '核对完成：\n{"verdict": "agree", "issues": []}\n以上。'
    assert verify.parse_verification(raw).verdict == "agree"


def test_unparseable_becomes_unclear():
    result = verify.parse_verification("我觉得应该是对的", model="m")
    assert result.verdict == "unclear"
    assert "无法解析" in result.reason


def test_none_input_becomes_unclear():
    assert verify.parse_verification(None).verdict == "unclear"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"verdict":"ok"}', "agree"),
        ('{"verdict":"PASS"}', "agree"),
        ('{"verdict":"correct"}', "agree"),
        ('{"verdict":"no","issues":["选项错"]}', "disagree"),
        ('{"verdict":"FAIL","issues":["x"]}', "disagree"),
        ('{"verdict":"weird"}', "unclear"),
        ('{}', "unclear"),
    ],
)
def test_verdict_normalisation(raw, expected):
    assert verify.parse_verification(raw).verdict == expected


def test_negative_verdict_without_evidence_is_not_alarming():
    """`no` 这类否定回答若没有任何问题描述，不应直接吓用户，降级为 unclear。"""
    assert verify.parse_verification('{"verdict":"no"}').verdict == "unclear"


def test_disagree_without_evidence_downgrades():
    """声称有错却不给任何问题或修正 → 降级为 unclear，避免误报。"""
    result = verify.parse_verification('{"verdict":"disagree","issues":[],"corrections":""}')
    assert result.verdict == "unclear"
    assert "没有给出具体问题" in result.reason


def test_issues_coercion_variants():
    assert verify.parse_verification('{"verdict":"disagree","issues":"单个问题"}').issues == ["单个问题"]
    assert verify.parse_verification('{"verdict":"disagree","issues":["a","","b"]}').issues == ["a", "b"]


def test_markdown_render_disagree():
    result = verify.VerificationResult(
        verdict="disagree",
        issues=["第 2 小问漏答"],
        corrections="补上：选 C",
        model="GLM-4.6V",
    )
    text = result.as_markdown()
    assert "## 核对结果" in text
    assert "核对发现疑点" in text
    assert "- 第 2 小问漏答" in text
    assert "补上：选 C" in text
    assert "GLM-4.6V" in text


def test_markdown_render_agree():
    text = verify.VerificationResult(verdict="agree", model="m").as_markdown()
    assert "核对通过" in text


def test_append_to_solution(tmp_path):
    solution = tmp_path / "answer.md"
    solution.write_text("# 解答\n\n选 B。\n", encoding="utf-8")

    ok = verify.append_verification_to_solution(solution, verify.VerificationResult(verdict="agree", model="m"))
    assert ok is True

    content = solution.read_text(encoding="utf-8")
    assert content.startswith("# 解答")
    assert "## 核对结果" in content
    assert "选 B。" in content


def test_append_to_missing_solution(tmp_path):
    assert verify.append_verification_to_solution(tmp_path / "nope.md", verify.VerificationResult(verdict="agree")) is False


def test_verify_answer_without_images():
    result = verify.verify_answer([], "答案")
    assert result.verdict == "unclear"
    assert "缺少题目图片" in result.reason


def test_verify_answer_with_empty_answer():
    result = verify.verify_answer([Path("x.jpg")], "   ")
    assert result.verdict == "unclear"
    assert "解答内容为空" in result.reason


def test_verify_answer_truncates_long_answer(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_call(image_paths, prompt, model_name, stream=False, extra_params=None):
        captured["prompt"] = prompt
        captured["images"] = image_paths
        return '{"verdict":"agree","issues":[]}'

    monkeypatch.setattr(verify.vision_client, "_call_vision_api", fake_call)

    image = tmp_path / "p.jpg"
    image.write_bytes(b"x")
    long_answer = "内容" * (verify.MAX_ANSWER_CHARS)  # 远超上限

    result = verify.verify_answer([image], long_answer)

    assert result.verdict == "agree"
    assert "解答过长" in captured["prompt"]
    assert len(captured["prompt"]) < len(long_answer)


def test_verify_answer_handles_client_exception(monkeypatch, tmp_path):
    def broken(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(verify.vision_client, "_call_vision_api", broken)
    image = tmp_path / "p.jpg"
    image.write_bytes(b"x")

    result = verify.verify_answer([image], "答案")
    assert result.verdict == "unclear"
    assert "核对调用失败" in result.reason


def test_verify_answer_handles_non_string_response(monkeypatch, tmp_path):
    monkeypatch.setattr(verify.vision_client, "_call_vision_api", lambda *a, **k: None)
    image = tmp_path / "p.jpg"
    image.write_bytes(b"x")

    result = verify.verify_answer([image], "答案")
    assert result.verdict == "unclear"
    assert "有效响应" in result.reason
