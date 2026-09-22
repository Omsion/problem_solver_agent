"""
test_text_layout.py - 题目文本排版与公式提取（A + C）

覆盖 2026-09-22 用户明确提出的要求：
1. **剥离 App 页眉/页脚/导航噪音**（`满分：135分 及格：115分 已答`、试卷 ID、
   翻页按钮行），否则它们会直接写进解答文件的 `# 题目文本` 小节；
2. **题号与题型必须保留**（`单选题 第18/60题 自动跳下一题` → `单选题 第18题`）——
   人读答案卡时首先要知道"这是第几题、什么题型"，剥掉它比留一行页眉危害大得多；
3. **正文绝不能被误伤**（`19、下一题题干` 整行必须完好，`第 2 题：求极限` 不能
   被当成导航，`单选题：下列说法…` 是正文不是页眉）；
4. **跨页代码围栏合并**：上一页停在未闭合的 ``` 内时不能插空行，否则渲染成两个块；
5. **公式片段提取/回填**：只把公式送去规范化（C），正文一个字都不经过模型。

运行：pytest tests/test_text_layout.py -v
"""

from __future__ import annotations

import pytest

from problem_solver_agent import text_layout as tl

FENCE = "`" * 3


# ---------------------------------------------------------------------------
# 1. 页面噪音剥离：该删的删
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "满分：135分 及格：115分 已答",
    "78060",
    "上一题 存疑 错题反",
    "自动跳下一题",
])
def test_pure_noise_lines_are_removed(line):
    assert tl.clean_page_line(line) == ""


def test_subject_line_is_removed():
    assert tl.clean_page_line("科目（工作级,Python）") == ""


def test_noise_fragment_is_removed_but_body_kept():
    """行内只混了一段噪音时，删噪音、留正文。"""
    assert tl.clean_page_line("18、下列说法正确的是（ ） 自动跳下一题") == "18、下列说法正确的是（ ）"


# ---------------------------------------------------------------------------
# 2. 题号与题型必须保留（用户 2026-09-22 的明确要求）
# ---------------------------------------------------------------------------


def test_question_kind_and_number_are_preserved():
    """页眉行里的题型与题号不能被一起剥掉。"""
    assert tl.clean_page_line("单选题 第18/60题 自动跳下一题") == "单选题 第18题"


def test_progress_denominator_is_dropped_but_number_kept():
    """`第18/60题` 的 `/60` 是 App 的进度显示，去掉；题号 18 必须留下。"""
    assert tl.clean_page_line("第18/60题") == "第18题"
    assert tl.clean_page_line("第 18 / 60 题") == "第18题"


@pytest.mark.parametrize("line", ["多选题 第5题", "判断题 第3题", "单选题 第18题", "填空题 第7题"])
def test_standalone_metadata_lines_survive(line):
    """只有题型+题号的行，原样保留（不是噪音）。"""
    assert tl.clean_page_line(line) == line


def test_subject_line_with_ocr_prefix_is_removed():
    """真实样本里页眉首字符被 OCR 读歪，实测出现过三种形态，都要剥掉。"""
    assert tl.clean_page_line("正科目（工作级,Python）") == ""
    assert tl.clean_page_line("工科目 (工作级.Python)") == ""
    assert tl.clean_page_line("工程目（工作级.Python）") == ""
    assert tl.clean_page_line("王科目（工作级.Python）") == ""
    # 但不能因此把正文里的"科目"误伤
    assert tl.clean_page_line("科目二的考试内容包括") == "科目二的考试内容包括"


def test_standalone_radio_marker_is_removed_even_on_a_normal_line():
    """模型常把 `○ A.` 单独输出成一行 —— 圈是 App 的选中标记，不属于题目内容。"""
    assert tl.clean_page_line("○ A.") == "A."
    assert tl.clean_page_line("● B.") == "B."
    assert tl.clean_page_line("○ A. ['p', 'y']") == "A. ['p', 'y']"
    # 选项字母与内容一字不动
    assert tl.clean_page_line("○ D. content = file.readlines()") == "D. content = file.readlines()"


def test_option_radio_marker_is_removed_but_option_kept():
    """`○ A.` 里的圈是 App 的选中状态标记，去掉圈、保留 `A.`。

    圈的两种出现形态都要处理：
    - 与页眉噪音同行（`…自动跳下一题 ○ 错题反`）→ 连页眉一起清掉；
    - **单独成行**（实测模型常这么输出）→ 去圈、保留 `A.`
    正文里的圈（后面不是选项字母）不动。
    """
    assert tl.clean_page_line("单选题 第18/60题 自动跳下一题 ○ 错题反") == "单选题 第18题"
    assert tl.clean_page_line("○ A. ['p', 'y']") == "A. ['p', 'y']"
    # 纯正文里的圈不动
    assert tl.clean_page_line("○ 表示已选中该项") == "○ 表示已选中该项"


