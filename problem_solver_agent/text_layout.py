"""
text_layout.py - 逐页 OCR 文本的本地排版规范化

## 为什么需要它

合并调用的 prompt 要求模型**逐页转录**，所以每页正文天然带有"那一页截图上的东西"：
考试 App 的页眉页脚（`科目（工作级,Python）`、`满分：135 分 及格：115 分 已答`）、
题号导航（`单选题 第18/60题 自动跳下一题`）。这些直接拼进解答文件的 `# 题目文本`
小节后，人读起来是噪音，而**模型并不总是照做**（实测：同一批图里两家模型在不同页
各自省略或保留页眉，方向还不一致）。

`VISION_INLINE_MERGE=true`（默认）下，多图任务走本地拼接、**不调用润色模型**
（理由见 `core_pipeline._textualize`：润色要重写整篇 10K token，且它的"找最长重叠"
指令在多道独立题场景会静默删掉一道题的开头）。代价就是**没有任何排版处理** ——
本模块补上这一环，且**零 token 成本、完全确定性**。

## 设计原则：宁可留噪音，绝不吃正文

页眉剥离是**有损**操作，而"少剥一行"的代价（多一行噪音）远小于"多剥一行"的代价
（丢掉题干）。因此这里的模式**全部要求强信号**：

- 只认"考试 App 特有的组合词"（`满分`+`及格`、`第 N/M 题` 带斜杠的导航格式…），
  不认单个宽泛词；
- `第 2 题`（正文里的题号）**必须完好**，只有 `第18/60题` 这种"当前题/总题数"
  导航格式才算噪音 —— 有专门用例锁住这一条；
- 剥离的行会原样返回（`kept_chrome`），便于日志与测试核对，不做隐藏。

## 与 `tools/vision_ab.py` 的关系

迁移验收时这套规则先写在 `tools/vision_ab.py` 里（那时只用于 A/B 判据）。
现在上移到生产路径，工具改为引用本模块 —— **两边必须用同一套规则**，
否则"验收时判 0 缺失"与"生产时剥了什么"会各说各话。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 页眉 / 页脚 / 题号导航
#
# **最重要的设计约束：剥离噪音，保留题目元信息。**
#
# 页眉行往往是混在一起的，例如考试 App 的一整行可能是：
#
#     单选题 第18/60题 自动跳下一题
#     ├ 题型   ├ 进度（噪音）  └ 按钮（噪音）
#
# 只做"整行删除"会把 `单选题` 与题号一起吃掉 —— 比留一行页眉危害大得多：
# 人读答案卡时首先要知道"这是第几题、什么题型"，而题型分类结果虽然也写进了
# frontmatter，正文里仍然需要（多选题的选项与单选的含义不同）。
#
# 因此这里的处理是**逐行重建**：从行里挑出"题目元信息"片段保留，其余丢掉。
# 拆不干净的行（例如纯按钮行）才整行删除。
# ---------------------------------------------------------------------------

# 题型标识：**必须整词**（允许 多/单/不定 前缀）。
# 早期版本写成 `单项?选择|多项?选择`，结果 `多选题` 被 `?` 吃掉"项"后匹配到 `选题` ——
# 于是正文行被当成"只含题型元信息"而整行删掉。宁可漏认几种说法，也不能误认。
_QUESTION_KIND_RE = re.compile(
    r"(?:不?[单多]项?\s*选择|判断|填空|简答|论述|编程|算法)(?:\s*题)?"
)

# 题号：`第18题`、`第 18 题`，刻意**允许**带 `/60` 这种进度分母 —— 提取时只取
# 斜杠前的当前题号。这与"整行判噪音"不同：那只认带斜杠的导航格式。
_QUESTION_NUMBER_RE = re.compile(r"第\s*(\d+)\s*(?:/\s*\d+\s*)?题")

# 行内噪音片段：一律直接删掉，不需要保留任何部分。
_NOISE_RE = re.compile(r"自动跳下一题|自动跳转下一题|自动跳转")

# 翻页/操作按钮词。**实测**（2026-09-22 的真实 8 图样本）里"下一题"就是 App 按钮：
# 它经常独立成行（`上一题 下一题 存疑` 里另两个词被删后只剩它），也会出现在页眉行
# 末尾。因此这里必须包含它 —— 留着它就是在解答文件里留一行纯按钮文字。
#
# 对正文的**唯一保护**：只有当"下一题"之后**没有跟中文字符**时才删。这样
# `下一题题干`（正文）会保留，而 `下一题`（按钮）与页眉里的 `…自动跳下一题 ○ 错题反`
# 会被清掉。实测的真实题干形态是"19、下一题题干"（带题号前缀的完整句子），
# 且有题号时整行本来就会被保留为正文。
_BUTTON_WORDS_RE = re.compile(r"上一题|下一题(?![一-龥])|存疑|错题反|收起|展开|收藏|标记|答题卡|交卷")

# 选项前的单选项圈：`○ A.` / `● B.` —— 这是 App 的选中状态标记，不是题目内容。
# **只在"行首（或行首空白后）紧跟选项字母"时**才去掉圈：正文里的 `○` 可能是题干的
# 一部分（如"○ 表示已选中该项"），无条件删会吃掉正文。
_OPTION_RADIO_RE = re.compile(r"^(\s*)[○●◯◉]\s*(?=[A-Za-z][.、．)）])")

# 孤立残留的圈号（前后是空白或行边界），如页眉清理后剩下的 `单选题 第18题 ○`。
# 与上一条的区别：这条不要求后面跟选项字母，但要求它是**独立**的字符。
_STANDALONE_RADIO_RE = re.compile(r"(?:(?<=\s)|^)[○●◯◉](?=\s|$)")

# 页眉可能带 OCR 前缀。实测形态：`正科目（...）`、`工科目 (...)`、`王科目（...）`、
# `工程目（...）` —— 第一个字被读歪，有时连"科"也被读成"程"。
# **只容忍"科目"前 0–2 个非空白字**，并额外枚举「工程目」这一种形态；
# 不做更宽的匹配，避免吃掉正文（正文里的"科目"通常在句中，不在行首紧跟括号）。
_SUBJECT_LINE_RE = re.compile(r"^(?:\S{0,2}?科目|工程目)\s*[（(：:]")

# 孤立的长数字（试卷/考试 ID，如 `78060`）
_EXAM_ID_RE = re.compile(r"^\s*\d{4,}\s*$")

# 纯进度/元信息（`满分：135 分 及格：115 分 已答`）：整行可删，但**先**把其中的
# 题号/题型摘出来（正常不含，但不敢假设排版永远规整）。
_SCORE_META_RE = re.compile(r"满分\s*[:：]|及格\s*[:：]|已答\s*$")


def search_metadata(line: str) -> tuple[str | None, int | None]:
    """在任意文本里找"题型"与"题号"。返回 (题型, 题号数字)。"""
    kind_match = _QUESTION_KIND_RE.search(line)
    number_match = _QUESTION_NUMBER_RE.search(line)
    return (
        kind_match.group(0).strip() if kind_match else None,
        int(number_match.group(1)) if number_match else None,
    )


def metadata_line(kind: str | None, number: int | None) -> str:
    """把题目元信息渲染成一行（顺序固定"题型 题号"）。"""
    parts = [part for part in (kind, f"第{number}题" if number else None) if part]
    return " ".join(parts)


def _salvage_metadata(line: str) -> str:
    """从一行噪音里摘出"题目元信息"（题型 + 题号）。

    Examples:
        `单选题 第18/60题 自动跳下一题` → `单选题 第18题`
        `第18/60题`                     → `第18题`
        `满分：135分 及格：115分 已答`   → ``（无题号无题型）
        `上一题 下一题 存疑`             → ``
    """
    return metadata_line(*search_metadata(line))


def clean_page_line(line: str) -> str:
    """处理单行：返回保留的内容（可能为空串 = 该行删除）。

    三种情况：
    - 行内只是混了噪音片段（`自动跳下一题`）→ 删掉片段，**保留题号/题型**；
    - 整行是噪音但含题号/题型（`第18/60题`）→ 只留元信息，并把它规范成 `第18题`
      （去掉 `/60` 这种进度分母）；
    - 正常正文 → 原样返回（不做任何改写）。

    **题号与题型是必须保住的信息**：人读答案卡时首先要知道"这是第几题、什么题型"。
    宁可多留一行噪音，也不能把这两样吃掉。
    """
    stripped = line.strip()
    if not stripped:
        return line

    kind, number = search_metadata(stripped)
    # `第18/60题` 这种**带进度分母**的独立导航行：规范成 `第18题`（分母是 App 的
    # 进度显示，不是题目信息）。注意只认带斜杠的格式 —— `第 2 题：求极限` 是正文，
    # 没有斜杠就不会命中这里。
    standalone_nav = bool(
        _QUESTION_NUMBER_RE.search(stripped)
        and "/" in stripped
        and re.fullmatch(r"[\s\d/第题]+", stripped)
    )
    looks_noisy = bool(
        _NOISE_RE.search(stripped)
        or _BUTTON_WORDS_RE.search(stripped)
        or _EXAM_ID_RE.match(stripped)
        or _SCORE_META_RE.search(stripped)
        or _SUBJECT_LINE_RE.match(line)
        or standalone_nav
    )
    if not looks_noisy:
        # 即使不是噪音行，选项前的单选项圈（`○ A.`）也要去掉 —— 它是 App 的选中
        # 状态标记，不是题目内容。实测模型经常把 `○ A.` 单独输出成一行，若只在
        # "噪音行"分支里处理，这些圈就会原样留在解答文件里。
        return _OPTION_RADIO_RE.sub(r"\1", line)

    # 行内噪音片段先删掉，剩下的部分就是正文（例如
    # `18、下列说法正确的是（ ） 自动跳下一题` → `18、下列说法正确的是（ ）`）。
    #
    # **不做"补题型前缀"这类聪明事**：那需要判断"这个题型词是元信息还是正文"
    # （`单选题：下列说法正确的是（ ）` 与页眉里的 `单选题` 长得一样），
    # 猜错的代价是往正文里塞字。只删噪音，不添加任何东西。
    cleaned = _NOISE_RE.sub("", line)
    if _SUBJECT_LINE_RE.match(line):
        # 科目行整行丢弃：它既不含题号也不含题型，对读题没有价值
        return ""
    if not standalone_nav:
        cleaned = _BUTTON_WORDS_RE.sub("", cleaned)
        if _EXAM_ID_RE.match(cleaned.strip()) or _SCORE_META_RE.search(cleaned):
            cleaned = ""      # 试卷 ID / 满分及格元信息：整行都是噪音，不留残余
    # `第18/60题` → `第18题`：分母是 App 的进度显示，不是题目信息。无论这行是
    # 独立导航还是与其它噪音混在一行，都统一规范化（题号本身必须留下）。
    if number is not None:
        cleaned = re.sub(r"第\s*\d+\s*/\s*\d+\s*题", f"第{number}题", cleaned)
    # 选项圈号（`○ A.`）：App 的选中状态标记，去掉圈、保留 `A.`
    cleaned = _OPTION_RADIO_RE.sub(r"\1", cleaned)
    # 清理页眉后被孤立剩下的圈（`单选题 第18题 ○`）—— 正文里的圈不会孤零零站着
    cleaned = _STANDALONE_RADIO_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()

    # 行内还有内容 → 那就是正文（噪音已删干净），原样返回，**不补任何前缀**
    if cleaned:
        return cleaned

    # 整行被清空 = 这行只在传达"题号/题型"这类元信息（外加噪音）→ 保住元信息
    return metadata_line(kind, number)


def strip_chrome_lines(text: str) -> tuple[str, list[str]]:
    """剥离考试 App 的页眉/页脚/导航噪音，**但保留题号与题型**。

    Returns:
        (题目正文, 被改写的行)。第二项原样返回处理前的行，供日志与测试核对
        —— 剥离过程必须可审计，不能"悄悄少吃了几行正文"。
    """
    body: list[str] = []
    touched: list[str] = []
    for line in (text or "").splitlines():
        cleaned = clean_page_line(line)
        if cleaned.strip() != line.strip():
            touched.append(line.strip())
        if cleaned.strip() or not line.strip():
            body.append(cleaned)
    return "\n".join(body), touched


def chrome_reason(line: str) -> str | None:
    """返回该行被判定为噪音的原因（没命中则 None）。用于日志与排查。"""
    if _SUBJECT_LINE_RE.match(line):
        return "科目行"
    if _SCORE_META_RE.search(line):
        return "满分/及格元信息"
    if _QUESTION_NUMBER_RE.search(line):
        return "题号导航（保留题号与题型）"
    if _NOISE_RE.search(line):
        return "自动跳题导航"
    if _BUTTON_WORDS_RE.search(line):
        return "翻页/操作按钮"
    if _EXAM_ID_RE.match(line.strip()):
        return "孤立的长数字（试卷 ID）"
    return None


# ---------------------------------------------------------------------------
# 代码围栏跨页合并
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")


def _page_body(page: str) -> str:
    """单页正文的排版清理：去首尾空行、统一行尾空白、去掉尾部连续空行。

    为什么去掉**尾部**空行很重要：拼接时上一页的尾空行 + 下一页的首空行会累积成
    3–4 个空行，Markdown 渲染出大片空白；人读答案卡时这是最明显的"没排版"。
    """
    lines = [line.rstrip() for line in (page or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _fence_state(body: str) -> bool:
    """正文是否停在**未闭合**的代码围栏内。

    用"围栏行出现次数的奇偶"判断：奇数 = 有一个 ``` 还没配对。
    这是近似（代码里可能出现 ``` 字面量），但对"手机截图翻页把代码块切成两半"
    这个真实场景足够，且判错的后果只是多/少一个换行。
    """
    count = sum(1 for line in body.splitlines() if _FENCE_RE.match(line))
    return count % 2 == 1


def normalize_pages(pages: list[str], continuations: list[bool] | None = None) -> tuple[str, list[str]]:
    """把逐页 OCR 文本排成人类可读的一份题目文本。

    做三件事，全部确定性、无模型参与：

    1. **剥离页眉/页脚/题号导航**（`strip_chrome_lines`）；
    2. **合并跨页代码围栏**：上一页停在未闭合的 ``` 内时，它与下一页之间**不加空行**
       （加了会被 Markdown 渲染成两个代码块，中间凭空断开）；
    3. **规整空行**：`CONT` 接缝用单换行，不同题之间用空行，尾随空行去掉。

    Args:
        pages: 逐页正文（长度 = 图片数）。
        continuations: 每页是否为上一页的延续（`True` = `CONT`）。缺省全按新题处理。

    Returns:
        (合并后的题目文本, 被剥离的噪音行)。
    """
    flags = list(continuations or [])
    bodies: list[str] = []
    removed: list[str] = []
    for index, page in enumerate(pages):
        body, dropped = strip_chrome_lines(page or "")
        removed.extend(dropped)
        bodies.append((index, _page_body(body)))

    parts: list[str] = []
    previous_open_fence = False
    accumulated_lines: list[str] = []       # 已收录的所有行，用于裁剪 CONT 页的重抄
    for index, body in bodies:
        if not body:
            continue
        lines = body.splitlines()
        is_continuation = bool(flags[index]) if index < len(flags) else False
        # CONT 页若把上一页内容整段重抄，先把它裁掉（见 _trim_continuation_repeats 的说明）
        if is_continuation and accumulated_lines and lines:
            trimmed = _trim_leading_repeat(lines, accumulated_lines)
            if trimmed is not None:
                lines = trimmed
        body = "\n".join(lines)
        if not body.strip():
            continue
        if not parts:
            parts.append(body)
        else:
            if previous_open_fence or is_continuation:
                # 代码块中间 / 同一题的接续：单换行，不能插空行
                parts.append("\n" + body)
            else:
                parts.append("\n\n" + body)
        previous_open_fence = _fence_state(body)
        accumulated_lines.extend(lines)
    joined = _drop_adjacent_duplicate_lines("".join(parts).strip())
    return joined, removed


def _trim_leading_repeat(lines: list[str], accumulated: list[str]) -> list[str] | None:
    """把 `lines` 开头那段"与 accumulated 末尾逐行相同"的内容删掉。

    Returns:
        裁剪后的行列表；没有可裁的重叠时返回 None（调用方保持原样）。

    只比较**完全相同的行**，因此模型真写了新内容时第一行就匹配不上，一行都不会删。
    从长到短找最长匹配，命中即止。
    """
    for size in range(min(len(lines), len(accumulated)), 0, -1):
        if [line.strip() for line in lines[:size]] == [
            line.strip() for line in accumulated[-size:]
        ]:
            return lines[size:]
    return None


def join_with_separator(texts: list[str], separator: str = "\n---[NEXT]---\n") -> str:
    """用显式分隔符连接多段文本（各段先各自做页面清理）。

    用途：**内联拼接不成立**的路径（JSON 回退协议 / 跳过润色 / 禁用内联）仍需要
    一个显式的页边界 —— 相邻两页可能是两道完全不同的题，让读题的人（与求解模型）
    知道"这里换了一页"。这与 `normalize_pages` 的"同一题连续页用空行"是两种语义：
    前者是"我不知道这两页什么关系"，后者是"模型告诉我它们是同一题"。

    题号/题型/代码围栏的处理与 `normalize_pages` 一致（都走 `_page_body`），
    区别只在内联时**不剥离页眉** —— 页眉里的题号在"多道独立题"场景恰恰是有用信息。
    """
    parts: list[str] = []
    for text in texts:
        body, _dropped = strip_chrome_lines(text or "")
        cleaned = _page_body(body)
        if cleaned:
            parts.append(cleaned)
    return separator.join(parts)


def _drop_adjacent_duplicate_lines(text: str) -> str:
    """删掉**紧邻的完全重复行**。

    为什么需要：页眉里的题号会跨页重复。同一道题的第 1、2 页各自带一个页眉
    `第18题`，而 `CONT` 接缝只用一个换行把它们接起来时，就会出现

        18、代码……
        ```python
        ...
        ```
        第18题
        第19题

    中间那行是纯噪音。**只删"与上一行逐字相同"的行** —— 这是最保守的判据，
    宁可留一行重复的题号，也不会误删正文里两行相同的内容（那本来就是极罕见排版）。
    """
    out: list[str] = []
    for line in text.splitlines():
        if out and line.strip() and line.strip() == out[-1].strip():
            continue
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 公式识别（供 C：只把公式送去规范化）
# ---------------------------------------------------------------------------

# 块级公式优先：`$$\n...\n$$`、`\[\n...\n\]`、以及裸的 `\begin{...}...\end{...}` 环境。
# 顺序即优先级，扫描时取最早出现的那个。
_MATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    # $$ ... $$（跨行，非贪婪）
    re.compile(r"\$\$[\s\S]*?\$\$"),
    # \[ ... \]
    re.compile(r"\\\[[\s\S]*?\\\]"),
    # \begin{...} ... \end{...}（矩阵/方程组等环境，**必须整体处理**）
    re.compile(r"\\begin\{(?P<env>[A-Za-z*]+)\}[\s\S]*?\\end\{(?P=env)\}"),
    # \( ... \)
    re.compile(r"\\\([\s\S]*?\\\)"),
    # 行内 $...$（不跨越 $ 与换行；避免把 "价格 $5 和 $6" 这种吞进来）
    re.compile(r"(?<!\$)\$(?!\$)[^\n$]{1,200}?\$(?!\$)"),
)


def extract_math_spans(text: str) -> tuple[str, list[str]]:
    """把文本切成"非公式段"与"公式段"交替的骨架，返回 (骨架, 公式列表)。

    骨架里每个公式位置替换成 `\\x00{i}\\x00` 占位符，用 `rebuild_math` 还原。
    这样模型只看到公式本身，输出量从"整篇 10K token"降到"几个公式几十 token"。

    只取**不重叠**的最早匹配：先扫出全部候选区间，按起点排序后贪心择取，
    避免 `$$..$$` 与其中的 `$..$` 同时命中导致重复替换。
    """
    spans: list[tuple[int, int]] = []
    for pattern in _MATH_PATTERNS:
        for match in pattern.finditer(text or ""):
            spans.append((match.start(), match.end()))
    # 贪心：按起点升序，区间不重叠者入选（长匹配优先由起点相同者的长度决定）
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen: list[tuple[int, int]] = []
    cursor = -1
    for start, end in spans:
        if start < cursor:
            continue
        chosen.append((start, end))
        cursor = end

    skeleton_parts: list[str] = []
    formulas: list[str] = []
    position = 0
    for start, end in chosen:
        skeleton_parts.append((text or "")[position:start])
        skeleton_parts.append(f"\x00{len(formulas)}\x00")
        formulas.append((text or "")[start:end])
        position = end
    skeleton_parts.append((text or "")[position:])
    return "".join(skeleton_parts), formulas


_PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")


def rebuild_math(skeleton: str, formulas: list[str]) -> str:
    """把规范化后的公式填回骨架。

    占位符在骨架里的出现次数由 `extract_math_spans` 保证恰好一次；这里仍做越界防护
    ——模型若把占位符写坏（多写/漏写），缺失的位置保留原位不还原，而不是抛异常。
    """
    def _replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if 0 <= index < len(formulas):
            return formulas[index]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, skeleton)
