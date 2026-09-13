"""
test_solver_client.py - 求解器客户端的健壮性测试

用假客户端打桩，不产生真实 API 调用。覆盖：
- 思考过程吃满 max_tokens（finish_reason=length、正文为空）时自动关闭思考模式重试
- 关闭思考模式后仍为空时报出可诊断的错误（带 finish_reason / 字符数）
- 建连阶段的网络错误仍然重试，最终失败时错误事件带真实异常文本
- 流式迭代中途抛错时不会把异常泄漏给调用方

运行：pytest tests/test_solver_client.py -v
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from problem_solver_agent import config, solver_client


def chunk(*, content: str | None = None, reasoning: str | None = None, finish_reason: str | None = None):
    """构造一个 OpenAI 流式 chunk（只需要 delta 与 finish_reason）。"""
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])


def stream(*chunks):
    return list(chunks)


def net_error() -> APIConnectionError:
    return APIConnectionError(
        request=httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    )


class FakeAPI:
    """假的 OpenAI 客户端。

    `responses` 按顺序消费：遇到 `Exception` 就抛出（模拟建连失败），
    遇到列表就当成一个流返回。每次请求体都记录下来供断言。
    """

    def __init__(self, *responses):
        self.queued = list(responses)
        self.payloads: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **payload):
        self.payloads.append(payload)
        if not self.queued:
            raise AssertionError("测试脚本里预置的响应已用尽")
        item = self.queued.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture()
def make_client(monkeypatch):
    """把 get_client 换成假客户端，并让重试延迟为 0（测试不等 10 秒）。"""
    monkeypatch.setattr(config, "RETRY_DELAY", 0)

    def _make(*responses) -> FakeAPI:
        client = FakeAPI(*responses)
        monkeypatch.setattr(solver_client, "get_client", lambda provider: client)
        return client

    return _make


def _text_of(events, event_type: str) -> str:
    return "".join(e["content"] for e in events if e["type"] == event_type)


# ---------------------------------------------------------------------------
# 思考过程吃满配额 → 自动降级
# ---------------------------------------------------------------------------


def test_thinking_exhaustion_falls_back_to_non_thinking(make_client):
    """复现线上故障：思考几分钟吃满 max_tokens，正文一个字没有。"""
    client = make_client(
        stream(chunk(reasoning="想了很久", finish_reason="length")),  # 思考模式：只有思考
        stream(chunk(content="## 最终答案\n选 B。", finish_reason="stop")),  # 降级后：有正文
    )

    events = list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash", enable_thinking=True))

    assert _text_of(events, "content") == "## 最终答案\n选 B。"
    assert not [e for e in events if e["type"] == "error"]
    # 思考内容仍然照常下发，用户能看到"发生了什么"
    assert "想了很久" in _text_of(events, "reasoning")

    assert len(client.payloads) == 2
    assert client.payloads[0]["extra_body"]["thinking"]["type"] == "enabled"
    assert client.payloads[0]["reasoning_effort"] == "medium"
    # 第二次必须真正关掉思考模式，否则会再次把配额耗在思考上
    assert client.payloads[1]["extra_body"]["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in client.payloads[1]
    assert client.payloads[1]["max_tokens"] == config.SOLVER_MAX_TOKENS


def test_thinking_disabled_is_sent_explicitly(make_client):
    """关闭思考必须显式下发 `thinking.type=disabled`。

    实测：只把 `extra_body` 整个省略时模型照样思考（741 字符思考、正文 0 字符），
    因此旧实现里"关闭思考模式重解"其实没有生效。
    """
    client = make_client(stream(chunk(content="解答", finish_reason="stop")))

    events = list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash", enable_thinking=False))

    assert _text_of(events, "content") == "解答"
    assert client.payloads[0]["extra_body"]["thinking"]["type"] == "disabled"


def test_fallback_failure_reports_finish_reason(make_client):
    """降级后依然没有正文时，错误信息要能自己解释原因。"""
    make_client(
        stream(chunk(reasoning="思考", finish_reason="length")),
        stream(chunk(reasoning="继续思考", finish_reason="length")),
    )

    events = list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash", enable_thinking=True))
    errors = [e["content"] for e in events if e["type"] == "error"]

    assert len(errors) == 1
    assert "deepseek-flash" in errors[0]
    assert "finish_reason=length" in errors[0]
    assert "正文=0 字符" in errors[0]


def test_non_thinking_empty_response_is_reported_without_second_call(make_client):
    """本来就没开思考模式时空响应无法再降级，直接报错（且只调用一次）。"""
    client = make_client(stream(chunk(finish_reason="length")))

    events = list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash", enable_thinking=False))

    # 先给流水线一份"本次求解画像"，再报错——画像里的 truncated 是升级思考档的依据
    assert [e["type"] for e in events] == ["meta", "error"]
    meta = events[0]
    assert meta["finish_reason"] == "length"
    assert meta["truncated"] is True
    assert meta["thinking"] is False
    assert "finish_reason=length" in events[1]["content"]
    assert len(client.payloads) == 1


# ---------------------------------------------------------------------------
# 失败路径的可诊断性
# ---------------------------------------------------------------------------


def test_fatal_api_error_is_surfaced_without_retry(make_client):
    """非网络类错误（如模型名/鉴权问题）直接失败，但要把真实原因带出去。"""
    client = make_client(RuntimeError("Error code: 401 - Authentication Fails"))

    events = list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash"))

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "401" in events[0]["content"]
    assert "deepseek-flash" in events[0]["content"]
    assert len(client.payloads) == 1  # 致命错误不重试


def test_network_error_is_retried_then_succeeds(make_client):
    """网络错误保持原有重试语义：前两次建连失败，第三次拿到正文。"""
    client = make_client(
        net_error(),
        net_error(),
        stream(chunk(content="恢复后的解答", finish_reason="stop")),
    )

    events = list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash"))

    assert _text_of(events, "content") == "恢复后的解答"
    assert not [e for e in events if e["type"] == "error"]
    assert len(client.payloads) == 3


def test_network_error_exhausts_retries_reports_reason(make_client):
    """重试次数用尽时，错误事件要说明是网络问题而不是"空响应"。"""
    attempts = config.MAX_RETRIES + 1
    client = make_client(*[net_error() for _ in range(attempts)])

    events = list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash"))

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "APIConnectionError" in events[0]["content"]
    assert len(client.payloads) == attempts


def test_stream_iteration_error_becomes_error_event(make_client):
    """流已经建好、迭代中途炸掉时，也不能把异常泄漏给流水线。"""

    def broken_stream():
        yield chunk(content="前半段", finish_reason=None)
        raise RuntimeError("connection reset by peer")

    client = make_client(broken_stream())

    events = list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash"))
    errors = [e["content"] for e in events if e["type"] == "error"]

    assert _text_of(events, "content") == "前半段"
    assert len(errors) == 1
    assert "connection reset by peer" in errors[0]
    assert len(client.payloads) == 1


# ---------------------------------------------------------------------------
# 求解画像（meta）与"按需升级"所需的接口
# ---------------------------------------------------------------------------


def test_success_emits_solver_profile(make_client):
    """成功时也要给出画像：流水线靠它判断答案是否被截断。"""
    make_client(stream(chunk(content="## 最终答案\n选 B。", finish_reason="stop")))

    events = list(
        solver_client.stream_solve("题干", "deepseek", "deepseek-flash", enable_thinking=False)
    )

    metas = [e for e in events if e["type"] == "meta"]
    assert len(metas) == 1
    assert metas[0]["finish_reason"] == "stop"
    assert metas[0]["truncated"] is False
    assert metas[0]["content_chars"] > 0
    assert metas[0]["thinking"] is False


def test_explicit_max_tokens_is_used(make_client):
    """升级档要能把预算调大（实测该 API 接受 32768）。"""
    client = make_client(stream(chunk(content="解答", finish_reason="stop")))

    list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash", max_tokens=32000))

    assert client.payloads[0]["max_tokens"] == 32000
    assert client.payloads[0]["max_tokens"] != config.SOLVER_MAX_TOKENS


def test_reasoning_effort_comes_from_config(make_client, monkeypatch):
    monkeypatch.setattr(config, "SOLVER_REASONING_EFFORT", "low")
    client = make_client(stream(chunk(content="解答", finish_reason="stop")))

    list(solver_client.stream_solve("题干", "deepseek", "deepseek-flash", enable_thinking=True))

    assert client.payloads[0]["reasoning_effort"] == "low"


def test_reasoning_limit_aborts_before_burning_the_budget(make_client):
    """思考远超阈值且始终没有正文时提前掐断，直接转入"关闭思考模式重试"。"""
    consumed: list[int] = []

    def endless_reasoning():
        for index in range(50):
            consumed.append(index)
            yield chunk(reasoning="思" * 100, finish_reason=None)

    client = make_client(
        endless_reasoning(),
        stream(chunk(content="兜底正文", finish_reason="stop")),
    )

    events = list(
        solver_client.stream_solve(
            "题干",
            "deepseek",
            "deepseek-flash",
            enable_thinking=True,
            reasoning_char_limit=250,
        )
    )

    assert len(consumed) <= 5, "提前掐断没生效，配额会被思考烧完"
    assert _text_of(events, "content") == "兜底正文"
    metas = [e for e in events if e["type"] == "meta"]
    assert metas[0]["aborted"] is True
    assert metas[0]["content_chars"] == 0
    # 第二次请求必须真正关掉思考
    assert client.payloads[1]["extra_body"]["thinking"]["type"] == "disabled"
