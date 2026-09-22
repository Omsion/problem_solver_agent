"""
test_vision_client.py - 视觉客户端的合并调用闸门、PAGE 协议解析与截断诊断

背景（2026-09-13 事故复盘 → 2026-09-20 迁移）：
- 合并调用（一次请求同时拿到题型 + 全部逐页转录）**曾经**用 JSON 协议，要求把
  所有图片的完整转录塞进一个 JSON，而输出上限只有 8192 token；多图时必然被截断
  → 解析失败 → 回退，白等约 50 秒且照样计费（用量流水：12 次尝试只成功 1 次）。
  于是 `COMBINED_VISION_MAX_IMAGES` 一度被压到 1（多图连请求都不发）。
- 迁移后协议换成 `<<<PAGE n|NEW/CONT>>>` 分隔符 + 流式 + 32768 输出上限，
  上限恢复到 8：**多图应当合并**。所以本文件里"多图跳过"的用例语义被改写，
  并新增协议解析用例（含 S12 的 LaTeX 反斜杠回归保护）。

JSON 与 LaTeX 天然互斥，这是换协议的全部理由：模型漏转义时 `\\frac`→`\\f`
（formfeed）、`\\begin`→`\\b`（backspace）、`\\theta`→`\\t`（tab）、`\\neq`→`\\n`
都是**合法转义**，解析"成功"但正文被静默破坏，还会通过页数/长度校验直接写进解答文件。

运行：pytest tests/test_vision_client.py -v
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from problem_solver_agent import config, prompts, vision_client


def _request() -> httpx.Request:
    """构造一个假的请求对象：`APIConnectionError` 需要一个 request（不会真发出去）。"""
    return httpx.Request("POST", "https://api.deepseek.com/chat/completions")


# ---------------------------------------------------------------------------
# 闸门
# ---------------------------------------------------------------------------


def test_auto_mode_only_allows_small_groups(monkeypatch):
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "COMBINED_VISION_MAX_IMAGES", 1)

    assert vision_client.combined_call_enabled(1) is True
    assert vision_client.combined_call_enabled(2) is False
    assert vision_client.combined_call_enabled(7) is False


def test_default_ceiling_now_allows_eight_image_group(monkeypatch):
    """新默认值的语义：`auto` 模式下 ≤8 张图就合并（旧默认 1 = 多图直接放弃）。

    S8 要求 8 图任务的视觉调用次数为 1，前提就是这个上限是 8。
    """
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")

    assert config.COMBINED_VISION_MAX_IMAGES == 8
    assert vision_client.combined_call_enabled(1) is True
    assert vision_client.combined_call_enabled(8) is True
    # 超过上限仍然不走合并（避免单次请求装下无限转录）
    assert vision_client.combined_call_enabled(9) is False


def test_true_and_false_modes_override_image_count(monkeypatch):
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "true")
    assert vision_client.combined_call_enabled(9) is True

    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "false")
    assert vision_client.combined_call_enabled(1) is False


def test_explicit_low_ceiling_still_skips_the_combined_request(tmp_path, monkeypatch):
    """显式把上限压到 1（灰度/排障配置）时，多图仍然**连请求都不发**。

    这是旧默认值下的正确行为，只是不再是默认值——保留它证明这个闸门还能用。
    """
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "COMBINED_VISION_MAX_IMAGES", 1)

    calls: list = []
    monkeypatch.setattr(
        vision_client, "_call_vision_api", lambda *a, **k: calls.append(a) or "{}"
    )

    images = [tmp_path / f"{i}.jpg" for i in range(4)]
    assert vision_client.classify_and_transcribe(images) is None
    assert calls == []


# ---------------------------------------------------------------------------
# 合并调用：协议、流式与返回值形状
# ---------------------------------------------------------------------------


def _chunks(text: str, finish_reason: str | None = "stop") -> list:
    """构造一个只有一个 chunk 的流式响应（够用：收集器只关心 delta/finish_reason）。"""
    delta = SimpleNamespace(content=text, reasoning_content=None)
    return [SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])]


def _fake_vision_call(
    calls: list[dict],
    stream_text: str,
    *,
    non_stream_text: str | None = None,
    finish_reason: str | None = "stop",
):
    """假的 `_call_vision_api`：记录调用参数，按 stream 返回流或字符串。

    记录 `prompt` 是为了断言"回退链每级用的是哪份 prompt"（PAGE / JSON / 单页转录）。
    """

    def _call(
        image_paths,
        user_prompt,
        model_name,
        stream=False,
        extra_params=None,
        provider=None,
        timeout=None,
    ):
        calls.append(
            {
                "images": list(image_paths),
                "prompt": user_prompt,
                "model": model_name,
                "stream": stream,
                "extra_params": extra_params,
                "provider": provider,
                "timeout": timeout,
            }
        )
        if stream:
            return iter(_chunks(stream_text, finish_reason))
        return non_stream_text

    return _call


def _page_raw(pages: str, type_label: str | None = "MULTIPLE_CHOICE", *, end: bool = True) -> str:
    parts = []
    if type_label is not None:
        parts.append(f"<<<TYPE>>>{type_label}\n")
    parts.append(pages)
    if end:
        parts.append("<<<END>>>\n")
    return "".join(parts)


def test_single_image_still_uses_the_combined_call(tmp_path, monkeypatch):
    """单图仍然走合并调用，但返回值是迁移后的形状（多了 5 个键）。"""
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "COMBINED_VISION_MAX_IMAGES", 8)
    raw = _page_raw("<<<PAGE 1|NEW>>>\n题面内容\n")

    calls: list[dict] = []
    monkeypatch.setattr(vision_client, "_call_vision_api", _fake_vision_call(calls, raw))

    result = vision_client.classify_and_transcribe([tmp_path / "1.jpg"])

    assert result == {
        "problem_type": "MULTIPLE_CHOICE",
        "pages": ["题面内容"],
        "failed_pages": [],
        "continuations": [False],
        "vision_mode": "combined",
        "refilled": 0,
        # 协议级截断信号：`<<<END>>>` 出现过。流式下拿不到 finish_reason，
        # 因此 webapp 告警与 A/B 报告靠它判断"转录是否被 max_tokens 截断"。
        "ended": True,
        # 单批 → 1 次请求；PAGE 协议给了 NEW/CONT（seams=True）→ 可本地内联拼接
        "calls": 1,
        "seams": True,
    }
    # 合并调用必须是流式（非流式要在单个超时内生成完 6–16K token，几乎必然超时），
    # 且用合并专用超时而不是逐页 OCR 的 VISION_TIMEOUT
    assert calls[0]["stream"] is True
    assert calls[0]["timeout"] == config.VISION_COMBINED_TIMEOUT
    assert calls[0]["prompt"] == prompts.CLASSIFY_AND_TRANSCRIBE_PROMPT


def test_missing_end_marker_is_reported_as_not_ended(tmp_path, monkeypatch):
    """缺 `<<<END>>>` 必须在返回值里体现（`ended=False`），并留下截断告警。

    流式下拿不到 `finish_reason`（`_call_vision_api(stream=True)` 只 yield 文本），
    所以 `ended` 是判断"转录被 max_tokens 截断"的**唯一**协议级信号；webapp 告警与
    A/B 报告的"截断页数必须为 0"都依赖它。这里锁住它真的会传出来。
    """
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "COMBINED_VISION_MAX_IMAGES", 8)
    # 有页标记、但没有收尾标记 —— 正是"被截断在最后一页中间"的样子
    raw = _page_raw("<<<PAGE 1|NEW>>>\n第 1 页\n<<<PAGE 2|NEW>>>\n第 2 页写到一半", end=False)
    monkeypatch.setattr(vision_client, "_call_vision_api", _fake_vision_call([], raw))

    records, handler = _capture_agent_log()
    try:
        result = vision_client.classify_and_transcribe([tmp_path / "1.jpg", tmp_path / "2.jpg"])
    finally:
        logging.getLogger("AgentLogger").removeHandler(handler)

    assert result is not None
    assert result["ended"] is False
    # 正文照样采用（部分成功可救），不是整批作废
    assert result["pages"] == ["第 1 页", "第 2 页写到一半"]
    assert any("<<<END>>>" in record.getMessage() for record in records), "截断必须留告警"


def test_multi_image_group_merges_in_batches(tmp_path, monkeypatch):
    """默认配置下 8 图分组走**分批合并**，页序按原图下标写回。

    语义变化（组 H2，2026-09-21 实测驱动）：早先是"8 张塞进一次请求"，当时改成
    "2 批各 4 张并发"。**2026-09-23 的交替轮次实测推翻了那次的前提**（批量 4：
    8.68 s / 3 请求 / 每轮补页；批量 8：7.68 s / 1 请求 / 0 补页），默认因此改回 8。

    用例把它固定成 4 张一批，好继续锁住"批次切分 + 按原图下标写回页序"这条契约 ——
    这是分批逻辑的核心，与默认值无关。
    """
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "VISION_BATCH_SIZE", 4)
    assert config.COMBINED_VISION_MAX_IMAGES == 8

    # 假响应按"这一批内"的页号作答 —— 每批都是 PAGE 1..4
    body = "".join(f"<<<PAGE {i}|NEW>>>\n第 {i} 页正文\n" for i in range(1, 5))
    calls: list[dict] = []
    monkeypatch.setattr(vision_client, "_call_vision_api", _fake_vision_call(calls, _page_raw(body)))

    images = [tmp_path / f"{i}.jpg" for i in range(8)]
    result = vision_client.classify_and_transcribe(images)

    assert result["vision_mode"] == "batched"
    # 两批返回的是同一段假文本，因此页序只能由**下标**决定：1-4 来自第一批、5-8 来自第二批
    assert result["pages"] == [f"第 {i} 页正文" for i in range(1, 5)] * 2
    assert result["failed_pages"] == []
    assert result["continuations"] == [False] * 8
    assert result["refilled"] == 0
    # 2 次请求（每批 1 次），而不是 9 次（1 分类 + 8 串行 OCR）
    assert len(calls) == 2
    assert all(call["stream"] is True for call in calls)
    assert result["calls"] == 2
    assert result["seams"] is True  # 每批都拿到了 NEW/CONT → 可以本地内联拼接
    # 每批只带自己那 4 张图
    assert calls[0]["images"] == images[:4] or calls[1]["images"] == images[:4]
    assert calls[0]["images"] != calls[1]["images"]
    assert calls[0]["model"] == config.VISION_CLASSIFY_MODEL


def test_combined_call_falls_back_to_json_protocol(tmp_path, monkeypatch):
    """PAGE 协议完全解析不出时走第二级 JSON 协议，并如实标记 `vision_mode="json"`。

    JSON 版没有 NEW/CONT 标记，因此 continuations 必须全 False（保守：不做内联去重、
    调用方保留润色），否则会静默丢掉一道题的开头。
    """
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client,
        "_call_vision_api",
        _fake_vision_call(
            calls,
            "抱歉，我直接给 JSON：",  # 无页标记 → PAGE 协议解析失败
            non_stream_text='{"problem_type": "CODING", "pages": ["题面"]}',
        ),
    )

    result = vision_client.classify_and_transcribe([tmp_path / "1.jpg"])

    assert result["vision_mode"] == "json"
    assert result["pages"] == ["题面"]
    assert result["continuations"] == [False]
    assert len(calls) == 2
    assert calls[0]["stream"] is True
    assert calls[0]["prompt"] == prompts.CLASSIFY_AND_TRANSCRIBE_PROMPT
    assert calls[1]["stream"] is False
    assert calls[1]["prompt"] == prompts.CLASSIFY_AND_TRANSCRIBE_JSON_PROMPT


def test_combined_call_returns_none_when_both_protocols_fail(tmp_path, monkeypatch):
    """三级回退链的最后一级：返回 None，调用方改用"分类 + 逐页 OCR 并行"路径。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client,
        "_call_vision_api",
        _fake_vision_call(calls, "我无法识别这张图", non_stream_text="not json at all"),
    )

    assert vision_client.classify_and_transcribe([tmp_path / "1.jpg"]) is None
    assert len(calls) == 2