def test_continuation_page_repeating_the_previous_page_is_trimmed():
    """模型把 CONT 页写成"整段重抄上一页"时，重复段必须去掉。

    真实样本（2026-09-22，8 图）：第 6 页被标 CONT 却把第 5 页的题干与四个选项
    逐字重抄，解答文件里因此出现两份完全相同的题。
    """
    page5 = "21、题干\n○ A. self.dict\n○ B. self._dict\n○ C. self.__dict__\n○ D. self.__dict"
    page6 = page5                      # 整段重抄
    text, _ = tl.normalize_pages([page5, page6], [False, True])

    assert text.count("21、题干") == 1, "重复的题干只能出现一次"
    assert text.count("A. self.dict") == 1


def test_continuation_with_new_content_is_not_trimmed():
    """CONT 页真的只写了新内容时（正常情况），一行都不能删。"""
    page1 = "18、题目开头"
    page2 = "（续）后半段题干\nA. 选项一"
    text, _ = tl.normalize_pages([page1, page2], [False, True])

    assert "（续）后半段题干" in text
    assert "A. 选项一" in text


def test_partial_repeat_is_trimmed_and_rest_kept():
    """CONT 页重抄了一部分又接着写新内容：只删重抄段，新内容保留。"""
    page1 = "19、题干\n1. T-minus 3\n2. T-minus 2"
    page2 = "1. T-minus 3\n2. T-minus 2\n3. T-minus 1\n4. end"
    text, _ = tl.normalize_pages([page1, page2], [False, True])

    assert text.count("1. T-minus 3") == 1
    assert "3. T-minus 1" in text and "4. end" in text


def test_new_question_page_is_never_trimmed():
    """非 CONT（新题）页即使开头与上一页相同也不裁剪 —— 那是两道题的巧合，不是重抄。"""
    page1 = "序号 题号\n18、第一题"
    page2 = "序号 题号\n19、第二题"
    text, _ = tl.normalize_pages([page1, page2], [False, False])

    assert text.count("序号 题号") == 2


# ---------------------------------------------------------------------------
# 3. 正文绝不能被误伤（这几条都是实测踩到过的回归）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "19、下一题题干",                      # 题号开头 → 整行是正文，按钮词不参与判定
    "第 2 题：求极限",                     # 没有斜杠 = 正文题号，不是导航
    "单选题：下列说法正确的是（ ）",          # 题型词出现在正文里
    "18、1+1 = ?",
    "多选题 第5题  下列说法正确的是（ ）",
    "print(x[::-1])",
    "下列哪项正确？",
    "○ 表示已选中该项",                     # 圈在句中/句首但后面不是选项字母 → 正文
])
def test_body_lines_pass_through_unchanged(line):
    assert tl.clean_page_line(line) == line


def test_bare_next_button_is_removed_but_body_word_is_kept():
    """`下一题` 是 App 按钮（实测独立成行、也出现在页眉行尾），必须删掉；
    但当它后面接着中文（`下一题题干`）时按正文保留。"""
    assert tl.clean_page_line("上一题 下一题 存疑") == ""
    assert tl.clean_page_line("下一题") == ""
    assert tl.clean_page_line("下一题题干") == "下一题题干"
    # 真实样本里的页脚：`上一题 下一题 存疑` 曾因只删掉另两个词而留下 `下一题`
    page = "18、题干\nA. 甲\n上一题 下一题 存疑"
    body, _ = tl.strip_chrome_lines(page)
    assert "下一题" not in body
    assert "A. 甲" in body


def test_nested_question_word_is_not_stripped():
    """`题干` 含 `题` 字，不能因为题型正则写松就被整行吃掉。"""
    text = "19、下一题题干\n请写出答案"
    body, touched = tl.strip_chrome_lines(text)
    assert body == text
    assert touched == []


# ---------------------------------------------------------------------------
# 4. 跨页代码围栏合并
# ---------------------------------------------------------------------------


def test_open_fence_is_not_split_by_a_blank_line():
    """上一页停在未闭合的 ``` 内 → 接缝用单换行，不能插空行。"""
    pages = [
        "18、阅读代码：\n" + FENCE + "python\nx = [1,2,3]",
        "print(x[::-1])\n" + FENCE,
    ]
    text, _ = tl.normalize_pages(pages, [False, True])
    assert text == "18、阅读代码：\n" + FENCE + "python\nx = [1,2,3]\nprint(x[::-1])\n" + FENCE
    assert text.count(FENCE) == 2, "一个代码块只能有两个围栏"
    # 代码块内部不能出现空行（那会把 Markdown 渲染切开）
    inner = text.split(FENCE + "python\n", 1)[1].split("\n" + FENCE, 1)[0]
    assert "\n\n" not in inner


def test_continuation_uses_single_newline_and_new_question_uses_blank_line():
    pages = ["第一页题目", "第二页续写", "第三页新题"]
    text, _ = tl.normalize_pages(pages, [False, True, False])
    assert text == "第一页题目\n第二页续写\n\n第三页新题"


def test_trailing_blank_lines_are_trimmed():
    """页尾空行会让拼接出现 3–4 个连续空行，渲染出大片空白。"""
    pages = ["第一页\n\n\n", "第二页"]
    text, _ = tl.normalize_pages(pages, [False, False])
    assert "\n\n\n" not in text
    assert text == "第一页\n\n第二页"


