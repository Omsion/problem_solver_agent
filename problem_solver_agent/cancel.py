"""
cancel.py - 任务取消原语

背景：原实现的取消是假的。`webapp/routes.py` 把取消请求记在
`_task_cancel_events` 字典里，但流水线从未读取过它，所以按了取消之后
线程继续跑、文件继续写，前端却显示"已取消"。

这里提供最小可用的取消协议：
- `CancelToken` 是线程安全的布尔标志，可由 HTTP 请求线程置位
- 流水线在**阶段边界**与**流式每个分片之间**调用 `raise_if_cancelled()`
- 置位后流水线抛出 `CancelledError`，由调用方决定保留已生成的部分内容
"""

from __future__ import annotations

import threading


class CancelledError(Exception):
    """任务被用户取消时抛出。"""


class CancelToken:
    """线程安全的取消标志。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """请求取消（幂等）。"""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """已取消则抛出 CancelledError。"""
        if self._event.is_set():
            raise CancelledError("任务已取消")

    def wait(self, timeout: float | None = None) -> bool:
        """等待取消信号（用于阻塞等待时也能及时退出）。"""
        return self._event.wait(timeout)


# 永不取消的令牌：CLI 场景（监控目录 + 无人交互）直接复用，省去分支判断
NEVER_CANCELLED = CancelToken()