def test_partial_page_success_is_adopted_and_refilled(tmp_path, monkeypatch):
    """部分成功即采用：只对失败页补做（补 1 页 = 1 次调用，而不是整批重来）。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    # 4 张图，模型只给了 1、2、4 页 → 第 3 页失败并补做
    body = (
        "<<<PAGE 1|NEW>>>\n甲\n"
        "<<<PAGE 2|CONT>>>\n乙\n"
        "<<<PAGE 4|NEW>>>\n丁\n"
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client,
        "_call_vision_api",
        _fake_vision_call(calls, _page_raw(body), non_stream_text="补做回来的第三页"),
    )

    images = [tmp_path / f"{i}.jpg" for i in range(4)]
    result = vision_client.classify_and_transcribe(images)

    assert result["vision_mode"] == "combined"
    assert result["pages"] == ["甲", "乙", "补做回来的第三页", "丁"]
    assert result["continuations"] == [False, True, False, False]
    assert result["failed_pages"] == []  # 补齐后不再有失败页
    assert result["refilled"] == 1
    assert len(calls) == 2
    # 补做只带那一张图、用单页转录 prompt
    assert calls[1]["images"] == [images[2]]
    assert calls[1]["prompt"] == prompts.TRANSCRIPTION_PROMPT
    assert calls[1]["stream"] is False


def test_missing_type_marker_triggers_one_classification_call(tmp_path, monkeypatch):
    """缺 `<<<TYPE>>>` 时 problem_type 留 None，由 refill_pages 补**一次**分类调用。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client,
        "_call_vision_api",
        _fake_vision_call(
            calls, _page_raw("<<<PAGE 1|NEW>>>\n题面\n", type_label=None), non_stream_text="GENERAL"
        ),
    )

    result = vision_client.classify_and_transcribe([tmp_path / "1.jpg"])

    assert result["problem_type"] == "GENERAL"
    assert result["refilled"] == 0
    assert len(calls) == 2  # 1 次合并 + 1 次分类
    assert calls[1]["prompt"] == prompts.CLASSIFICATION_PROMPT
    assert calls[1]["stream"] is False


