"""
test_remote_stream.py - 全局 SSE 端点与远程连接检测测试

覆盖缺陷 A：电脑端（本机来源）连接 /api/events/stream 时绝不能收到
remote_connected 事件，否则前端会把"手机扫码"按钮隐藏掉。

这里的端点测试直接调用路由函数并只消费生成器的第一段，
避免 TestClient 处理永不结束的 SSE 流时卡住退出。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from webapp import routes
from webapp.presence import RemotePresence, watch_connection

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class _FakeClient:
    def __init__(self, host: str | None):
        self.host = host


class _FakeRequest:
    def __init__(self, host: str | None, user_agent: str | None):
        self.client = _FakeClient(host) if host is not None else None
        self.headers = {"user-agent": user_agent} if user_agent is not None else {}


def _first_sse_chunk(request: _FakeRequest) -> tuple[int, str]:
    """调用全局 SSE 端点，只取第一段数据后关闭生成器。"""
    response = asyncio.run(routes.stream_global_events(request))  # type: ignore[arg-type]
    gen = response.body_iterator

    async def _consume_first() -> str:
        try:
            return await gen.__anext__()  # type: ignore[union-attr]
        finally:
            aclose = getattr(gen, "aclose", None)
            if aclose is not None:
                await aclose()

    chunk = asyncio.run(_consume_first())
    if isinstance(chunk, bytes):
        chunk = chunk.decode("utf-8")
    return response.status_code, chunk


@pytest.fixture(autouse=True)
def _clean_global_state():
    routes.event_bus = routes.TaskEventBus()
    routes.remote_presence.clear()
    yield
    routes.remote_presence.clear()


def test_local_desktop_client_gets_no_remote_event():
    """核心回归：本机来源不应触发 remote_connected。"""
    status, body = _first_sse_chunk(_FakeRequest("127.0.0.1", DESKTOP_UA))
    assert status == 200
    assert "event: init" in body
    assert "remote_connected" not in body
    assert routes.remote_presence.active() == {}


def test_local_client_with_mobile_ua_still_not_remote():
    """即使 UA 像手机，只要来源是本机，也不算远程设备。"""
    status, body = _first_sse_chunk(_FakeRequest("127.0.0.1", IPHONE_UA))
    assert status == 200
    assert "remote_connected" not in body
    assert routes.remote_presence.active() == {}


def test_request_without_client_is_treated_as_local():
    """拿不到来源地址时不能认成手机。"""
    status, body = _first_sse_chunk(_FakeRequest(None, IPHONE_UA))
    assert status == 200
    assert "remote_connected" not in body


def test_remote_phone_connection_is_registered():
    """远程地址 + 手机 UA 才登记为远程设备，且断开时广播状态变化。"""
    # publish_global 只投递给已订阅的队列，所以要先自己订阅一个
    observer = routes.event_bus.subscribe_global()

    status, body = _first_sse_chunk(_FakeRequest("192.0.2.55", IPHONE_UA))
    assert status == 200
    assert "event: init" in body

    seen = []
    while not observer.empty():
        seen.append(observer.get_nowait().get("type"))
    assert "remote_connected" in seen
    assert "remote_disconnected" in seen

    # 生成器已关闭，计数必须回到零
    assert routes.remote_presence.active() == {}


def test_remote_desktop_on_lan_is_not_treated_as_phone():
    """局域网里另一台电脑（桌面 UA）不算手机，避免无谓的状态提示。"""
    observer = routes.event_bus.subscribe_global()
    status, _ = _first_sse_chunk(_FakeRequest("192.0.2.77", DESKTOP_UA))
    assert status == 200
    seen = []
    while not observer.empty():
        seen.append(observer.get_nowait().get("type"))
    assert "remote_connected" not in seen
    assert routes.remote_presence.active() == {}


# ---------------------------------------------------------------------------
# RemotePresence 单元测试：连接/断开事件只在正确的时刻各触发一次
# ---------------------------------------------------------------------------


def test_first_connection_notifies_once():
    presence = RemotePresence()
    connected: list[str] = []
    disconnected: list[str] = []

    close1 = watch_connection(presence, "192.0.2.55", connected.append, disconnected.append)
    assert connected == ["192.0.2.55"]
    assert disconnected == []

    # 同一台手机的第二条连接（断线重连时会出现）不再重复通知
    close2 = watch_connection(presence, "192.0.2.55", connected.append, disconnected.append)
    assert connected == ["192.0.2.55"]

    # 关掉其中一条不算断开
    close1()
    assert disconnected == []

    # 关掉最后一条才算断开
    close2()
    assert disconnected == ["192.0.2.55"]
    assert presence.active() == {}


def test_close_is_idempotent():
    presence = RemotePresence()
    disconnected: list[str] = []
    close = watch_connection(presence, "192.0.2.55", lambda _: None, disconnected.append)

    close()
    close()
    close()
    assert disconnected == ["192.0.2.55"]
    assert not presence.any_connected()


def test_empty_identity_is_ignored():
    presence = RemotePresence()
    close = watch_connection(presence, None, lambda _: None, lambda _: None)
    close()
    assert presence.active() == {}


def test_two_devices_are_tracked_independently():
    presence = RemotePresence()
    disconnected: list[str] = []
    close_a = watch_connection(presence, "192.0.2.10", lambda _: None, disconnected.append)
    close_b = watch_connection(presence, "192.0.2.11", lambda _: None, disconnected.append)

    close_a()
    assert disconnected == ["192.0.2.10"]
    assert presence.active() == {"192.0.2.11": 1}
    close_b()
    assert sorted(disconnected) == ["192.0.2.10", "192.0.2.11"]


def test_remote_event_payload_is_json_serialisable():
    """确认广播的事件体可以被 SSE 序列化。"""
    event = {"type": "remote_connected", "client_ip": "192.0.2.55"}
    assert json.loads(json.dumps(event, ensure_ascii=False)) == event