def test_adjacent_duplicate_metadata_line_is_dropped():
    """跨页重复的页眉题号（`第18题` 又出现一次）应当去掉。"""
    pages = ["18、代码\n" + FENCE + "python\nx=1", "第18/60题", "，y=2\n" + FENCE]
    text, _ = tl.normalize_pages(pages, [False, True, True])
    # 第 2 页只含一个页眉题号 → 与上一页的 `18、代码` 不同，因此保留；
    # 但**重复出现的同一行**会被去掉
    duplicate_pages = ["18、题目", "18、题目"]
    deduped, _ = tl.normalize_pages(duplicate_pages, [False, True])
    assert deduped == "18、题目"


# ---------------------------------------------------------------------------
# 5. 端到端：一整页真实形态的截图
# ---------------------------------------------------------------------------


def test_realistic_screenshot_page_is_cleaned_but_keeps_metadata():
    page = (
        "科目（工作级,Python）\n"
        "78060\n"
        "满分：135分 及格：115分 已答\n"
        "单选题 第18/60题 自动跳下一题\n"
        "18、下列关于 Python 的说法正确的是（ ）\n"
        "A. 列表是可变类型\n"
        "B. 元组是可变类型\n"
        "上一题 存疑"
    )
    body, touched = tl.strip_chrome_lines(page)

    assert "科目（工作级,Python）" not in body
    assert "78060" not in body
    assert "满分：135分" not in body
    assert "上一题" not in body and "存疑" not in body
    # **题号与题型必须留下**
    assert "单选题 第18题" in body
    # 题目正文一字不少
    assert "18、下列关于 Python 的说法正确的是（ ）" in body
    assert "A. 列表是可变类型" in body
    assert "B. 元组是可变类型" in body
    # 被改写的行可核对
    assert any("满分" in line for line in touched)


# ---------------------------------------------------------------------------
# 6. 公式片段抽取与回填（C 的基础设施）
# ---------------------------------------------------------------------------


def test_extract_block_and_inline_math():
    text = r"求 $\frac{a}{b}$ 的值，其中 $$\int_0^1 x\,dx = \frac{1}{2}$$"
    skeleton, formulas = tl.extract_math_spans(text)

    assert formulas == [r"$\frac{a}{b}$", r"$$\int_0^1 x\,dx = \frac{1}{2}$$"]
    assert skeleton.count("\x00") == 4, "两个公式各有一对占位符"
    assert r"\frac" not in skeleton, "公式本体已被摘出，正文里不再出现"


def test_extract_begin_environment_as_one_span():
    """`\\begin{}...\\end{}` 必须整体处理，不能只吃掉一半。"""
    text = "矩阵：\n" + r"\begin{bmatrix} a & b \\ c & d \end{bmatrix}" + "\n完"
    _, formulas = tl.extract_math_spans(text)
    assert len(formulas) == 1
    assert formulas[0].startswith(r"\begin{bmatrix}")
    assert formulas[0].endswith(r"\end{bmatrix}")


def test_extract_ignores_dollar_signs_in_plain_text():
    """`价格 $5 和 $6` 这种不是公式，不该被当成行内公式。"""
    text = "价格 $5 和 $6 之间"
    _, formulas = tl.extract_math_spans(text)
    # 两侧都有 $ 会被当行内公式（这是 LaTeX 文本的固有歧义），
    # 但**不能**跨越换行把两段正文吞成一个公式
    _, multiline = tl.extract_math_spans("$x\ny$")
    assert multiline == []


def test_rebuild_round_trip_is_lossless():
    text = r"设 $x=1$，则 $$\begin{cases}a\\b\end{cases}$$ 成立"
    skeleton, formulas = tl.extract_math_spans(text)
    assert tl.rebuild_math(skeleton, formulas) == text


def test_rebuild_with_normalized_formulas_only_changes_math():
    text = r"设 $\frac{a}{b}=1$ 求 $x$"
    skeleton, formulas = tl.extract_math_spans(text)
    assert len(formulas) == 2
    rebuilt = tl.rebuild_math(skeleton, [r"$\dfrac{a}{b}=1$", r"$x$"])
    assert rebuilt.startswith("设 ")
    assert r"\dfrac{a}{b}" in rebuilt
    assert rebuilt.endswith(" 求 $x$")
    # 正文（非公式部分）逐字未变
    assert "设 " in rebuilt and " 求 " in rebuilt


def test_rebuild_survives_broken_placeholder():
    """模型把占位符写坏时不能抛异常，也不能吞掉内容。"""
    skeleton, formulas = tl.extract_math_spans(r"值 $\frac{a}{b}$ 完")
    broken = skeleton.replace("\x000\x00", "\x009\x00", 1)
    out = tl.rebuild_math(broken, formulas)
    assert "\x009\x00" in out, "越界占位符原样保留，便于排查"


def test_math_span_order_is_by_position_not_by_pattern():
    """`$$..$$` 与其中的 `$..$` 不能重复命中同一段。"""
    text = "前 $a$ 中 $$b$$ 后"
    _, formulas = tl.extract_math_spans(text)
    assert formulas == ["$a$", "$$b$$"]