def test_stream_error_marker_skips_the_json_fallback(tmp_path, monkeypatch):
    """流里只有"请求没发出去"的错误标记时，直接返回 None（再走 JSON 协议是白等）。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client,
        "_call_vision_api",
        _fake_vision_call(
            calls,
            f"\n\n{vision_client._STREAM_ERROR_MARKER} All retries failed for deepseek-flash. ---\n",
        ),
    )

    assert vision_client.classify_and_transcribe([tmp_path / "1.jpg"]) is None
    assert len(calls) == 1  # 不再发起第二次（JSON）调用


def test_collect_stream_retries_errors_raised_during_iteration(monkeypatch):
    """流式下错误发生在**迭代期间**：收集器必须自己重试。

    为什么这么测：`_call_vision_api` 的重试循环只覆盖 `create()` 抛出的异常，而流式下
    `create()` 已经返回。没有这一层，一次网络抖动就会丢掉整次转录（合并调用承载
    全部分页，代价尤其大）。重试语义是"整次请求重发"，已收集的半截内容作废。
    """
    monkeypatch.setattr(config, "RETRY_DELAY", 0)

    attempts = {"count": 0}
    seen_chunks: list[str] = []

    def issue_request():
        attempts["count"] += 1
        if attempts["count"] == 1:
            def broken():
                yield _chunks("半截")[0]
                raise APIConnectionError(request=_request())
            return broken()
        return iter(_chunks("完整转录"))

    text, finish_reason = vision_client._collect_stream(
        issue_request, model_name="deepseek-flash", on_chunk=seen_chunks.append
    )

    assert attempts["count"] == 2  # 抖动后整次重发
    assert text == "完整转录"  # 半截内容被丢弃，不是拼在一起
    assert finish_reason == "stop"
    assert seen_chunks == ["半截", "完整转录"]  # 增量回调如实反映"收到的每一段"


def test_collect_stream_gives_up_after_max_retries(monkeypatch):
    """可重试错误耗尽 `MAX_RETRIES` 后返回 `("", None)`，不抛异常给调用方。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    attempts = {"count": 0}

    def issue_request():
        attempts["count"] += 1
        raise APIConnectionError(request=_request())

    records, handler = _capture_agent_log()
    try:
        text, finish_reason = vision_client._collect_stream(
            issue_request, model_name="deepseek-flash"
        )
    finally:
        logging.getLogger("AgentLogger").removeHandler(handler)

    assert text == ""
    assert finish_reason is None
    assert attempts["count"] == config.MAX_RETRIES + 1
    assert any("放弃" in record.getMessage() for record in records)


# ---------------------------------------------------------------------------
# PAGE 协议解析（协议换掉 JSON 的核心）
# ---------------------------------------------------------------------------


def test_parse_page_protocol_three_pages_with_new_and_cont():
    raw = _page_raw(
        "<<<PAGE 1|NEW>>>\n第一页题干\n"
        "<<<PAGE 2|CONT>>>\n接续内容\n"
        "<<<PAGE 3|NEW>>>\n第三页\n"
    )

    parsed = vision_client.parse_page_protocol(raw, 3)

    assert parsed["problem_type"] == "MULTIPLE_CHOICE"
    assert parsed["pages"] == ["第一页题干", "接续内容", "第三页"]
    assert parsed["continuations"] == [False, True, False]
    assert parsed["declared"] == [1, 2, 3]
    assert parsed["failed_pages"] == []


