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


# ---------------------------------------------------------------------------
# 整个解答文件（REST 返回的形态）：不能把元信息/题面当成答案
# ---------------------------------------------------------------------------

FULL_FILE = """\
---
problem_type: ACM
solver: deepseek (deepseek-flash)
aux_model: deepseek (deepseek-flash)
style: OPTIMAL
created: 2026-09-12 03:50:58
images:
  - 22-1.jpg
---

# 题目文本

# 22、商品购买预测

## 题目描述
实现一个二分类逻辑回归模型，预测用户是否购买。答案要求保留四位小数。

---

# 解答

## 最终答案
选 C。购买概率 0.8123。
"""


def test_full_file_ignores_frontmatter_and_problem_text():
    card = extract_answer_card(FULL_FILE)
    assert card["section"] == "最终答案"
    assert card["text"].startswith("选 C")
    # 元信息与题面都不能进卡片
    assert "problem_type" not in card["text"]
    assert "题目描述" not in card["text"]
    assert "保留四位小数" not in card["text"]


def test_full_file_without_final_answer_section_skips_preamble():
    """没有「最终答案」小节时，回退内容也必须是解答，而不是题面。"""
    file_text = FULL_FILE.replace("## 最终答案\n选 C。购买概率 0.8123。\n", "**核心任务**：写出梯度下降实现。\n")
    card = extract_answer_card(file_text)
    assert card["text"].startswith("**核心任务**")
    assert "题目描述" not in card["text"]


def test_crlf_line_endings_are_handled():
    """解答文件在 Windows 上是 CRLF 写的，小节正则必须照样命中。"""
    card = extract_answer_card(FULL_FILE.replace("\n", "\r\n"))
    assert card["section"] == "最终答案"
    assert "选 C" in card["text"]


def test_bom_and_leading_blank_lines_are_tolerated():
    """带 BOM 或前导空行时，元信息同样不能进卡片（否则又变回"显示 problem_type"）。"""
    for prefix in ("\ufeff", "\n\n", "\ufeff\n"):
        card = extract_answer_card(prefix + FULL_FILE)
        assert card["section"] == "最终答案", prefix
        assert card["text"].startswith("选 C"), prefix
        assert "problem_type" not in card["text"], prefix


def test_problem_text_containing_rule_does_not_win_the_card():
    """题面里出现独立的 `---` 行时，不能把题面当成解答。

    模型抄录的"输入输出格式"里常带 `---`；按"下一个分隔线"切题面会切不干净，
    结果题面里的「最终答案」字样会被当成真答案。
    """
    file_text = (
        "---\n"
        "problem_type: ACM\n"
        "solver: deepseek (deepseek-flash)\n"
        "---\n\n"
        "# 题目文本\n\n"
        "第一段题面\n"
        "---\n"
        "## 最终答案\n"
        "这是题面里对输出格式的说明\n"
        "---\n\n"
        "# 解答\n\n"
        "## 最终答案\n"
        "真正的答案：选 C。\n"
    )
    card = extract_answer_card(file_text)
    assert card["text"].startswith("真正的答案")
    assert "题面里对输出格式的说明" not in card["text"]


def test_empty_extraction_is_reported_as_empty():
    """抽不出正文时返回空文本，由调用方决定怎么兜底（不能显示一张空卡片）。"""
    card = extract_answer_card("---\nproblem_type: ACM\n---\n\n# 解答\n")
    assert card["text"] == ""
    assert card["extracted"] is False
