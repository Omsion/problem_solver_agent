"""
presence.py - 远程设备（手机）连接状态跟踪

职责单一，便于单元测试：记录每台远程设备当前有多少条活跃的全局 SSE 连接，
并在"第一台连接"与"最后一台断开"时各触发一次回调。

为什么用计数而不是集合：手机断线重连时，旧连接与新连接可能短暂并存。
只有计数归零才代表真正断开，否则会在重连瞬间误报 remote_disconnected。
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class RemotePresence:
    """跟踪远程设备的活跃连接数。所有操作都是线程安全的。"""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def register(self, identity: str) -> bool:
        """登记一条连接。

        Returns:
            True 表示这是该设备的第一条连接（调用方应广播"已连接"）。
        """
        if not identity:
            return False
        with self._lock:
            current = self._counts.get(identity, 0) + 1
            self._counts[identity] = current
            return current == 1

    def unregister(self, identity: str) -> bool:
        """注销一条连接。

        Returns:
            True 表示该设备的所有连接都已断开（调用方应广播"已断开"）。
        """
        if not identity:
            return False
        with self._lock:
            current = self._counts.get(identity, 0) - 1
            if current <= 0:
                self._counts.pop(identity, None)
                return True
            self._counts[identity] = current
            return False

    def active(self) -> dict[str, int]:
        """返回当前连接数的快照（用于诊断接口）。"""
        with self._lock:
            return dict(self._counts)

    def any_connected(self) -> bool:
        with self._lock:
            return bool(self._counts)

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()


def watch_connection(
    presence: RemotePresence,
    identity: str | None,
    on_connected: Callable[[str], None],
    on_disconnected: Callable[[str], None],
) -> Callable[[], None]:
    """登记连接并返回一个幂等的注销函数。

    幂等很关键：SSE 生成器的 finally 与异常分支可能都会调用它，
    重复调用不能让计数被减两次。
    """
    if not identity:
        return lambda: None

    first = presence.register(identity)
    if first:
        on_connected(identity)

    done = threading.Event()

    def _close() -> None:
        if done.is_set():
            return
        done.set()
        if presence.unregister(identity):
            on_disconnected(identity)

    return _close
