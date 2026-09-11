"""
answer_card.py - 从解答文本中抽取"最终答案"

考试场景下用户真正要的是那几行结论，而不是整篇推理过程。现有 Prompt
模板本来就会输出「最终答案」小节（`_FILL_IN_THE_BLANKS`、`_MULTIPLE_CHOICE`
等都有类似约定），这里直接用规则抽取，不额外调用模型（省时省钱）。

抽取失败时回退到"第一段正文"，并在返回值里标记 `extracted=False`，
前端据此提示"未能自动抽取，请查看完整解答"。

注意：REST 接口给的是**整个解答文件**（YAML frontmatter + 题目文本 + 解答），
而流水线上报给 SSE 的只是模型输出。两条路径都必须得到同一张卡，因此这里先
统一换行符、再剥掉文件前言，然后才做小节匹配——否则卡片里会出现元信息或题面。
"""

from __future__ import annotations

import re

# 按优先级排列的小节标题（Markdown 标题形式）
_SECTION_PATTERNS = [
    r"最终答案",
    r"答案",
    r"结论",
    r"解答",
    r"参考答案",
    r"总结",
]

MAX_CARD_CHARS = 1200

# 小节结束标志：Markdown 标题、粗体小节名、分隔线。
# 模型常用 **解析** 这类粗体行作为下一小节标题，只匹配 # 标题会把它吞进来。
_SECTION_END = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+\S|\*\*\S|[-*_]{3,})[^\n]*$", re.MULTILINE)

# --- 解答文件前言（不是解答内容，但 REST 接口返回的是整个文件）---
# 1) YAML frontmatter：problem_type / solver / created / images…
#    用"键: 值"做前置断言，避免把正文里成对出现的 `---` 当成元信息删掉
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?=[\s\S]*?^(?:problem_type|solver|aux_model|status|created|images):)"
    r"[\s\S]*?\n---[ \t]*\n",
    re.DOTALL | re.MULTILINE,
)
# 2) 「题目文本」小节：从标题到下一个分隔线（或文件末尾）
_PROBLEM_SECTION_RE = re.compile(
    r"^[ \t]*#{1,6}[ \t]*题目文本[^\n]*\n.*?(?=^[ \t]*-{3,}[ \t]*$|\Z)",
    re.DOTALL | re.MULTILINE,
)
# 3) 包住整篇解答的「# 解答」标题
_ANSWER_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*解答[ \t]*$", re.MULTILINE)
# 4) 前言与正文之间的分隔线
_LEADING_RULE_RE = re.compile(r"\A(?:[ \t]*-{3,}[ \t]*\n+)+")
# 5) 文件可能带 BOM / 前导空行，先削掉再匹配上面的锚点
_BOM_RE = re.compile(r"\A\uFEFF")


def _normalize_newlines(text: str) -> str:
    """统一换行符。

    流水线用文本模式写解答文件，Windows 上换行是 `\\r\\n`，而下面所有小节正则
    都用 `$` 锚定行尾——不归一化时它们全部失配，答案卡会静默退化成题面/元信息。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_preamble(text: str) -> str:
    """去掉解答文件的前言，只留下模型真正写出的解答正文。

    优先按 `# 解答` 标题切：题面里完全可能出现独立的 `---` 行（模型抄的输入输出
    格式里就有），按"下一个分隔线"切会切不干净，让题面里的「最终答案」字样赢得
    答案卡。旧文件没有该标题时才退回按 frontmatter + 题目文本小节 + 分隔线剥离。
    """
    cleaned = _BOM_RE.sub("", text).lstrip("\n")

    answer_heading = _ANSWER_HEADING_RE.search(cleaned)
    if answer_heading:
        return cleaned[answer_heading.end():].lstrip("\n").strip()

    # 旧文件：按 frontmatter + 题目文本小节剥离；只有真的剥掉了前言，才去掉分隔线
    stripped = False
    without_frontmatter = _FRONTMATTER_RE.sub("", cleaned, count=1)
    if without_frontmatter != cleaned:
        stripped = True
    cleaned = without_frontmatter

    without_problem_text = _PROBLEM_SECTION_RE.sub("", cleaned, count=1)
    if without_problem_text != cleaned:
        stripped = True
    cleaned = without_problem_text

    if stripped:
        cleaned = _LEADING_RULE_RE.sub("", cleaned.lstrip("\n"), count=1)
    return cleaned.strip()


def _heading_regex(title: str) -> re.Pattern[str]:
    # 匹配 "### 最终答案" / "**最终答案**" / "最终答案：" 等形式
    # 注意 `\*{0,2}`：模型常用 **粗体** 作为小节标题，单个 \* 匹配不到两个星号
    return re.compile(
        rf"^[ \t]*(?:#{{1,6}}[ \t]*)?\*{{0,2}}[ \t]*{title}[ \t]*\*{{0,2}}[ \t]*[:：]?[ \t]*$",
        re.MULTILINE,
    )


def extract_answer_card(answer: str, max_chars: int = MAX_CARD_CHARS) -> dict:
    """从完整解答中抽取答案卡。

    Returns:
        {
          "text": str,             # 抽取出的答案正文
          "extracted": bool,       # 是否命中了明确的"最终答案"小节
          "section": str | None,   # 命中的小节标题
          "truncated": bool,       # 是否因过长被截断
        }
    """
    if not answer or not answer.strip():
        return {"text": "", "extracted": False, "section": None, "truncated": False}

    text = _strip_preamble(_normalize_newlines(answer))

    for title in _SECTION_PATTERNS:
        pattern = _heading_regex(title)
        match = pattern.search(text)
        if not match:
            continue
        body = text[match.end():].lstrip("\n")
        if not body.strip():
            continue
        # 到下一个小节标志为止
        next_section = _SECTION_END.search(body)
        if next_section:
            body = body[: next_section.start()]
        body = body.strip()
        if not body:
            continue
        truncated = len(body) > max_chars
        return {
            "text": body[:max_chars].rstrip() if truncated else body,
            "extracted": True,
            "section": title,
            "truncated": truncated,
        }

    # 回退：取第一段非空正文（标题行不是正文，跳过）
    fallback = ""
    for block in re.split(r"\n\s*\n", text):
        candidate = block.strip()
        if not candidate:
            continue
        if candidate.startswith("#"):
            continue
        fallback = candidate
        break

    if not fallback:
        fallback = text.strip()

    truncated = len(fallback) > max_chars
    return {
        "text": fallback[:max_chars].rstrip() if truncated else fallback,
        "extracted": False,
        "section": None,
        "truncated": truncated,
    }