def test_parse_page_protocol_tolerates_missing_or_spaced_flag():
    """`NEW`/`CONT` 缺失或带空格时必须保守按 NEW 处理，而不是丢掉整页。

    为什么这么测：早期正则**强制**要求 `|NEW`，于是模型写成 `<<<PAGE 1>>>` 时
    一个页标记都匹配不上 —— 整页丢失并触发 JSON 回退（还可能连 `<<<END>>>` 一起
    误报截断）。计划书的规则是"标记缺失即按 NEW"，宁可不合并也不可丢题。
    """
    raw_spaced = _page_raw("<<<PAGE 1 | NEW >>>\n第一页\n<<<PAGE 2 | CONT >>>\n接续\n")
    parsed = vision_client.parse_page_protocol(raw_spaced, 2)
    assert parsed["pages"] == ["第一页", "接续"]
    assert parsed["continuations"] == [False, True]
    assert parsed["failed_pages"] == []

    raw_no_flag = _page_raw("<<<PAGE 1>>>\n第一页\n<<<PAGE 2>>>\n第二页\n")
    parsed = vision_client.parse_page_protocol(raw_no_flag, 2)
    assert parsed["pages"] == ["第一页", "第二页"]
    # 没有 CONT 信息 → 全部按 NEW（不做跨页去重，保留润色更安全）
    assert parsed["continuations"] == [False, False]
    assert parsed["failed_pages"] == []


def test_parse_page_protocol_preserves_latex_backslashes():
    """S12 回归保护：正文逐字直出，LaTeX 反斜杠不得被转义成控制字符。

    为什么这么测：JSON 协议下 `\\frac`/`\\begin`/`\\theta`/`\\neq` 漏转义都会
    **解析成功但正文被破坏**（formfeed/backspace/tab/换行），会一路写到解答文件里
    且没有任何校验能发现。换成分隔符协议正是为了杜绝这类静默损坏。
    """
    raw = _page_raw(
        "<<<PAGE 1|NEW>>>\n"
        r"设 $x=\frac{a}{b}$，矩阵 $\begin{matrix}1&0\\0&1\end{matrix}$" "\n"
        "<<<PAGE 2|CONT>>>\n"
        r"已知 $\theta \neq 0$，$\sqrt{2}$ 是无理数" "\n"
        "<<<PAGE 3|NEW>>>\n"
        r"结论：$\frac{\theta}{\sqrt{2}}$" "\n"
    )

    pages = vision_client.parse_page_protocol(raw, 3)["pages"]

    assert r"\frac{a}{b}" in pages[0]
    assert r"\begin{matrix}" in pages[0]
    assert r"\theta" in pages[1]
    assert r"\neq" in pages[1]
    assert r"\sqrt{2}" in pages[1]
    assert r"\frac{\theta}{\sqrt{2}}" in pages[2]

    text = "\n".join(pages)
    for control_char in ("\x0c", "\x08", "\t", "\x0b", "\r"):
        assert control_char not in text, f"正文里出现了被转义的控制字符 {control_char!r}"
    # `\neq` 若被当成换行转义，这一页会被拆成两行——单独锁一遍
    assert "\n" not in pages[1]


def test_json_protocol_is_the_corruption_the_page_protocol_avoids():
    """对照组：证明上面那条断言测的是真问题，而不是"顺手加的"。

    JSON 里模型把 `\\frac` 写成单反斜杠时，`\\f` 是**合法**转义 → 解析"成功"、
    正文却变成 formfeed + "rac"；`\\sqrt` 则直接硬失败。
    """
    corrupted = vision_client.parse_json_response(
        r'{"problem_type": "GENERAL", "pages": ["\frac{a}{b}"]}'
    )
    assert corrupted is not None  # 页数/长度校验都发现不了
    assert corrupted["pages"][0] == "\x0crac{a}{b}"

    assert vision_client.parse_json_response(r'{"pages": ["\sqrt{2}"]}') is None


def test_parse_page_protocol_locates_pages_by_declared_number():
    """按**声明序号**而不是出现顺序定位：跳号能被发现，页也不会滑位。"""
    raw = _page_raw(
        "<<<PAGE 1|NEW>>>\n甲\n"
        "<<<PAGE 2|CONT>>>\n乙\n"
        "<<<PAGE 4|NEW>>>\n丁\n"
    )

    parsed = vision_client.parse_page_protocol(raw, 4)

    assert parsed["declared"] == [1, 2, 4]
    # 第 3 页模型没给 → 空串 + 记入 failed_pages（0 基下标 2）
    assert parsed["pages"][2] == ""
    assert parsed["failed_pages"] == [2]
    # 4 号页留在下标 3，不会因为"少了一页"滑到下标 2
    assert parsed["pages"][3] == "丁"


def test_parse_page_protocol_records_duplicate_page_numbers():
    """重复声明同一页码 → 记入 failed_pages（内容可能被覆盖，必须让调用方知道）。"""
    raw = _page_raw(
        "<<<PAGE 1|NEW>>>\n第一版\n"
        "<<<PAGE 1|NEW>>>\n第二版\n"
        "<<<PAGE 2|NEW>>>\n乙\n"
    )

    parsed = vision_client.parse_page_protocol(raw, 2)

    assert parsed["declared"] == [1, 1, 2]
    assert parsed["pages"][0] == "第二版"  # 后写覆盖先写
    assert parsed["pages"][1] == "乙"
    assert parsed["failed_pages"] == [0]


