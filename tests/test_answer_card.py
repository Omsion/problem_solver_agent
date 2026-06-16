"""
test_answer_card.py - 答案卡抽取测试

运行：pytest tests/test_answer_card.py -v
"""

from __future__ import annotations

from problem_solver_agent.answer_card import extract_answer_card


def test_extracts_final_answer_section():
    answer = """# 解答

## 题目分析
这是一道关于存储器的题目。

## 最终答案
程序存储器（ROM）与数据存储器（RAM）。

## 知识点解析
补充说明。
"""
    card = extract_answer_card(answer)
    assert card["extracted"] is True
    assert card["section"] == "最终答案"
    assert "程序存储器" in card["text"]
    # 不应把下一节内容带进来
    assert "知识点解析" not in card["text"]


def test_extracts_bold_heading():
    answer = "**最终答案**\n\n选 B。\n\n**解析**\n因为……"
    card = extract_answer_card(answer)
    assert card["extracted"] is True
    assert card["text"].strip() == "选 B。"


def test_bold_heading_stops_at_bold_subsection():
    """模型常用 **解析** 这类粗体行作为下一节，不能把它吞进答案卡。"""
    answer = "**最终答案**\n\n答案：程序存储器与数据存储器。\n\n**知识点解析**\n这是补充说明。"
    card = extract_answer_card(answer)
    assert card["text"].strip() == "答案：程序存储器与数据存储器。"
    assert "补充说明" not in card["text"]


def test_section_stops_at_horizontal_rule():
    answer = "## 最终答案\n选 B。\n\n---\n\n## 参考资料\n其他内容"
    card = extract_answer_card(answer)
    assert card["text"].strip() == "选 B。"


def test_heading_with_colon_and_hash():
    answer = "### 最终答案：\n\n答案是 42。\n"
    card = extract_answer_card(answer)
    assert card["extracted"] is True
    assert card["text"] == "答案是 42。"


def test_fallback_to_first_paragraph_without_heading():
    answer = "选 B。因为根据题意……\n\n更多解释。"
    card = extract_answer_card(answer)
    assert card["extracted"] is False
    assert card["section"] is None
    assert card["text"].startswith("选 B。")


def test_fallback_skips_frontmatter_and_headings():
    answer = """---
problem_type: MULTIPLE_CHOICE
solver: deepseek
images:
  - a.jpg
---

# 题目文本

题目内容……

---

# 解答

选 C。
"""
    card = extract_answer_card(answer)
    assert card["text"].startswith("选 C")


def test_truncation_flag():
    answer = "## 最终答案\n\n" + ("很长的内容。" * 500)
    card = extract_answer_card(answer, max_chars=50)
    assert card["truncated"] is True
    assert len(card["text"]) <= 50


def test_empty_input():
    card = extract_answer_card("")
    assert card["text"] == ""
    assert card["extracted"] is False


def test_whitespace_only_section_falls_through():
    answer = "## 最终答案\n\n\n## 其他\n内容"
    card = extract_answer_card(answer)
    # "最终答案"小节为空，应继续尝试其他小节或回退
    assert card["text"] != ""


def test_priority_prefers_final_answer_over_answer():
    answer = "## 答案\n简略版\n\n## 最终答案\n详细版"
    card = extract_answer_card(answer)
    assert card["section"] == "最终答案"
    assert "详细版" in card["text"]
