"""
answer_card.py - 从解答文本中抽取"最终答案"

考试场景下用户真正要的是那几行结论，而不是整篇推理过程。现有 Prompt
模板本来就会输出「最终答案」小节（`_FILL_IN_THE_BLANKS`、`_MULTIPLE_CHOICE`
等都有类似约定），这里直接用规则抽取，不额外调用模型（省时省钱）。

抽取失败时回退到"第一段正文"，并在返回值里标记 `extracted=False`，
前端据此提示"未能自动抽取，请查看完整解答"。
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

    text = answer.strip()

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

    # 回退：取第一段非空正文，去掉 YAML frontmatter 与"题目文本"小节
    cleaned = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)
    fallback = ""
    for block in re.split(r"\n\s*\n", cleaned):
        candidate = block.strip()
        if not candidate:
            continue
        if candidate.startswith("#"):
            continue
        fallback = candidate
        break

    if not fallback:
        fallback = cleaned.strip()

    truncated = len(fallback) > max_chars
    return {
        "text": fallback[:max_chars].rstrip() if truncated else fallback,
        "extracted": False,
        "section": None,
        "truncated": truncated,
    }