def test_out_of_range_page_number_is_dropped_not_recorded():
    """越界页码：当前实现**忽略该标记且不记入 failed_pages**。

    注意这是实现与契约 §2 的一个差异点（契约写"越界/重复的 n 记入 failed_pages"，
    实测只有重复会被记；越界页的内容会安静地丢失）——已回报 Lead，这里锁定真实行为，
    避免"以为越界会被发现"。
    """
    raw = _page_raw(
        "<<<PAGE 1|NEW>>>\n甲\n"
        "<<<PAGE 2|NEW>>>\n乙\n"
        "<<<PAGE 9|NEW>>>\n丙\n"
    )

    parsed = vision_client.parse_page_protocol(raw, 2)

    assert parsed["declared"] == [1, 2]
    assert parsed["pages"] == ["甲", "乙"]  # "丙" 随越界标记一起被丢弃
    assert parsed["failed_pages"] == []  # ← 与契约不一致的地方


def test_parse_page_protocol_reads_to_eof_without_end_marker():
    """没有 `<<<END>>>` 时也要能解析到 EOF（模型忘写收尾标记很常见）。"""
    raw = "<<<TYPE>>>CODING\n<<<PAGE 1|NEW>>>\n只有一页，且没有 END 标记"

    parsed = vision_client.parse_page_protocol(raw, 1)

    assert parsed["pages"] == ["只有一页，且没有 END 标记"]
    assert parsed["problem_type"] == "CODING"


def test_parse_page_protocol_drops_preamble_and_code_fence():
    """标记前的客套话与 Markdown 代码围栏一律丢弃（否则会写进题目文本）。"""
    raw = (
        "好的，我来帮你转录。\n"
        "```markdown\n"
        "<<<TYPE>>>QUESTION_ANSWERING\n"
        "<<<PAGE 1|NEW>>>\n第一页正文\n"
        "<<<PAGE 2|CONT>>>\n第二页正文\n"
        "<<<END>>>\n"
        "```\n"
    )

    parsed = vision_client.parse_page_protocol(raw, 2)

    assert parsed["problem_type"] == "QUESTION_ANSWERING"
    assert parsed["pages"] == ["第一页正文", "第二页正文"]
    assert parsed["continuations"] == [False, True]
    assert "客套话" not in "".join(parsed["pages"])
    assert "```" not in "".join(parsed["pages"])


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "这是一段普通回答，完全没有页标记。",
    ],
)
def test_parse_page_protocol_without_markers_returns_none(raw):
    """完全没有页标记 → None，调用方据此回退 JSON 协议 / 并行路径。"""
    assert vision_client.parse_page_protocol(raw, 3) is None


def test_parse_page_protocol_without_type_marker_gives_empty_problem_type():
    """缺 `<<<TYPE>>>` → problem_type 为 None（交由 refill_pages 补一次分类）。"""
    parsed = vision_client.parse_page_protocol(_page_raw("<<<PAGE 1|NEW>>>\n题面\n", type_label=None), 1)

    assert parsed["problem_type"] is None
    assert parsed["pages"] == ["题面"]


# ---------------------------------------------------------------------------
# 本地拼接（零 token 成本的内联去重）
# ---------------------------------------------------------------------------


def test_join_by_continuation_uses_newline_for_cont_and_blank_line_for_new():
    """CONT 用 `\\n`（同一题翻页）、NEW 用 `\\n\\n`（新的一道题），空页跳过。"""
    joined = vision_client.join_by_continuation(
        ["第一段", "接续", "", "新题"],
        [False, True, False, False],
    )

    assert joined == "第一段\n接续\n\n新题"


def test_join_by_continuation_of_all_empty_pages_is_empty_string():
    assert vision_client.join_by_continuation(["", "  ", ""], [False, True, False]) == ""


# ---------------------------------------------------------------------------
# refill_pages：按失败下标单页补做
# ---------------------------------------------------------------------------


def test_refill_pages_only_reruns_failed_indices(tmp_path, monkeypatch):
    """补 2 页 = 2 次调用（不是整批 9 次重来），且原地写回 result。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client, "_call_vision_api", _fake_vision_call(calls, "", non_stream_text="补做内容")
    )

    images = [tmp_path / f"{i}.jpg" for i in range(4)]
    result = {
        "problem_type": "CODING",
        "pages": ["甲", "", "丙", ""],
        "failed_pages": [1, 3],
        "continuations": [False, False, False, False],
        "vision_mode": "combined",
    }

    updated = vision_client.refill_pages(images, result)

    assert updated is result  # 原地修改并返回
    assert len(calls) == 2  # 调用次数 == 失败页数
    assert [call["images"][0] for call in calls] == [images[1], images[3]]
    assert all(call["prompt"] == prompts.TRANSCRIPTION_PROMPT for call in calls)
    assert updated["pages"] == ["甲", "补做内容", "丙", "补做内容"]
    assert updated["failed_pages"] == []
    assert updated["refilled"] == 2


def test_refill_pages_keeps_pages_that_still_fail(tmp_path, monkeypatch):
    """补做仍返回空内容时，该页必须留在 failed_pages 里（不能假装成功）。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client, "_call_vision_api", _fake_vision_call(calls, "", non_stream_text=None)
    )

    images = [tmp_path / "1.jpg"]
    result = {
        "problem_type": "CODING",
        "pages": [""],
        "failed_pages": [0],
        "continuations": [False],
        "vision_mode": "combined",
    }

    updated = vision_client.refill_pages(images, result)

    assert len(calls) == 1
    assert updated["refilled"] == 0
    assert updated["failed_pages"] == [0]


