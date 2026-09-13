"""
test_vision_client.py - 视觉客户端的"合并调用闸门"与截断诊断测试

背景（2026-09-13 事故复盘）：
- 合并调用（一次请求同时拿到题型 + 全部逐页转录）要求把**所有图片的完整转录**
  塞进一个 JSON，而输出上限只有 8192 token；多图时必然被截断 → 解析失败 → 回退，
  白等约 50 秒且这次调用照样计费。webapp 用量流水显示 12 次尝试只成功 1 次。
- 截断这个真因原本被完全吞掉：日志里只有"无法解析为 JSON"。

运行：pytest tests/test_vision_client.py -v
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from problem_solver_agent import config, vision_client


# ---------------------------------------------------------------------------
# 闸门
# ---------------------------------------------------------------------------


def test_auto_mode_only_allows_small_groups(monkeypatch):
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "COMBINED_VISION_MAX_IMAGES", 1)

    assert vision_client.combined_call_enabled(1) is True
    assert vision_client.combined_call_enabled(2) is False
    assert vision_client.combined_call_enabled(7) is False


def test_true_and_false_modes_override_image_count(monkeypatch):
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "true")
    assert vision_client.combined_call_enabled(9) is True

    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "false")
    assert vision_client.combined_call_enabled(1) is False


def test_multi_image_group_skips_the_combined_request(tmp_path, monkeypatch):
    """多图时应当**连请求都不发**，而不是发出去等它超长截断。"""
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "COMBINED_VISION_MAX_IMAGES", 1)

    calls: list = []
    monkeypatch.setattr(
        vision_client, "_call_vision_api", lambda *a, **k: calls.append(a) or "{}"
    )

    images = [tmp_path / f"{i}.jpg" for i in range(4)]
    assert vision_client.classify_and_transcribe(images) is None
    assert calls == []


def test_single_image_still_uses_the_combined_call(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USE_COMBINED_VISION_CALL", "auto")
    monkeypatch.setattr(config, "COMBINED_VISION_MAX_IMAGES", 1)
    payload = '{"problem_type": "CODING", "pages": ["题面内容"]}'
    monkeypatch.setattr(vision_client, "_call_vision_api", lambda *a, **k: payload)

    result = vision_client.classify_and_transcribe([tmp_path / "1.jpg"])
    assert result == {"problem_type": "CODING", "pages": ["题面内容"]}


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
    completions = _FakeCompletions('{"problem_type": "CODING", "pages": ["截', finish_reason)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(vision_client, "_get_vision_client", lambda: fake_client)

    records, handler = _capture_agent_log()
    try:
        text = vision_client._call_vision_api([], "prompt", "GLM-4.6V-FlashX")
    finally:
        logging.getLogger("AgentLogger").removeHandler(handler)

    assert text == '{"problem_type": "CODING", "pages": ["截'
    warned = any("截断" in record.getMessage() for record in records)
    assert warned is should_warn
