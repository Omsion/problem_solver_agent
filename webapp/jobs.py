"""
jobs.py - 任务注册表

集中管理"哪个任务正在跑、用什么令牌可以取消它、同时最多跑几个"。

背景：
- `webapp/routes.py` 原先把取消令牌存在 `_task_cancel_events` 里，但流水线
  从未读取，取消是假动作；而且 `_processing_locks` 只在任务真正结束时释放，
  取消后立刻重试会因为拿不到锁而永久失败。
- 自动导入与手动上传此前各跑各的，没有并发上限，高峰期会把 API 配额打满，
  导致**所有**任务一起超时。

取消令牌与并发上限都收敛到这一个注册表里。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from problem_solver_agent.cancel import CancelToken

logger = logging.getLogger("TaskRegistry")


class TaskRegistry:
    """正在运行任务的登记处。"""

    def __init__(self, max_concurrent: int = 2) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, CancelToken] = {}
        self._max_concurrent = max(1, max_concurrent)
        # 用信号量限制并发，避免把上游 API 配额打满
        self._slots = threading.BoundedSemaphore(self._max_concurrent)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._tokens

    def running_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._tokens)

    def start(self, task_id: str) -> CancelToken | None:
        """登记一个任务并返回其取消令牌。

        Returns:
            新令牌；若该任务已在运行则返回 None（调用方应跳过重复启动）。
        """
        with self._lock:
            if task_id in self._tokens:
                return None
            token = CancelToken()
            self._tokens[task_id] = token
            return token

    def cancel(self, task_id: str) -> bool:
        """请求取消。返回是否确实存在正在运行的任务。"""
        with self._lock:
            token = self._tokens.get(task_id)
        if token is None:
            return False
        token.cancel()
        logger.info("已请求取消任务 %s", task_id)
        return True

    def finish(self, task_id: str) -> None:
        with self._lock:
            self._tokens.pop(task_id, None)

    # ---- 并发控制 ----

    def acquire_slot(self, timeout: float | None = None) -> bool:
        """获取一个执行槽位（阻塞直到有空位）。"""
        return self._slots.acquire(timeout=timeout) if timeout is not None else self._slots.acquire()

    def release_slot(self) -> None:
        try:
            self._slots.release()
        except ValueError:
            # 释放次数多于获取次数：说明调用方逻辑有误，但不该让线程崩溃
            logger.warning("多余的信号量释放被忽略")

    def run_with_slot(self, task_id: str, work: Callable[[CancelToken], None]) -> bool:
        """在槽位内执行 work(token)，并保证令牌被清理。

        Args:
            work: 接收本任务取消令牌的可调用对象。

        Returns:
            是否真正执行（任务已在运行时不执行）。
        """
        token = self.start(task_id)
        if token is None:
            logger.warning("任务 %s 已在运行，跳过重复启动", task_id)
            return False

        acquired = False
        try:
            self.acquire_slot()
            acquired = True
            work(token)
        finally:
            if acquired:
                self.release_slot()
            self.finish(task_id)
        return True