def test_refill_pages_classifies_once_when_problem_type_missing(tmp_path, monkeypatch):
    """只有 `problem_type` 缺失时才补**一次**分类调用；此时不应触发任何单页 OCR。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client, "_call_vision_api", _fake_vision_call(calls, "", non_stream_text="GENERAL")
    )

    images = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    result = {
        "problem_type": None,
        "pages": ["甲", "乙"],
        "failed_pages": [],
        "continuations": [False, False],
        "vision_mode": "combined",
    }

    updated = vision_client.refill_pages(images, result)

    assert updated["problem_type"] == "GENERAL"
    assert updated["refilled"] == 0
    assert len(calls) == 1
    assert calls[0]["prompt"] == prompts.CLASSIFICATION_PROMPT


# ---------------------------------------------------------------------------
# 截断诊断
# ---------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, content: str | None, finish_reason: str | None) -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.payloads: list[dict] = []

    def create(self, **payload):
        self.payloads.append(payload)
        choice = SimpleNamespace(
            message=SimpleNamespace(content=self.content),
            finish_reason=self.finish_reason,
        )
        return SimpleNamespace(choices=[choice])


def _capture_agent_log() -> tuple[list[logging.LogRecord], logging.Handler]:
    """抓取 AgentLogger 的日志。

    注意：这个 logger 显式 `propagate=False`，pytest 的 caplog 默认抓不到，
    所以直接挂一个自己的 handler。
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logging.getLogger("AgentLogger").addHandler(handler)
    return records, handler


@pytest.mark.parametrize(
    ("finish_reason", "should_warn"),
    [("length", True), ("stop", False)],
)
def test_truncated_vision_output_is_logged(finish_reason, should_warn, monkeypatch):
    """`finish_reason == "length"` 是判断"转录被截断"的唯一信号，务必保留。

    模型名用 `config.VISION_CLASSIFY_MODEL` 而不是硬编码 GLM —— 本用例与
    "当前跑在哪个 provider 上"无关。
    """
    completions = _FakeCompletions('{"problem_type": "CODING", "pages": ["截', finish_reason)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    # 实现已支持 provider 参数，fake 的签名要跟上（`_call_vision_api` 会位置传参）
    monkeypatch.setattr(vision_client, "_get_vision_client", lambda provider=None: fake_client)

    records, handler = _capture_agent_log()
    try:
        text = vision_client._call_vision_api([], "prompt", config.VISION_CLASSIFY_MODEL)
    finally:
        logging.getLogger("AgentLogger").removeHandler(handler)

    assert text == '{"problem_type": "CODING", "pages": ["截'
    warned = any("截断" in record.getMessage() for record in records)
    assert warned is should_warn
    # 告警里必须带上真实的输出上限，便于判断"该不该调大 VISION_MAX_TOKENS"
    if should_warn:
        assert any(str(config.VISION_MAX_TOKENS) in record.getMessage() for record in records)
    # payload 里下发的是配置里的输出上限，而不是旧实现写死的 8192
    assert completions.payloads[0]["max_tokens"] == config.VISION_MAX_TOKENS


# ---------------------------------------------------------------------------
# provider 透传与逐页补做（A/B 闸门成立的前提）
# ---------------------------------------------------------------------------


def test_parallel_path_threads_the_requested_provider(tmp_path, monkeypatch):
    """`classify_and_transcribe_parallel(paths, provider)` 必须把 provider 传到每一条调用。

    这是迁移审计发现的**阻断级**缺陷：并行路径此前不接受 provider 参数，于是 A/B 工具
    按 `--provider zhipu` 评测时，实际打的是默认 provider —— 而计划书把 A/B 称为
    "核心闸门"，闸门测错了对象等于没有闸门。
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client,
        "_call_vision_api",
        _fake_vision_call(calls, "", non_stream_text="GENERAL"),
    )
    # 转录用单页 prompt、分类用分类 prompt：两者都要是 GLM 的模型名
    monkeypatch.setattr(config, "OCR_PARALLEL_WORKERS", 2)

    images = [tmp_path / "1.jpg", tmp_path / "2.jpg"]
    vision_client.classify_and_transcribe_parallel(images, provider="zhipu")

    assert calls, "并行路径必须真的发起调用"
    assert all(call["provider"] == "zhipu" for call in calls)
    assert all(call["model"] == "GLM-4.6V-FlashX" for call in calls)


def test_refill_pages_threads_the_requested_provider(tmp_path, monkeypatch):
    """`refill_pages(..., provider=...)` 的单页补做与补救分类都要用同一家。

    早先这里写死 `config.VISION_CLASSIFY_MODEL`：按 provider="zhipu" 评测时会拿
    `deepseek-flash` 去请求智谱端点。
    """
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client,
        "_call_vision_api",
        _fake_vision_call(calls, "", non_stream_text="补做内容"),
    )

    images = [tmp_path / "1.jpg", tmp_path / "2.jpg"]
    result = {
        "problem_type": None,
        "pages": ["甲", ""],
        "failed_pages": [1],
        "continuations": [False, False],
        "vision_mode": "combined",
    }

    vision_client.refill_pages(images, result, provider="zhipu")

    assert result["pages"][1] == "补做内容"
    assert all(call["provider"] == "zhipu" for call in calls)
    assert all(call["model"] == "GLM-4.6V-FlashX" for call in calls)


def test_refill_pages_tolerates_short_page_list(tmp_path, monkeypatch):
    """`result["pages"]` 比图片数短时补做不应抛 IndexError（审计发现的越界隐患）。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client, "_call_vision_api", _fake_vision_call(calls, "", non_stream_text="补")
    )

    images = [tmp_path / "1.jpg", tmp_path / "2.jpg"]
    result = {"problem_type": "GENERAL", "pages": [], "failed_pages": [1]}

    updated = vision_client.refill_pages(images, result)

    assert updated["pages"] == ["", "补"]
    assert updated["refilled"] == 1
    assert updated["failed_pages"] == []


# ---------------------------------------------------------------------------
# 组 H2：分批合并（每批 ≤ VISION_BATCH_SIZE 张图，批间并行）
# ---------------------------------------------------------------------------


