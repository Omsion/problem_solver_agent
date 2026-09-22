# -*- coding: utf-8 -*-
"""
vision_ab.py - 视觉层 A/B 评测工具（S1 / S2 / S12 的唯一判据）

**为什么必须有这个工具**：视觉模型从 GLM-4.6V 换到 `deepseek-flash` 是一次
"用未知的 OCR 质量换确定的速度与成本"的交易。没有量化，"换不换"就只能靠信仰；
迁移方案第 0 节把 OCR 不倒退（S1）、分类一致率 ≥90%（S2）、无 LaTeX 静默损坏（S12）
定为硬闸门，本工具就是量这三件事的地方。

设计要点（每条都对应一个具体的坑）：
1. **不改生产代码路径**：provider 通过 `classify_and_transcribe(..., provider=)` 直接
   透传（底座已支持），不动任何全局 config，也不用子进程改环境变量 —— 一条进程内
   跑两家，两家的耗时/结果不会被"重启进程"之类的差异污染。
2. **如实记录走了哪条回退路径**：合并调用（1 次视觉调用）失败时会回退到
   `classify_and_transcribe_parallel`（1 次分类 + N 次 OCR）。这条信息是迁移方案第 8 节
   要回填的"PAGE 协议成功率"，所以必须写进报告，而不是悄悄用了回退还不说。
3. **要素差按"题目正文"比对**：基准 = `--reference` 指定的那一家的逐页文本
   （不是"两版并集"—— 并集语义会让任何一家少写的内容都变成"谁都没丢"，从而失去判据
   意义）。比对**先剥离页眉/页脚/题号导航**，因为 `extract_elements` 抓的是全页数字，
   试卷 ID、`满分：135分 及格：115分`、`第18/60题` 都会被算成"题干数字"，产生假缺失
   （2026-09-21 实测：某组"缺失 11 个"全部来自页眉）。全页计数同时写进报告
   （`要素缺失数（全页…）`）与被剥离的行样例，剥离过程完全可核对。
4. **LaTeX 静默损坏检测**：JSON 协议下 `\frac`→`\f`(formfeed)、`\begin`→`\b`(backspace)、
   `\theta`→`\t`(tab)、`\neq`→`\n` 都是**合法转义**，解析"成功"但正文被改写，还会
   通过页数校验直接写进解答文件。换 PAGE 协议的目的就是消除这类损坏，因此必须
   断言输出里既没有这些控制字符，也没有 `rac{` / `egin{` 这种被吃掉反斜杠的残片。
5. **不硬编码任何密钥**：全部走 `config._vision_api_key(provider)`（内部就是读 .env / 环境变量）。
6. **"调用成功"必须是"拿到了可用输出"**：`ok` 由"至少有一页非空"决定，而不是"没抛异常"。
   否则单 provider 模式下两条硬闸门都标"不适用"，报告会在**什么都没跑出来**的时候
   打印"✅ 满足验收标准" —— 这是最危险的一种假 PASS。
7. **截断闸门必须能被截断触发**：缺 `<<<END>>>`（`ended=False`）时，即使每页都有文字
   也计为被截断（最后一页常被 max_tokens 从中间切断，"空页计数"会漏成 0）；并行回退
   路径没有协议级信号，只能标"无法判定"，绝不据此写成满足。
8. **基准为空就不许打勾**：`--reference` 的基准若没有任何非空页，"要素缺失 0"只是
   "没得比"，本项判为无法判定/不满足，而不是伪造一个 PASS。

用法：
    py -3.10 -m tools.vision_ab check -i <图或目录>              # 两家各跑一遍（默认）
    py -3.10 -m tools.vision_ab check -i <目录> --provider deepseek
    py -3.10 -m tools.vision_ab check -i <目录> --provider deepseek --reference zhipu
    py -3.10 -m tools.vision_ab check -i <目录> --images 2       # 只跑 2 张，控制费用

产物：`_probe/vision_ab/<时间戳>/report.md`（人工读）+ 同目录 `result.json`（回归对比）
      + `<provider>.log`（该 provider 的日志切片，失败时用于定位）。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from problem_solver_agent import config, vision_client  # noqa: E402
from problem_solver_agent.image_order import photo_timestamp, sort_images_by_time  # noqa: E402

# 评测产物目录（与 _probe/ 下其他施工产物同级，实施完成后可整目录删除）
REPORT_ROOT = Path(__file__).resolve().parents[1] / "_probe" / "vision_ab"

# 默认输入目录：仓库里现成的题图，避免用户还得先找图
DEFAULT_INPUT = "webapp/uploads"

# 说明（F11）：这里原本有一个模块级常量 `COMBINED_TIMEOUT = config.VISION_COMBINED_TIMEOUT`，
# 但没有任何代码读它 —— 评测的超时全部由生产路径内部的 `config.VISION_COMBINED_TIMEOUT`
# 决定，报告里的配置快照与 CLI 输出也直接读 config。留一个不生效的副本只会让人以为
# "改这里能改评测超时"，所以删掉：要改超时就改生产 config（那才是被测的真实行为）。

# 失败时从日志里捞的关键字：这些行解释了"为什么这条路径失败"，
# 是回退到 parallel 之后唯一能追查的线索（`classify_and_transcribe` 失败只返回 None）。
_ERROR_KEYWORDS = (
    "失败", "错误", "异常", "未初始化", "未找到", "超时", "截断",
    "回退", "不可用", "invalid", "error", "timeout", "missing", "refus",
)


# ==============================================================================
# 第一部分：单元素提取与损坏检测
# ==============================================================================

# LaTeX 命令白名单：从正文里抠出"用了哪些公式片段"，用于比对要素有没有丢。
# 覆盖迁移文档点名的 \frac / \begin / \theta / \neq / \sqrt 以及常见同类命令。
LATEX_COMMANDS = (
    "frac", "dfrac", "tfrac", "begin", "end", "theta", "alpha", "beta", "gamma",
    "delta", "lambda", "mu", "pi", "sigma", "omega", "neq", "leq", "geq", "sqrt",
    "sum", "prod", "int", "lim", "log", "ln", "sin", "cos", "tan", "times", "cdot",
    "div", "pm", "infty", "partial", "nabla", "left", "right", "text", "mathbf",
    "mathbb", "mathrm", "overline", "vec", "hat", "angle", "triangle", "perp",
    "parallel", "approx", "equiv", "subset", "cup", "cap", "binom", "matrix",
    "cases", "align", "array", "rightarrow", "Rightarrow", "quad", "qquad",
    "displaystyle", "operatorname", "pmod", "bmod", "pmatrix", "vmatrix",
    "varepsilon", "varphi", "rho", "tau", "phi", "psi", "eta", "iota", "kappa",
    "nu", "xi", "zeta", "circ", "star", "ast", "propto", "sim", "cong", "forall",
    "exists", "neg", "land", "lor", "in", "notin", "emptyset", "varnothing",
)
# 命令名按长度降序，保证 `\notin` 不会被 `\not` 抢走前缀
_LATEX_ALT = "|".join(sorted(LATEX_COMMANDS, key=len, reverse=True))

_RE_LATEX_CMD = re.compile(r"\\(" + _LATEX_ALT + r")\b")
# `\frac` 已在本表内单列，保持与文档一致的顺序便于阅读报告
for _name in ("frac", "begin", "theta", "neq", "sqrt"):
    assert _name in LATEX_COMMANDS, f"{_name} 必须在 LaTeX 命令表内"

_RE_ENV = re.compile(r"\\begin\{([A-Za-z*]+)\}")
# 2.5 / 3/4 / 12% / 1,000 之类的题干数字（不含纯序号外的符号）
_RE_NUMBER = re.compile(r"\d+(?:[.,]\d+)*(?:\s*%)?")
# 选项标号：`A.` / `B、` / `（C）` / `D)`，只在行首或行内独立出现时算（避免把英文单词里的 A 算进去）
_RE_OPTION = re.compile(r"(?:(?<=^)|(?<=[\s（(]))([A-Ha-h])\s*[.、．)）]")
# 选项本身是公式时（`$A$` / `\$A\$`）
_RE_OPTION_MATH = re.compile(r"\$\s*([A-Ha-h])\s*\$")
# 协议级截断信号（`<<<END>>>` 是否出现）由生产返回值 `classify_and_transcribe(...)["ended"]`
# 提供，因此工具侧不再自己解析原始流文本（早期版本在此定义页标记/END 标记两个正则）。

_CATEGORY_LABELS = (
    ("numbers", "题干数字"),
    ("fractions", "分数/百分数"),
    ("latex_commands", "LaTeX 命令"),
    ("environments", "LaTeX 环境"),
    ("options", "选项标号"),
)

# 考试 App 的页眉/页脚/题号导航 —— **不是题目要素**（S1 的判据说的是"题干、选项、数字、公式"）。
# 为什么必须单独剥离：`extract_elements` 抓的是全页数字，试卷 ID（`78060`）、
# `满分：135分 及格：115分 已答`、`第18/60题` 都会落进"题干数字"。2026-09-21 的验收
# 实测证明这会产生**假缺失**：同一组 8 张图，两家模型各自在不同页页眉上省略/保留，
# 于是"缺失 11 个"全部来自页眉数字，而正文逐要素比对无缺失。
# 被剥离的行会原样记进报告（`chrome_samples`），可人工核对，不做隐藏。
_CHROME_LINE_RE = re.compile(
    r"(科目|模拟试题|满分|及格|已答|上一题|下一题|存疑|错题反|自动跳下一题"
    r"|第\s*\d+\s*/\s*\d+\s*题"          # 题号导航（必须有斜杠，避免误伤"第 2 题"这种正文）
    r"|^\s*\d{4,}\s*$"                   # 整行只有一个长数字（试卷/考试 ID）
    r")"
)

# 被吃掉反斜杠的残片 → 正确写法的映射。
# 为什么按"残片"查而不是按"控制字符"查：控制字符可能被后续处理（如 .strip()、
# 写文件时的编码）吃掉，残片 `rac{` 才是最终落盘的样子，两种都要查。
# 说明：不检测 `\neq`→`eq` / `\leq`→`eq` 这一种。`eq` 在数学文本里天然常见
# （`eq.`、`(eq 3)`、`equations`），误报会让"S12 无损坏"这个结论直接失去可信度；
# 宁可漏报这一种，也要保证报出来的每一条都是真损坏。
_CORRUPTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("\\frac → rac{（formfeed 吃掉了 \\f）", re.compile(r"(?<![A-Za-z\\])rac\s*\{")),
    ("\\begin → egin{（backspace 吃掉了 \\b）", re.compile(r"(?<![A-Za-z\\])egin\s*\{")),
    ("\\theta → heta（tab 吃掉了 \\t）", re.compile(r"(?<![A-Za-z\\])heta\b")),
    ("\\times → imes（tab 吃掉了 \\t）", re.compile(r"(?<![A-Za-z\\])imes\b")),
    ("\\nabla → abla（换行吃掉了 \\n）", re.compile(r"(?<![A-Za-z\\])abla\b")),
    ("\\notin → otin（换行吃掉了 \\n）", re.compile(r"(?<![A-Za-z\\])otin\b")),
    ("\\left → eft（换行吃掉了 \\n）", re.compile(r"(?<![A-Za-z\\])eft\b")),
    ("\\sqrt → qrt{（无法转义，通常直接硬失败）", re.compile(r"(?<![A-Za-z\\])qrt\s*\{")),
)

# 控制字符清单：formfeed / backspace / tab / bell / vertical-tab / NUL。
# `\t` 与 `\n` 分开处理：换行和空格、缩进是正常排版，不算损坏；
# 而 tab 出现在"公式语境"（同一行有 LaTeX 命令）里才说明是 `\theta` 被吃了。
_CONTROL_CHARS = ("\x0c", "\x08", "\x09", "\x0b", "\x07", "\x00")
_CONTROL_NAMES = {
    "\x0c": "formfeed(\\f)",
    "\x08": "backspace(\\b)",
    "\x09": "tab(\\t)",
    "\x0b": "vertical-tab(\\v)",
    "\x07": "bell(\\a)",
    "\x00": "NUL(\\0)",
}


def extract_elements(text: str) -> dict[str, list[str]]:
    """把一页转录文本拆成"可逐项比对的要素"。

    为什么需要它：字符数差只能发现"少了一大段"，发现不了"数字 3.5 变成了 3"
    或"`\\frac` 丢了" —— 而后者正是多图 OCR 出错的典型方式（一处缺失，整道题废掉）。

    注意：本函数抓**全页**数字，因此会包含页眉/题号里的数字；判据请用
    `extract_body_elements`（先剥离这些非题目行）。
    """
    numbers = _RE_NUMBER.findall(text)
    fractions = [n for n in numbers if any(ch in n for ch in "./%")]
    return {
        "numbers": numbers,
        "fractions": fractions,
        "latex_commands": _RE_LATEX_CMD.findall(text),
        "environments": _RE_ENV.findall(text),
        "options": [m.group(1).upper() for m in _RE_OPTION.finditer(text)]
                   + [m.group(1).upper() for m in _RE_OPTION_MATH.finditer(text)],
    }


def extract_body_elements(text: str) -> dict[str, list[str]]:
    """只从**题目正文**里抽要素（先剥离页眉/页脚/题号导航行）。

    为什么需要：`extract_elements` 的分类名叫"题干数字"，但它按正则抓全页数字，
    于是试卷 ID（`78060`）、`满分：135分 及格：115分`、`第18/60题` 都会被算成
    "题干数字"。2026-09-21 的迁移验收实测证明这会产生**假缺失**：同一组 8 张图，
    两家模型各自在不同页面上省略了页眉 → "缺失 11 个"全部来自页眉数字，而题目正文
    逐要素比对无缺失（且候选比基准多出 24 个要素、字符数 +345）。

    S1 说的是"题目要素（题干、选项、数字、公式）"无遗漏，因此**判据用本函数**；
    原始计数同时保留在报告里（`missing_raw_count`），不做隐藏。
    """
    body, _ignored = strip_chrome_lines(text)
    return extract_elements(body)


def strip_chrome_lines(text: str) -> tuple[str, list[str]]:
    """剥离考试 App 的页眉/页脚/题号导航行，返回 (题目正文, 被剥离的行)。"""
    body: list[str] = []
    ignored: list[str] = []
    for line in (text or "").splitlines():
        if _CHROME_LINE_RE.search(line):
            ignored.append(line.strip())
        else:
            body.append(line)
    return "\n".join(body), ignored


def _snippet(text: str, offset: int, width: int = 24) -> str:
    start = max(0, offset - width // 2)
    return sanitize_controls(text[start:start + width]).replace("\n", "\\n")


def sanitize_controls(text: str) -> str:
    """把控制字符换成可见记号后再写进报告。

    否则 Markdown 里会出现"看不见"的字符（backspace 甚至会把光标退回去覆盖前面的字），
    人工比对时根本看不出损坏在哪 —— 报告本身就失去了作为证据的资格。
    """
    for char, name in _CONTROL_NAMES.items():
        text = text.replace(char, f"⟦{name}⟧")
    return text.replace("\r", "")


def detect_corruption(text: str) -> list[dict[str, Any]]:
    """检测 LaTeX 静默损坏（S12），返回命中列表（空列表 = 干净）。

    两类检测：
    A. **控制字符**：`\\f` / `\\b` / `\\t` 被 JSON 层解释掉后留下的真实控制字符；
    B. **被吃掉落转义的残片**：`rac{` / `egin{` / `heta` / `eq` —— 即使控制字符被
       后续处理清掉，正文里的这些残片仍然是损坏的铁证。
    """
    findings: list[dict[str, Any]] = []

    for char in _CONTROL_CHARS:
        for line_no, line in enumerate(text.splitlines(), start=1):
            if char not in line:
                continue
            # tab 单独判：出现在公式语境（同行有 LaTeX 命令）才算损坏，
            # 纯缩进用 tab 是合法排版，误报会让验收结论不可信。
            if char == "\x09" and not _RE_LATEX_CMD.search(line) and "\\" not in line:
                continue
            findings.append({
                "kind": "control_char",
                "name": _CONTROL_NAMES[char],
                "line": line_no,
                "snippet": _snippet(line, line.index(char)),
            })

    for name, pattern in _CORRUPTION_PATTERNS:
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            findings.append({
                "kind": "eaten_backslash",
                "name": name,
                "line": line_no,
                "snippet": _snippet(text, match.start()),
            })

    return findings


# ==============================================================================
# 第二部分：跑一家 provider 的完整 OCR
# ==============================================================================

class _RecordCollector(logging.Handler):
    """把本轮调用的日志收进内存：失败时错误原文是唯一证据，不能被冲进控制台就算了。"""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.records.append(self.format(record))
        except Exception:  # pragma: no cover - 日志格式化不该影响评测
            pass


@contextmanager
def _collect_logs():
    """临时挂一个 Handler 到项目 logger 上，退出时摘掉（不影响其他调用方）。"""
    collector = _RecordCollector()
    collector.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    target = logging.getLogger("AgentLogger")
    target.addHandler(collector)
    try:
        yield collector.records
    finally:
        target.removeHandler(collector)


def _useful_lines(lines: list[str]) -> list[str]:
    """从日志里挑出解释失败原因的行（限长，避免把整篇日志塞进报告）。"""
    picked: list[str] = []
    for line in lines:
        if not any(key.lower() in line.lower() for key in _ERROR_KEYWORDS):
            continue
        text = line.strip()
        if len(text) > 300:
            text = text[:300] + "…"
        if text not in picked:
            picked.append(text)
    return picked[:12]


def _combined_truncation(combined: dict[str, Any], expected_pages: int) -> dict[str, Any]:
    """由**生产返回值**判定截断（S 判据的精确版本）。

    流式响应拿不到 `finish_reason`，因此 `classify_and_transcribe` 返回的 `ended`
    （响应里有没有 `<<<END>>>`）就是唯一的协议级截断信号。
    为什么读返回值而不是包装 `_collect_stream`：包装测的是"包装后的行为"，
    而验收必须落在真实生产路径上。
    """
    mode = str(combined.get("vision_mode") or "")
    ended = combined.get("ended")
    pages = [str(p) for p in (combined.get("pages") or [])]
    if ended is None or mode == "parallel":
        return {
            "truncated": False,
            "finish_reason": None,
            "detail": f"vision_mode={mode or '—'}，无协议级截断信号（退化到空页代理指标）",
        }
    filled = sum(1 for page in pages if page.strip())
    truncated = not bool(ended)
    return {
        "truncated": truncated,
        "finish_reason": None,
        "ended": bool(ended),
        "page_markers": filled,
        "expected_pages": expected_pages,
        "detail": (
            f"有内容的页 {filled}/{expected_pages}，"
            f"{'有' if ended else '**缺**'} <<<END>>>"
        ),
    }


def _usable_page_count(pages: list[Any] | None) -> int:
    """返回"真的有内容"的页数（非空白页）。

    为什么单独定义：F2 的根因是 `ok` 被当成了"调用没抛异常"，而"有没有可用输出"的
    唯一可靠载体是页正文 —— `pages` 列表长度为 N 但 N 页全空，等于什么都没拿到。
    """
    return sum(1 for page in (pages or []) if str(page).strip())


def _empty_page_count(pages: list[Any] | None) -> int:
    return sum(1 for page in (pages or []) if not str(page).strip())


def _apply_combined_truncation(result: dict[str, Any], truncated: dict[str, Any]) -> None:
    """把合并路径的截断判定写回 run（F3 的核心修复）。

    为什么需要独立一步：截断信号有三个来源（服务端 `finish_reason` / 协议级
    `<<<END>>>` / "补做后仍有空页"的推断），而闸门只读 `truncated_pages`。
    旧实现把"缺 `<<<END>>>`"换算成"空页计数"，于是最典型的截断 —— 转录在
    **最后一页中间**被 max_tokens 切断、于是每一页都有文字 —— 被算成 0 页而 PASS。
    这里保证：只要判定为截断，`truncated_pages` 至少是 1。
    """
    empty = _empty_page_count(result.get("pages"))
    if truncated.get("finish_reason"):
        # 服务端明确给了 finish_reason：最强证据，直接采信
        result["finish_reason"] = str(truncated["finish_reason"])
        result["finish_reason_basis"] = "服务端 finish_reason"
        result["truncation_signal"] = "finish_reason"
        result["truncated"] = True
        result["truncated_pages"] = max(1, empty)
        return
    if "ended" in truncated:
        hit = bool(truncated["truncated"])
        result["finish_reason"] = "length(协议推断)" if hit else "stop(协议推断)"
        result["finish_reason_basis"] = truncated["detail"]
        result["truncation_signal"] = "protocol"
        result["truncated"] = hit
        result["truncated_pages"] = max(1, empty) if hit else 0
        return
    # 既没有 finish_reason 也没有协议级 END 信号：只能按"补做后仍有空页"推断。
    # 推断不出截断时记 `unknown`（而不是 0 = 没截断），交给 verdict 判"无法判定"。
    inferred = bool(result.get("failed_pages"))
    result["finish_reason"] = "length(推断)" if inferred else None
    result["finish_reason_basis"] = truncated["detail"]
    result["truncation_signal"] = "inferred" if inferred else "unknown"
    result["truncated"] = inferred
    result["truncated_pages"] = max(1, empty) if inferred else 0


def run_provider(provider: str, image_paths: list[Path]) -> dict[str, Any]:
    """用指定 provider 跑一遍完整 OCR（合并调用优先，失败回退并行路径）。

    Returns:
        含 provider/ok/path/mode/pages/problem_type/elapsed/failed_pages/
        finish_reason/truncated_pages/error 的结果字典。

        `ok` 的语义是"**拿到了可用输出**"（至少一页非空），不是"调用没抛异常"。
    """
    cfg = config.VISION_PROVIDER_CONFIG[provider]
    model = cfg["classify_model"]
    started = time.perf_counter()
    result: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "base_url": cfg["base_url"],
        "key_env": cfg["api_key_env"],
        "key_configured": bool(config._vision_api_key(provider)),  # noqa: SLF001
        "combined_expected": vision_client.combined_call_enabled(len(image_paths)),
        "ok": False,
        "path": "",
        "vision_mode": "",
        "problem_type": "",
        "pages": [],
        "failed_pages": [],
        "continuations": [],
        "refilled": 0,
        "elapsed": 0.0,
        "finish_reason": None,
        "truncated_pages": 0,
        # `truncation_signal`：截断结论来自哪种信号（protocol / finish_reason /
        # inferred / unknown）。verdict 用它区分"确认未截断"与"无从判断"。
        "truncation_signal": "unknown",
        "truncated": False,
        "usable_pages": 0,
        "error": "",
        "log_excerpt": [],
    }

    if not result["key_configured"]:
        # 缺密钥时直接说清楚缺哪个环境变量，而不是等 SDK 抛一个 401
        result["error"] = f"缺少 {cfg['api_key_env']}，无法评测 provider={provider}"
        result["elapsed"] = round(time.perf_counter() - started, 2)
        return result

    with _collect_logs() as records:
        try:
            combined = vision_client.classify_and_transcribe(image_paths, provider=provider)
        except Exception as exc:  # 合并路径抛异常也要退回并行，并如实记录
            combined = None
            result["error"] = f"classify_and_transcribe 抛出异常: {type(exc).__name__}: {exc}"

        if combined:
            result.update({
                # 注意：这里**不**写 `ok`。是不是成功由下面的"可用页数"统一判定（F2）。
                "path": "classify_and_transcribe",
                "vision_mode": combined.get("vision_mode", "combined"),
                "problem_type": combined.get("problem_type") or "",
                "pages": [str(p) for p in combined.get("pages") or []],
                "failed_pages": list(combined.get("failed_pages") or []),
                "continuations": list(combined.get("continuations") or []),
                "refilled": int(combined.get("refilled") or 0),
            })
            # 截断判据优先用**协议级信号**（返回的 `ended`：响应里有没有 `<<<END>>>`），
            # 因为流式响应本身拿不到 finish_reason；没有该信号时才退化到空页代理。
            truncated = _combined_truncation(combined, len(image_paths))
            result["ended"] = truncated.get("ended")
            result["truncation_detail"] = truncated["detail"]
            _apply_combined_truncation(result, truncated)
        else:
            # ---- 回退：并行路径（1 次分类 + N 次 OCR）----
            try:
                problem_type, pages, failed = vision_client.classify_and_transcribe_parallel(
                    image_paths
                )
                result.update({
                    "path": "classify_and_transcribe_parallel",
                    "vision_mode": "parallel",
                    "problem_type": problem_type or "",
                    "pages": [str(p) for p in pages],
                    "failed_pages": list(failed),
                    "continuations": [],
                    "truncation_signal": "unknown",
                    "truncated": False,
                })
                # 并行路径不返回 finish_reason（底层函数没有这个出口）——
                # 记 None 比编一个 "stop" 诚实：报告里会写成 unknown。
                result["finish_reason"] = None
                result["finish_reason_basis"] = "unknown(并行路径不返回 finish_reason)"
                # F3：空页数只是**代理指标**，0 个空页并不能证明"没被截断"
                # （最后一页被切断时每页都非空）。如实记录数字，并标注 signal=unknown，
                # 由 verdict 判为「无法判定」，绝不据此写成满足。
                result["truncated_pages"] = _empty_page_count(result["pages"])
                result["truncation_detail"] = (
                    "并行回退路径没有 finish_reason / <<<END>>> 信号"
                    f"（截断页数按空页代理：{result['truncated_pages']}）"
                )
            except Exception as exc:
                result["error"] = (
                    (result["error"] + " | " if result["error"] else "")
                    + f"并行回退路径也失败: {type(exc).__name__}: {exc}"
                )
                result["truncation_signal"] = "unknown"
                result["truncated_pages"] = 0

        result["log_excerpt"] = _useful_lines(records)

    # F2：`ok` = "真的拿到了可用输出"，而不是"调用没抛异常"。
    # 旧实现无条件写 `ok: True`，于是"所有页都为空 / 一次调用都没成功"的 run 在
    # 单 provider 模式下会被渲染成"调用成功 + 两条硬闸门不适用" → 假 PASS 通过验收。
    usable_pages = _usable_page_count(result["pages"])
    result["usable_pages"] = usable_pages
    result["ok"] = bool(result["path"]) and usable_pages > 0
    if not result["ok"]:
        detail = (
            f"provider={provider} 未产出任何可用内容："
            f"{len(result['pages'])} 页中 {usable_pages} 页非空"
            f"（路径 {result['path'] or '—'}）"
        )
        result["error"] = (result["error"] + " | " if result["error"] else "") + detail

    result["elapsed"] = round(time.perf_counter() - started, 2)
    result["pages_chars"] = [len(p) for p in result["pages"]]
    result["total_chars"] = sum(result["pages_chars"])
    result.setdefault("truncated_pages", 0)
    return result


# ==============================================================================
# 第三部分：比对与指标
# ==============================================================================

def compare_pages(
    baseline_pages: list[str], candidate_pages: list[str]
) -> dict[str, Any]:
    """逐页做要素差：基准 = 指定的基准 provider 的逐页文本（candidate 相对它比）。

    返回：
    - `missing` / `missing_count`：**题目正文**要素缺失（判据，见 `extract_body_elements`）
    - `missing_raw` / `missing_raw_count`：**含页眉/题号的全页**要素缺失（诊断，不做判据）
    - `extra` / `extra_count`、`extra_raw_count`：候选多出的要素（可能是幻觉，也可能是更完整）
    - `chrome_samples`：被当成非题目行剥离的样例行（可人工核对，避免"判据偷偷放水"）
    """
    missing: dict[str, list[list[Any]]] = {key: [] for key, _ in _CATEGORY_LABELS}
    extra: dict[str, list[list[Any]]] = {key: [] for key, _ in _CATEGORY_LABELS}
    missing_raw: dict[str, list[list[Any]]] = {key: [] for key, _ in _CATEGORY_LABELS}
    extra_raw: dict[str, list[list[Any]]] = {key: [] for key, _ in _CATEGORY_LABELS}
    chrome_samples: list[str] = []
    per_page: list[dict[str, Any]] = []

    count = max(len(baseline_pages), len(candidate_pages))
    for index in range(count):
        base_text = baseline_pages[index] if index < len(baseline_pages) else ""
        cand_text = candidate_pages[index] if index < len(candidate_pages) else ""
        # 判据用题目正文；原始计数同时算一份，只作诊断
        base_elements = extract_body_elements(base_text)
        cand_elements = extract_body_elements(cand_text)
        base_all = extract_elements(base_text)
        cand_all = extract_elements(cand_text)
        _body, ignored_base = strip_chrome_lines(base_text)
        _body, ignored_cand = strip_chrome_lines(cand_text)

        page_missing: dict[str, list[str]] = {}
        page_extra: dict[str, list[str]] = {}
        for key, _label in _CATEGORY_LABELS:
            base_set, cand_set = set(base_elements[key]), set(cand_elements[key])
            page_missing[key] = sorted(base_set - cand_set)
            page_extra[key] = sorted(cand_set - base_set)
            for element in page_missing[key]:
                missing[key].append([index + 1, element])
            for element in page_extra[key]:
                extra[key].append([index + 1, element])
            for element in sorted(set(base_all[key]) - set(cand_all[key])):
                missing_raw[key].append([index + 1, element])
            for element in sorted(set(cand_all[key]) - set(base_all[key])):
                extra_raw[key].append([index + 1, element])

        for line in ignored_base + ignored_cand:
            if line and line not in chrome_samples:
                chrome_samples.append(line)

        per_page.append({
            "page": index + 1,
            "chars": len(cand_text),
            "baseline_chars": len(base_text),
            "char_delta": len(cand_text) - len(base_text),
            "missing": page_missing,
            "extra": page_extra,
            "chrome_ignored": len(ignored_base) + len(ignored_cand),
        })

    lengths = [entry["chars"] for entry in per_page]
    baseline_lengths = [entry["baseline_chars"] for entry in per_page]
    return {
        "per_page": per_page,
        "missing": missing,
        "extra": extra,
        "missing_raw": missing_raw,
        "extra_raw": extra_raw,
        "chrome_samples": chrome_samples[:12],
        "missing_count": sum(len(items) for items in missing.values()),
        "missing_raw_count": sum(len(items) for items in missing_raw.values()),
        "extra_count": sum(len(items) for items in extra.values()),
        "extra_raw_count": sum(len(items) for items in extra_raw.values()),
        "chars": sum(lengths),
        "baseline_chars": sum(baseline_lengths),
        "char_delta": sum(lengths) - sum(baseline_lengths),
        "char_delta_ratio": (
            (sum(lengths) - sum(baseline_lengths)) / sum(baseline_lengths)
            if sum(baseline_lengths) else 0.0
        ),
    }


# 兜底单价表（元/百万 token）：**只在 `webapp.accounts` 导不进来时**使用。
#
# 为什么会存在：导入失败（例如评测环境缺 webapp 依赖）不该让整份报告没有费用数字。
# 为什么仍是复制一份而不是从生产表派生：兜底的全部意义就是"生产模块不可用时还能算"，
# 派生会把两者重新绑死、兜底也就没了。
# F9 的教训：这张表曾经落后于迁移（`deepseek-flash` 还是 0.5/2.0 的旧价，成本被低估
# 约 4 倍）。因此这里与 `webapp/accounts.py` 的 COST_TABLE 逐项一致，并由
# `tests/test_vision_ab.py` 的同步用例锁定 —— 单价再变时测试会先红。
_FALLBACK_COST_TABLE: dict[str, tuple[float, float]] = {
    "deepseek-flash": (2.0, 8.0),
    "deepseek-v4-pro": (9.0, 27.0),
    "GLM-4.6V-FlashX": (0.5, 1.5),
    "GLM-4.6V": (2.0, 6.0),
    "default": (2.0, 8.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """按 `webapp/accounts.py` 的 COST_TABLE 估算费用（元）。

    为什么不复制一份单价表：单价是会变的（迁移本身就在改它），复制一份必然漂移。
    这里直接读生产模块；万一带不来（例如缺 webapp 依赖），退化到内置表并**在报告里
    标注**，避免"用了兜底单价却当成生产单价"。
    """
    try:
        from webapp.accounts import estimate_cost as _production_estimate

        return float(_production_estimate(model, input_tokens, output_tokens))
    except Exception:
        in_price, out_price = _FALLBACK_COST_TABLE.get(
            model, _FALLBACK_COST_TABLE["default"]
        )
        return round(
            input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price, 6
        )


def cost_table_source() -> str:
    """报告里要写清单价是从哪来的（可复现性）。"""
    try:
        from webapp.accounts import COST_TABLE  # noqa: F401

        return "webapp.accounts.COST_TABLE（生产表，与计费一致）"
    except Exception as exc:
        return f"内置兜底表（webapp.accounts 不可用: {type(exc).__name__}）"


def _token_estimate(pages: list[str]) -> tuple[int, int]:
    """与生产同口径的 token 估算（复用 webapp.usage，保证与计费一致）。"""
    try:
        from webapp.usage import estimate_tokens

        return estimate_tokens(len(pages), sum(len(p) for p in pages))
    except Exception:
        # 兜底：与 webapp/usage.py 的常量保持一致
        return 600 + len(pages) * 1024, int(sum(len(p) for p in pages) * 0.6)


def enrich(run: dict[str, Any]) -> dict[str, Any]:
    """给一次运行补上：逐页要素、损坏命中、费用估算、页数汇总。"""
    pages = run.get("pages") or []
    run["elements"] = [extract_elements(page) for page in pages]
    run["corruption"] = [detect_corruption(page) for page in pages]
    corrupt_pages = [index + 1 for index, hits in enumerate(run["corruption"]) if hits]
    run["corrupt_pages"] = corrupt_pages
    run["corruption_count"] = sum(len(hits) for hits in run["corruption"])
    run["page_count"] = len(pages)
    run["nonempty_pages"] = sum(1 for page in pages if page.strip())
    run["total_chars"] = sum(len(page) for page in pages)
    run["avg_page_chars"] = round(run["total_chars"] / len(pages), 1) if pages else 0.0

    in_tokens, out_tokens = _token_estimate(pages)
    run["input_tokens_est"] = in_tokens
    run["output_tokens_est"] = out_tokens
    run["cost_yuan_est"] = estimate_cost(run.get("model", ""), in_tokens, out_tokens)
    return run


def classify_agreement(reference: str, candidate: str) -> dict[str, Any]:
    """两家题型分类是否一致（S2）。

    只有单张图/单次调用，所以这里的一致率是 0 / 1 的离散值；报告里同时给出
    混淆矩阵形态（reference → candidate），多图批跑时才能看出偏差方向。
    """
    ref = (reference or "").strip().upper()
    cand = (candidate or "").strip().upper()
    return {
        "reference": ref,
        "candidate": cand,
        "agree": bool(ref) and ref == cand,
        "rate": 1.0 if (ref and ref == cand) else 0.0,
        "matrix": {ref or "<空>": {cand or "<空>": 1}},
    }


# ==============================================================================
# 第四部分：图片发现与报告渲染
# ==============================================================================

def discover_images(target: Path, limit: int | None) -> list[Path]:
    """把 `-i` 展开成按拍摄顺序排好的图片列表（复用生产排序，保证页序与生产一致）。

    `-i` 是目录时递归收集（uploads 下每个任务一个子目录），并按拍摄时间排序 ——
    生产多图任务就是这么定页序的，评测必须用同一套顺序，否则比对的是错位的两页。
    """
    if target.is_file():
        images = [target]
    elif target.is_dir():
        images = [
            path for path in sorted(target.rglob("*"))
            if path.is_file() and path.suffix.lower() in config.ALLOWED_EXTENSIONS
        ]
    else:
        raise FileNotFoundError(f"输入路径不存在: {target}")

    if not images:
        raise FileNotFoundError(f"输入路径下没有可识别的图片: {target}")
    images = sort_images_by_time(images)
    if limit is not None:
        images = images[:limit]
    return images


def _describe_images(images: list[Path]) -> list[dict[str, Any]]:
    described = []
    for path in images:
        timestamp, source = photo_timestamp(path)
        described.append({
            "name": path.name,
            "path": str(path),
            "order_source": source,
            "timestamp": datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
            if timestamp else "",
        })
    return described


def render_report(payload: dict[str, Any], comparisons: dict[str, dict[str, Any]],
                  verdict: dict[str, Any], corrupt_detail: dict[str, Any]) -> str:
    """渲染 report.md。

    结构固定为五段：元信息 → 结论 → 指标 → 损坏/截断明细 → 逐页并排全文。
    结论放最前面，因为这份报告的读者是"决定要不要切 provider 的人"，
    他不会从几千行转录里自己算指标。
    """
    runs: dict[str, dict[str, Any]] = payload["runs"]
    order: list[str] = payload["providers"]
    reference: str | None = payload.get("reference")
    lines: list[str] = []
    add = lines.append

    add("# 视觉层 A/B 报告")
    add("")
    add(f"- 生成时间：{payload['created']}")
    add(f"- 图片数：{len(payload['images'])}（按拍摄顺序，来源见下表）")
    add(f"- provider：{', '.join(order)}"
        + (f"（基准：{reference}）" if reference else ""))
    add(f"- VISION_PROVIDER 环境值：`{payload['vision_provider_env']}`"
        f"（本次评测**不改动**它，只按 `provider=` 透传）")
    add(f"- 单价来源：{payload['cost_source']}")
    add("")
    add("| # | 图片 | 排序依据 | 拍摄时间 |")
    add("|---|---|---|---|")
    for index, entry in enumerate(payload["images"], start=1):
        add(f"| {index} | {entry['name']} | {entry['order_source']} | {entry['timestamp']} |")
    add("")

    # ---- 结论 ----
    add("## 结论")
    add("")
    add(f"**验收标准**：要素缺失必须为 0；题型分类一致率 ≥ 90%；截断页数必须为 0；"
        f"LaTeX 静默损坏必须为 0。")
    add("")
    add(f"**判定：{'✅ 满足' if verdict['pass'] else '❌ 不满足'}**")
    add("")
    for item in verdict["items"]:
        add(f"- {item['mark']} {item['label']}：{item['detail']}")
    add("")
    if verdict["notes"]:
        for note in verdict["notes"]:
            add(f"> {note}")
        add("")

    # ---- 指标 ----
    add("## 自动指标")
    add("")
    header = "| 指标 | " + " | ".join(
        f"{name}（{runs[name]['model']}）" for name in order
    ) + " |"
    add(header)
    add("|" + "---|" * (len(order) + 1))
    metric_rows = (
        ("成功", lambda run: "是" if run["ok"] else "**否**"),
        ("调用路径", lambda run: f"`{run['path'] or '—'}`"),
        ("vision_mode", lambda run: f"`{run['vision_mode'] or '—'}`"),
        ("题型", lambda run: run["problem_type"] or "—"),
        ("页数（非空/总）", lambda run: f"{run['nonempty_pages']}/{run['page_count']}"),
        ("失败页", lambda run: _fmt_indices(run["failed_pages"])),
        ("补做页数", lambda run: str(run.get("refilled", 0))),
        ("转录字符数", lambda run: f"{run['total_chars']}"),
        ("平均每页字符", lambda run: f"{run['avg_page_chars']}"),
        ("耗时（秒）", lambda run: f"{run['elapsed']:.2f}"),
        ("finish_reason", lambda run: run.get("finish_reason") or "unknown"),
        ("截断页数", lambda run: str(run.get("truncated_pages", 0))),
        ("LaTeX 损坏命中", lambda run: (
            f"{run['corruption_count']}（页 {_fmt_indices(run['corrupt_pages'])}）"
            if run["corrupt_pages"] else f"{run['corruption_count']}"
        )),
        ("估算费用（元）", lambda run: f"{run['cost_yuan_est']:.6f}"),
        ("估算 token（入/出）",
         lambda run: f"{run['input_tokens_est']}/{run['output_tokens_est']}"),
    )
    for label, getter in metric_rows:
        cells = []
        for name in order:
            try:
                cells.append(str(getter(runs[name])))
            except Exception as exc:  # 指标渲染不该中断整份报告
                cells.append(f"n/a({type(exc).__name__})")
        add(f"| {label} | " + " | ".join(cells) + " |")
    add("")

    # 对比指标（有基准才算）
    if reference and comparisons:
        add("### 相对基准的差异")
        add("")
        add("| 指标 | " + " | ".join(
            f"{name} vs {reference}" for name in order if name != reference
        ) + " |")
        add("|" + "---|" * len([n for n in order if n != reference]) + "---|")
        rows = (
            ("字符数差（候选-基准）", lambda c: f"{c['char_delta']:+d}"),
            ("字符数差比例", lambda c: f"{c['char_delta_ratio']:+.1%}"),
            ("**要素缺失数（题目正文，判据）**", lambda c: f"**{c['missing_count']}**"),
            ("要素缺失数（全页，含页眉/题号，仅诊断）",
             lambda c: f"{c['missing_raw_count']}"),
            ("要素多出数（题目正文）", lambda c: f"{c['extra_count']}"),
        )
        for label, getter in rows:
            cells = [str(getter(comparisons[name])) for name in order if name != reference]
            add(f"| {label} | " + " | ".join(cells) + " |")
        add("")
        # 判据只覆盖"题目正文"，因此被剥离的行必须列出来，避免看起来像偷偷放水
        chrome_seen: list[str] = []
        for name in order:
            if name != reference and name in comparisons:
                for sample in comparisons[name]["chrome_samples"]:
                    if sample not in chrome_seen:
                        chrome_seen.append(sample)
        if chrome_seen:
            add("> 判据已剥离的非题目行（页眉 / 页脚 / 题号导航，样例）："
                + "、".join(f"`{sample}`" for sample in chrome_seen[:6]) + "…")
            add(">")
            add("> `要素缺失数（全页…）` 是同一批文本**未剥离**时的计数，用来核对剥离没有掩盖"
                "正文缺失：两者相差部分即页眉/题号差异。")
            add("")
        # 要素缺失按类别 + 具体元素列出：只有"缺了什么"才能判断严重性
        for name in order:
            if name == reference or name not in comparisons:
                continue
            found = False
            for key, label in _CATEGORY_LABELS:
                items = comparisons[name]["missing"].get(key) or []
                if not items:
                    continue
                if not found:
                    add(f"**{name} 相对 {reference} 缺失的要素：**")
                    add("")
                    found = True
                add(f"- {label}：" + "、".join(f"第{p}页 `{v}`" for p, v in items))
            if found:
                add("")
            extras = []
            for key, label in _CATEGORY_LABELS:
                for page, value in comparisons[name]["extra"].get(key) or []:
                    extras.append(f"第{page}页 {label} `{value}`")
            if extras:
                add(f"{name} 多出的要素（参考，可能是幻觉或更完整）："
                    + "、".join(extras[:20]) + ("…" if len(extras) > 20 else ""))
                add("")

    # 分类一致率
    add("### 题型分类一致率（S2）")
    add("")
    if reference and comparisons:
        ref_type = runs[reference]["problem_type"] or "<空>"
        add(f"- 基准 {reference}：`{ref_type}`")
        for name in order:
            if name == reference:
                continue
            cand = runs[name]["problem_type"] or "<空>"
            mark = "✅ 一致" if cand == ref_type else "❌ 不一致"
            add(f"- {name}：`{cand}` → {mark}"
                f"（本轮一致率 {'100%' if cand == ref_type else '0%'}；"
                f"混淆矩阵：`{ref_type} → {cand}`）")
        add("")
        add("> 单次调用的一致率只有 0/1 两档。要做成统计意义上的 ≥90%，"
            "需要多组题图（≥10 次独立调用）重复本命令后汇总 result.json。")
    else:
        add("- 单 provider 模式：没有基准，**无法计算一致率**（本项判为不适用）。"
            "需要比一致率请加 `--reference <另一个 provider>`。")
    add("")

    # ---- 损坏与截断明细 ----
    add("## LaTeX 静默损坏与截断明细（S12）")
    add("")
    for name in order:
        run = runs[name]
        add(f"### {name}")
        add("")
        if run["corruption_count"]:
            for page, hits in enumerate(run["corruption"], start=1):
                for hit in hits:
                    add(f"- ❌ 第 {page} 页 [{hit['kind']}] {hit['name']}"
                        f"（第 {hit['line']} 行）：`{hit['snippet']}`")
        else:
            add("- ✅ 未发现控制字符或落转义残片")
        add("")
        # 逐页首尾各 60 字符摘要：这是"没有额外花了钱的截断检测"的可见痕迹
        add(f"- finish_reason：`{run.get('finish_reason') or 'unknown'}`"
            f"（依据：{run.get('finish_reason_basis', '—')}）")
        if run.get("truncation_detail"):
            # 三种状态要分开写：并行回退路径没有协议级信号，`truncated_pages=0`
            # 只是"空页代理为 0"，不能渲染成"未被截断"（F3）。
            if run.get("truncation_signal") == "unknown":
                state = " → **无法判定是否被截断**"
            else:
                state = (" → **判定为被截断**" if run.get("truncated_pages")
                         else " → 未被截断")
            add(f"- 协议级截断检查：{run['truncation_detail']}{state}")
        if run.get("failed_pages"):
            add(f"- 失败/空页：{_fmt_indices(run['failed_pages'])}")
        if run.get("error"):
            add(f"- ⚠️ 错误原文：`{run['error']}`")
        if run.get("log_excerpt"):
            add("")
            add("<details><summary>本轮日志中的错误/回退行（原始记录）</summary>")
            add("")
            add("```text")
            for line in run["log_excerpt"]:
                add(line)
            add("```")
            add("</details>")
        add("")

    if corrupt_detail.get("tail_head"):
        add("### 每页首尾摘要（人工快速扫一眼是否被切断）")
        add("")
        for name in order:
            run = runs[name]
            add(f"**{name}**")
            add("")
            for index, page in enumerate(run["pages"], start=1):
                head = sanitize_controls(page[:60]).replace("\n", "⏎")
                tail = sanitize_controls(page[-60:]).replace("\n", "⏎")
                add(f"- 第 {index} 页：`{head}` … `{tail}`")
            add("")

    # ---- 逐页全文并排 ----
    add("## 逐页转录全文（两版并排，人工比对）")
    add("")
    count = max((len(runs[name]["pages"]) for name in order), default=0)
    for index in range(count):
        add(f"### 第 {index + 1} 页（{payload['images'][index]['name']}）"
            if index < len(payload["images"]) else f"### 第 {index + 1} 页")
        add("")
        for name in order:
            run = runs[name]
            page = run["pages"][index] if index < len(run["pages"]) else ""
            add(f"<details open><summary>{name}（{len(page)} 字符）</summary>")
            add("")
            add("````text")
            add(page)
            add("````")
            add("</details>")
            add("")
    return "\n".join(lines)


def _fmt_indices(indices: list[int]) -> str:
    if not indices:
        return "—"
    return ", ".join(str(i + 1) for i in indices)


# ==============================================================================
# 第五部分：CLI
# ==============================================================================

def _resolve_provider(name: str) -> str:
    """把用户输入的 provider 名解析成"生效名"。

    F12：注解曾经写成 `tuple[str, bool]`（多出一个早已不存在的 flag 返回值），
    与真实的单值返回不符 —— 调用方 `main()` 也只接一个值。这里改成 `str`。

    为什么不照 `config` 的"未知值回落 deepseek"规则：那是给服务用的（配置写错不该
    让服务起不来），而 A/B 评测里 provider 名写错会让整轮实测**测错模型还不自知**。
    因此这里显式报错（由 argparse 输出可选项），绝不静默回落。
    """
    cleaned = (name or "").strip().lower()
    if cleaned not in config.VISION_PROVIDER_CONFIG:
        # 用 argparse 的 error 出口，比抛栈更容易看懂
        raise argparse.ArgumentTypeError(
            f"未知 provider '{name}'，可选：{' / '.join(config.VISION_PROVIDER_CONFIG)}"
        )
    return cleaned


def _build_verdict(
    runs: dict[str, dict[str, Any]],
    order: list[str],
    reference: str | None,
    comparisons: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """按验收标准给出"是否满足"的明确结论。

    验收三条（迁移文档第 8 节）：要素缺失必须 0、分类一致率 ≥90%、截断页数必须 0。
    另外把 S12（无 LaTeX 损坏）也并入判定 —— 它是"转录可用"的前提。
    """
    items: list[dict[str, str]] = []
    notes: list[str] = []
    overall = True

    # ① 要素缺失
    reference_run = runs.get(reference) if reference else None
    reference_pages = list((reference_run or {}).get("pages") or [])
    reference_usable = _usable_page_count(reference_pages)
    if reference and reference_run is None:
        items.append({
            "mark": "❌",
            "label": "要素缺失（数字/选项/LaTeX 片段）",
            "detail": f"无法判定：基准 {reference} 不在本次运行结果里，没有比对基准",
        })
        notes.append("基准 provider 不在 runs 里（调用方参数不一致），本项无法比对、判为不满足。")
        overall = False
    elif reference and comparisons and reference_usable:
        worst = max(comparisons.values(), key=lambda c: c["missing_count"], default=None)
        missing = worst["missing_count"] if worst else 0
        raw_missing = max(
            (c["missing_raw_count"] for c in comparisons.values()), default=0
        )
        ok = missing == 0
        detail = (f"相对基准 {reference} 缺失 {missing} 个"
                  + ("，满足「必须为 0」" if ok else "，**不满足**「必须为 0」"))
        if raw_missing > missing:
            detail += (f"（判据只数**题目正文**要素；全页计数为 {raw_missing} 个，"
                       f"其中 {raw_missing - missing} 个来自页眉/题号等非题目行，已单列并可见）")
        items.append({
            "mark": "✅" if ok else "❌",
            "label": "要素缺失（题干/选项/公式，题目正文）",
            "detail": detail,
        })
        overall &= ok
    elif reference and comparisons:
        # F2：基准 provider 全是空页时，"缺失 0"只是"没得比"，不是"没缺" —— 空基准下
        # 任何候选的要素差都是 0，照旧打 ✅ 就等于用"没有基准"伪造一个"要素零缺失"。
        items.append({
            "mark": "❌",
            "label": "要素缺失（题干/选项/公式，题目正文）",
            "detail": (f"无法判定：基准 {reference} 没有任何非空页"
                       f"（{len(reference_pages)} 页全空），"
                       "空基准下「缺失 0」不构成满足验收标准的证据"),
        })
        notes.append(f"基准 {reference} 没有产出可用文本，跨 provider 的要素缺失比对无法进行；"
                     "本项判为**不满足**（而不是 0 缺失），请先让基准 provider 跑出结果。")
        overall = False
    else:
        items.append({
            "mark": "➖",
            "label": "要素缺失",
            "detail": "单 provider 模式无基准，本项不适用（加 `--reference <provider>` 才有对比）",
        })
        notes.append("本次只跑了一家 provider，无法给出跨 provider 的要素缺失/一致率结论；"
                     "正式验收（S1/S2）请跑双 provider 或加 --reference。")

    # ② 分类一致率
    # 先查"有没有分类输出"：题型为空时连一致率的分母都不存在。单 provider 模式下也不能
    # 把"没有分类"当成"不适用" —— 那会与 F2 一样，用缺失的输出换一个 PASS。
    empty_types = [
        name for name in order if not str(runs[name].get("problem_type") or "").strip()
    ]
    if empty_types:
        items.append({
            "mark": "❌",
            "label": "题型分类一致率",
            "detail": ("无法判定：" + "、".join(empty_types)
                       + " 的题型为空（没有分类输出），本项判为不满足"),
        })
        overall = False
    elif reference and len(order) > 1:
        rates = [
            classify_agreement(
                runs[reference]["problem_type"], runs[name]["problem_type"]
            )["rate"]
            for name in order if name != reference
        ]
        rate = sum(rates) / len(rates) if rates else 0.0
        ok = rate >= 0.9
        items.append({
            "mark": "✅" if ok else "❌",
            "label": "题型分类一致率",
            "detail": (f"{rate:.0%}（基准 {reference}），标准 ≥90%"
                       + ("，满足" if ok else "，**不满足**")),
        })
        overall &= ok
        notes.append("一致率是单次调用的 0/1 结果，样本量为 1 时不足以代表整体；"
                     "需多组题图重复本命令再汇总 result.json 才能得统计结论。")
    else:
        items.append({
            "mark": "➖",
            "label": "题型分类一致率",
            "detail": "单 provider 模式不适用（需 --reference）",
        })

    # ③ 截断页数
    trunc = {name: runs[name].get("truncated_pages", 0) for name in order}
    worst_trunc = max(trunc.values()) if trunc else 0
    # 哪些 provider 的截断状态根本无从判断：并行回退路径没有 finish_reason、也没有
    # 协议级 `<<<END>>>`，空页代理为 0 **不能**证明"没被截断"（F3）—— 这类情况必须
    # 判为无法判定，而不是把 0 当成满足。
    unknown = [
        name for name in order
        if runs[name].get("truncation_signal") == "unknown"
        or runs[name].get("finish_reason") is None
    ]
    if unknown:
        items.append({
            "mark": "❌",
            "label": "max_tokens 截断页数",
            "detail": ("、".join(f"{name}={value}" for name, value in trunc.items())
                       + "；但 " + "、".join(unknown)
                       + " 没有 finish_reason / <<<END>>> 信号，空页代理为 0 也"
                         "不能证明未被截断 → **无法判定，不计为满足**"),
        })
        overall = False
        notes.append("以下 provider 走了并行回退路径，底层不返回 finish_reason，"
                     "截断状态无法判定（本项不判满足）：" + "、".join(unknown) + "。")
    else:
        ok = worst_trunc == 0
        items.append({
            "mark": "✅" if ok else "❌",
            "label": "max_tokens 截断页数",
            "detail": ("、".join(f"{name}={value}" for name, value in trunc.items())
                       + ("，满足「必须为 0」" if ok else "，**不满足**「必须为 0」")),
        })
        overall &= ok

    # ④ LaTeX 静默损坏（S12）
    corrupt = {name: runs[name]["corruption_count"] for name in order}
    worst_corrupt = max(corrupt.values()) if corrupt else 0
    ok = worst_corrupt == 0
    items.append({
        "mark": "✅" if ok else "❌",
        "label": "LaTeX 静默损坏命中",
        "detail": ("、".join(f"{name}={value}" for name, value in corrupt.items())
                   + ("，满足「无损坏」" if ok else "，**存在损坏**")),
    })
    overall &= ok

    # ⑤ 路径可用性（任一 provider 跑不通 / 没有可用输出，结论都不能算通过）
    # 判定标准写两遍（ok 与"非空页数"）：`ok` 已经由 run_provider 按可用页数算好，
    # 这里再独立看一次页正文 —— 万一将来有人把 `ok` 又改回无条件 True，只要转录是空的，
    # 结论仍然失败。这是 F2 的纵深防御。
    failed = [
        name for name in order
        if not runs[name]["ok"] or _usable_page_count(runs[name].get("pages")) == 0
    ]
    if failed:
        overall = False
        reasons = []
        for name in failed:
            reason = str(runs[name].get("error") or "").replace("\n", " ").strip()
            if not reason:
                pages = list(runs[name].get("pages") or [])
                reason = f"无可用输出（{len(pages)} 页中 {_usable_page_count(pages)} 页非空）"
            if len(reason) > 120:
                reason = reason[:120] + "…"
            reasons.append(f"{name}（{reason}）")
        items.append({
            "mark": "❌",
            "label": "provider 有可用输出",
            # F2：ok=False 的 provider 绝不写成"调用成功"，并把它自己的原因带出来
            "detail": "没有产出可用内容的 provider：" + "、".join(reasons),
        })
    else:
        items.append({
            "mark": "✅",
            "label": "provider 有可用输出",
            "detail": "、".join(
                f"{name} 走 `{runs[name]['path']}`"
                f"（{_usable_page_count(runs[name].get('pages'))} 页有内容，"
                f"{runs[name]['elapsed']:.1f}s）"
                for name in order
            ),
        })

    return {"pass": bool(overall), "items": items, "notes": notes}


def build_payload(images: list[Path], providers: list[str], reference: str | None) -> dict[str, Any]:
    """跑评测并组装 payload（不含渲染）。"""
    runs: dict[str, dict[str, Any]] = {}
    for name in providers:
        print(f"[{name}] 开始 OCR（{len(images)} 张图）…", flush=True)
        run = enrich(run_provider(name, images))
        runs[name] = run
        status = "成功" if run["ok"] else "失败"
        print(
            f"[{name}] {status}：路径={run['path'] or '—'} 模式={run['vision_mode'] or '—'} "
            f"题型={run['problem_type'] or '—'} 字符={run['total_chars']} "
            f"耗时={run['elapsed']:.1f}s 费用≈{run['cost_yuan_est']:.6f}元",
            flush=True,
        )
        if run.get("error"):
            print(f"[{name}] 错误原文：{run['error']}", flush=True)

    comparisons: dict[str, dict[str, Any]] = {}
    if reference and reference in runs:
        for name in providers:
            if name == reference:
                continue
            comparisons[name] = compare_pages(runs[reference]["pages"], runs[name]["pages"])
    # 截断页数已由 run_provider 判定（协议级信号优先），这里只兜底补默认值
    for name in providers:
        runs[name].setdefault("truncated_pages", 0)

    return {
        "created": datetime.now().isoformat(timespec="seconds"),
        "stamp": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "images": _describe_images(images),
        "providers": providers,
        "reference": reference,
        "vision_provider_env": config.VISION_PROVIDER,
        "cost_source": cost_table_source(),
        "cost_table": _cost_table_snapshot(),
        "config_snapshot": {
            "VISION_MAX_TOKENS": config.VISION_MAX_TOKENS,
            "VISION_COMBINED_TIMEOUT": config.VISION_COMBINED_TIMEOUT,
            "VISION_DISABLE_THINKING": config.VISION_DISABLE_THINKING,
            "VISION_INLINE_MERGE": config.VISION_INLINE_MERGE,
            "COMBINED_VISION_MAX_IMAGES": config.COMBINED_VISION_MAX_IMAGES,
            "USE_COMBINED_VISION_CALL": config.USE_COMBINED_VISION_CALL,
            "OCR_PARALLEL_WORKERS": config.OCR_PARALLEL_WORKERS,
            "FILENAME_MODE": config.FILENAME_MODE,
        },
        "runs": runs,
    }


def _cost_table_snapshot() -> dict[str, list[float]]:
    try:
        from webapp.accounts import COST_TABLE

        return {name: [inp, out] for name, (inp, out) in COST_TABLE.items()}
    except Exception:
        return {}


def write_outputs(payload: dict[str, Any], comparisons: dict[str, dict[str, Any]],
                  verdict: dict[str, Any], corrupt_detail: dict[str, Any]) -> Path:
    out_dir = REPORT_ROOT / payload["stamp"]
    out_dir.mkdir(parents=True, exist_ok=True)

    report = render_report(payload, comparisons, verdict, corrupt_detail)
    (out_dir / "report.md").write_text(report, encoding="utf-8")

    # result.json 只放结构化事实（逐页全文放 json 会让文件巨大且没人读；
    # 需要全文的场景直接看 report.md 或下面的 pages.txt）
    result = {
        "created": payload["created"],
        "images": payload["images"],
        "providers": payload["providers"],
        "reference": payload["reference"],
        "vision_provider_env": payload["vision_provider_env"],
        "cost_source": payload["cost_source"],
        "cost_table": payload["cost_table"],
        "config_snapshot": payload["config_snapshot"],
        "runs": {
            name: {key: value for key, value in run.items() if key != "pages"}
            | {"pages_chars": run.get("pages_chars", []),
               "pages_head": [page[:200] for page in run.get("pages", [])]}
            for name, run in payload["runs"].items()
        },
        "comparisons": comparisons,
        "verdict": verdict,
    }
    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 逐页全文单独存：既方便 diff 工具直接比，也避免 json 里塞长文本
    for name, run in payload["runs"].items():
        lines = []
        for index, page in enumerate(run.get("pages", []), start=1):
            lines.append(f"===== PAGE {index} =====")
            lines.append(page)
        (out_dir / f"{name}.pages.txt").write_text("\n".join(lines), encoding="utf-8")
        if run.get("log_excerpt"):
            (out_dir / f"{name}.log").write_text(
                "\n".join(run["log_excerpt"]), encoding="utf-8"
            )
    return out_dir


def cmd_check(args: argparse.Namespace) -> int:
    target = Path(args.input)
    limit = args.images if args.images and args.images > 0 else None

    providers = [args.provider] if args.provider else list(config.VISION_PROVIDER_CONFIG)
    # 默认 base = 当前生效 provider（默认 deepseek），保证报告里"基准"语义稳定
    base = args.base if args.base else (args.provider or config.VISION_PROVIDER)
    reference = base if len(providers) > 1 else args.reference
    if reference and reference not in providers:
        providers.append(reference)

    try:
        images = discover_images(target, limit)
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    print("=" * 66)
    print("视觉层 A/B 评测")
    print("=" * 66)
    print(f"输入      : {target}")
    print(f"图片      : {len(images)} 张 -> {', '.join(p.name for p in images)}")
    print(f"provider  : {', '.join(providers)}（基准 {reference or '无'}）")
    print(f"当前 .env : VISION_PROVIDER={config.VISION_PROVIDER}"
          f"（评测不修改它，只按 provider= 透传）")
    print(f"关键配置  : max_tokens={config.VISION_MAX_TOKENS} "
          f"合并超时={config.VISION_COMBINED_TIMEOUT:g}s "
          f"关思考={config.VISION_DISABLE_THINKING} "
          f"合并上限={config.COMBINED_VISION_MAX_IMAGES}")
    print("=" * 66)

    payload = build_payload(images, providers, reference)
    comparisons: dict[str, dict[str, Any]] = {}
    if reference and reference in payload["runs"]:
        for name in providers:
            if name != reference:
                comparisons[name] = compare_pages(
                    payload["runs"][reference]["pages"], payload["runs"][name]["pages"]
                )
    verdict = _build_verdict(payload["runs"], providers, reference, comparisons)
    out_dir = write_outputs(payload, comparisons, verdict, {"tail_head": True})

    print()
    print("=" * 66)
    print(f"结论：{'✅ 满足验收标准' if verdict['pass'] else '❌ 不满足验收标准'}")
    for item in verdict["items"]:
        print(f"  {item['mark']} {item['label']}：{item['detail']}")
    print(f"报告：{out_dir / 'report.md'}")
    print(f"数据：{out_dir / 'result.json'}")
    print("=" * 66)
    return 0 if verdict["pass"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.vision_ab",
        description="视觉层 A/B 评测（S1 OCR 不倒退 / S2 分类一致率 / S12 无 LaTeX 静默损坏）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  py -3.10 -m tools.vision_ab check -i webapp/uploads\n"
            "  py -3.10 -m tools.vision_ab check -i webapp/uploads --provider deepseek --images 2\n"
            "  py -3.10 -m tools.vision_ab check -i webapp/uploads/20260912-011924-edc4 "
            "--provider deepseek --reference zhipu\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="对同一组图各 provider 跑一遍 OCR 并出报告")
    check.add_argument("-i", "--input", default=DEFAULT_INPUT,
                       help=f"图片文件或目录（默认 {DEFAULT_INPUT}）")
    check.add_argument("--provider", type=str, default=None,
                       help="只跑这一家（默认跑 config 里的全部 provider）")
    check.add_argument("--reference", type=str, default=None,
                       help="单 provider 模式下额外跑这一家作为对比基准（会多一次真实调用）")
    check.add_argument("--base", type=str, default=None,
                       help="多 provider 模式下的基准名（默认取当前 VISION_PROVIDER）")
    check.add_argument("--images", type=int, default=None,
                       help="最多评测几张图（控制费用；默认全部）")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # provider 名校验放在这里统一做，避免子命令里散落重复的检查
    try:
        if args.provider:
            args.provider = _resolve_provider(args.provider)
        if args.reference:
            args.reference = _resolve_provider(args.reference)
        if args.base:
            args.base = _resolve_provider(args.base)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