def _fake_batch_calls(calls: list[dict], by_first_image: dict[str, str], refill_text: str = "补做内容"):
    """按**该批的首张图文件名**决定假响应（与批的完成顺序无关，测试因此确定）。

    单页补做（`TRANSCRIPTION_PROMPT`）单独返回 `refill_text`。
    """

    def _call(
        image_paths,
        user_prompt,
        model_name,
        stream=False,
        extra_params=None,
        provider=None,
        timeout=None,
    ):
        calls.append(
            {
                "images": list(image_paths),
                "prompt": user_prompt,
                "stream": stream,
                "provider": provider,
                "timeout": timeout,
            }
        )
        if user_prompt == prompts.TRANSCRIPTION_PROMPT:
            return refill_text
        key = image_paths[0].name if image_paths else ""
        return iter(_chunks(by_first_image.get(key, by_first_image.get("*", ""))))

    return _call


def test_batches_split_by_batch_size_and_keep_global_order(tmp_path, monkeypatch):
    """`VISION_BATCH_SIZE=2` + 6 张图 → 3 批；页序按**原图下标**而非返回顺序。

    并发返回顺序是随机的，而"哪张是第一页"决定整道题对不对，因此合并必须按下标写回。
    """
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "VISION_BATCH_SIZE", 2)
    monkeypatch.setattr(config, "VISION_BATCH_WORKERS", 3)

    by_first = {
        "0.jpg": _page_raw("<<<PAGE 1|NEW>>>\nA\n<<<PAGE 2|CONT>>>\nB\n"),
        "2.jpg": _page_raw("<<<PAGE 1|NEW>>>\nC\n<<<PAGE 2|NEW>>>\nD\n"),
        "4.jpg": _page_raw("<<<PAGE 1|NEW>>>\nE\n<<<PAGE 2|CONT>>>\nF\n"),
    }
    calls: list[dict] = []
    monkeypatch.setattr(vision_client, "_call_vision_api", _fake_batch_calls(calls, by_first))

    images = [tmp_path / f"{i}.jpg" for i in range(6)]
    result = vision_client.classify_and_transcribe(images)

    assert len(calls) == 3, "6 张图按每批 2 张应当正好 3 次请求"
    assert result["pages"] == ["A", "B", "C", "D", "E", "F"]
    assert result["continuations"] == [False, True, False, False, False, True]
    assert result["vision_mode"] == "batched"
    assert result["calls"] == 3
    assert result["seams"] is True
    assert result["failed_pages"] == []
    assert result["refilled"] == 0


def test_batch_first_page_is_forced_to_new(tmp_path, monkeypatch):
    """批首页一律按 NEW：批与批之间模型互相看不见，跨批接缝没有可信判断。

    若模型把某批的首页标成 CONT，本实现**不采纳** —— 跨批去重猜错的方式是删掉一道题的
    开头，代价远高于留一点重复内容。
    """
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "VISION_BATCH_SIZE", 2)

    by_first = {
        "0.jpg": _page_raw("<<<PAGE 1|NEW>>>\nA\n<<<PAGE 2|CONT>>>\nB\n"),
        # 第 2 批首页被模型标成 CONT —— 必须被纠正成 NEW
        "2.jpg": _page_raw("<<<PAGE 1|CONT>>>\nC\n<<<PAGE 2|CONT>>>\nD\n"),
    }
    calls: list[dict] = []
    monkeypatch.setattr(vision_client, "_call_vision_api", _fake_batch_calls(calls, by_first))

    images = [tmp_path / f"{i}.jpg" for i in range(4)]
    result = vision_client.classify_and_transcribe(images)

    assert result["continuations"] == [False, True, False, True]


def test_failed_batch_is_refilled_page_by_page(tmp_path, monkeypatch):
    """某一批合并失败时只有那一批的页进 failed_pages → 单页补做，其余批照常保留。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "VISION_BATCH_SIZE", 2)

    by_first = {
        "0.jpg": _page_raw("<<<PAGE 1|NEW>>>\nA\n<<<PAGE 2|NEW>>>\nB\n"),
        # 第 2 批两种协议都失败（既无页标记、也不是 JSON）
        "2.jpg": "抱歉，我无法识别这两张图。",
    }
    calls: list[dict] = []

    def _call(image_paths, user_prompt, model_name, stream=False, extra_params=None,
              provider=None, timeout=None):
        calls.append({"images": list(image_paths), "prompt": user_prompt, "stream": stream})
        if user_prompt == prompts.TRANSCRIPTION_PROMPT:
            return "补做-" + image_paths[0].name
        if user_prompt == prompts.CLASSIFY_AND_TRANSCRIBE_JSON_PROMPT:
            return "not json at all"
        key = image_paths[0].name if image_paths else ""
        return iter(_chunks(by_first.get(key, "")))

    monkeypatch.setattr(vision_client, "_call_vision_api", _call)

    images = [tmp_path / f"{i}.jpg" for i in range(4)]
    result = vision_client.classify_and_transcribe(images)

    assert result["pages"] == ["A", "B", "补做-2.jpg", "补做-3.jpg"]
    assert result["failed_pages"] == []
    assert result["refilled"] == 2, "只补失败那一批的 2 页，而不是整批重来"
    # 该批走后了 JSON 回退 → 全段按"无接缝"处理（保留润色）
    assert result["seams"] is False


def test_every_batch_failing_returns_none(tmp_path, monkeypatch):
    """所有批都彻底失败时返回 None，调用方回退"分类 + 逐页 OCR 并行"（重试面更大）。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "VISION_BATCH_SIZE", 2)
    calls: list[dict] = []
    monkeypatch.setattr(
        vision_client,
        "_call_vision_api",
        _fake_vision_call(calls, "无法识别", non_stream_text="not json"),
    )

    images = [tmp_path / f"{i}.jpg" for i in range(4)]
    result = vision_client.classify_and_transcribe(images)

    assert result is None
    # 每批 2 次尝试（PAGE + JSON）× 2 批 = 4 次；不会退化成逐页请求
    assert len(calls) == 4


def test_missing_end_marker_in_any_batch_marks_the_whole_run_unfinished(tmp_path, monkeypatch):
    """任一批缺 `<<<END>>>` → 整体 `ended=False`（截断信号必须保守传播）。"""
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "VISION_BATCH_SIZE", 2)
    by_first = {
        "0.jpg": _page_raw("<<<PAGE 1|NEW>>>\nA\n<<<PAGE 2|NEW>>>\nB\n"),          # 有 END
        "2.jpg": _page_raw("<<<PAGE 1|NEW>>>\nC\n<<<PAGE 2|NEW>>>\nD\n", end=False),  # 缺 END
    }
    calls: list[dict] = []
    monkeypatch.setattr(vision_client, "_call_vision_api", _fake_batch_calls(calls, by_first))

    images = [tmp_path / f"{i}.jpg" for i in range(4)]
    result = vision_client.classify_and_transcribe(images)

    assert result["ended"] is False
    assert result["pages"] == ["A", "B", "C", "D"]


def test_problem_type_majority_wins_when_batches_disagree(tmp_path, monkeypatch):
    """多批题型不一致时取**多数**：一组互不相关的题本来就没有唯一题型，
    而"哪一批先返回"是随机的，取多数比取"第一个完成的"稳定。"""
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "VISION_BATCH_SIZE", 2)
    by_first = {
        "0.jpg": _page_raw("<<<PAGE 1|NEW>>>\nA\n<<<PAGE 2|NEW>>>\nB\n", type_label="CODING"),
        "2.jpg": _page_raw("<<<PAGE 1|NEW>>>\nC\n<<<PAGE 2|NEW>>>\nD\n", type_label="CODING"),
        "4.jpg": _page_raw("<<<PAGE 1|NEW>>>\nE\n<<<PAGE 2|NEW>>>\nF\n", type_label="MULTIPLE_CHOICE"),
    }
    calls: list[dict] = []
    monkeypatch.setattr(vision_client, "_call_vision_api", _fake_batch_calls(calls, by_first))

    images = [tmp_path / f"{i}.jpg" for i in range(6)]
    result = vision_client.classify_and_transcribe(images)

    assert result["problem_type"] == "CODING"
    assert result["calls"] == 3


# ---------------------------------------------------------------------------
# PAGE 协议：畸形标记与编号整体平移（静默错位的两种真实形态）
# ---------------------------------------------------------------------------


def test_malformed_flag_folds_into_its_own_page_not_the_previous_one():
    """`<<<PAGE 2|NEWY>>>` 这类畸形 flag 只能影响**它自己**那一页。

    旧正则强制 `|NEW`/`|CONT` 精确匹配，畸形标记整块不匹配 → 它的正文落进**上一页**
    的切片（页边界取"下一个标记的起点"），于是上一页被塞进两页内容、自己那页为空并触发
    refill —— 结果是同一段文字在最终文本里出现两次。**静默重复**比回退危险得多。
    """
    raw = _page_raw(
        "<<<PAGE 1|NEW>>>\n第一页\n"
        "<<<PAGE 2|NEWY>>>\n第二页\n"
        "<<<PAGE 3|NEW>>>\n第三页\n"
    )

    parsed = vision_client.parse_page_protocol(raw, 3)

    assert parsed is not None
    assert parsed["pages"] == ["第一页", "第二页", "第三页"]
    # 非法 flag 保守按 NEW（不做跨页去重）
    assert parsed["continuations"] == [False, False, False]
    assert parsed["failed_pages"] == []
    assert parsed["declared"] == [1, 2, 3]


def test_zero_based_numbering_is_rejected_as_untrustworthy():
    """编号按 0 基输出时必须整段判不可信（回退），而不是把每页错位一格。

    平移后的后果是**静默**的：第 1 张图的正文落在 `PAGE 0` 块里被丢弃（它的槽位非空，
    refill 永远不会补），其余页全部右移一格。这种平移无法与"合法跳号"区分，因此宁可
    回退 JSON/并行路径（多花调用）也不接受错位。
    """
    raw = _page_raw(
        "<<<PAGE 0|NEW>>>\n第一张图\n"
        "<<<PAGE 1|NEW>>>\n第二张图\n"
        "<<<PAGE 2|NEW>>>\n第三张图\n"
    )

    assert vision_client.parse_page_protocol(raw, 3) is None


def test_page_gaps_are_still_tolerated_as_empty_slots():
    """合法跳号（模型漏了第 2 页）仍按"空槽位 → failed_pages → refill"处理。"""
    raw = _page_raw("<<<PAGE 1|NEW>>>\n甲\n<<<PAGE 3|NEW>>>\n丙\n")

    parsed = vision_client.parse_page_protocol(raw, 3)

    assert parsed is not None
    assert parsed["pages"] == ["甲", "", "丙"]
    assert parsed["failed_pages"] == [1]


def test_end_marker_truncates_every_page_body_not_only_the_last():
    """`<<<END>>>` 之后的内容不属于任何页（模型爱在结尾补一句客套话）。

    旧实现只对**最后一页**裁掉 END 之后的文本，于是"END 出现在中间"的畸形输出会把
    标记本身留给后面那页；现在每一页都在 END 处截断，后面的页变为空 → 进 failed_pages →
    refill（宁可补一次，也不把标记混进正文）。
    """
    raw = (
        "<<<TYPE>>>GENERAL\n"
        "<<<PAGE 1|NEW>>>\n甲\n<<<END>>>\n"
        "<<<PAGE 2|NEW>>>\n乙被 END 之后的内容污染\n"
    )

    parsed = vision_client.parse_page_protocol(raw, 2)

    assert parsed is not None
    assert parsed["pages"] == ["甲", ""]
    assert parsed["failed_pages"] == [1]
    assert parsed["ended"] is True


def test_trailing_chatter_after_end_is_not_part_of_the_last_page():
    """收尾标记之后模型的客套话不能写进最后一页。"""
    raw = "<<<PAGE 1|NEW>>>\n甲\n<<<END>>>\n希望这有帮助！\n"

    parsed = vision_client.parse_page_protocol(raw, 1)

    assert parsed is not None
    assert parsed["pages"] == ["甲"]
