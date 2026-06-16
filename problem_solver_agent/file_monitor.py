"""
file_monitor.py - 文件系统监控模块

使用 'watchdog' 监控截图目录，把新出现的图片交给回调处理。

相比旧实现的两点变化：
1. 不再 `from .image_grouper import ImageGrouper`——监控模块不该反向依赖
   调度器，改为接收任意 callable，Web 端因此可以复用同一个监控器。
2. 新增文件稳定性检查：截图工具或浏览器写盘期间可能产生半截文件，
   直接送去 OCR 会得到残缺文本。这里等到文件大小稳定后再交付。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config
from .utils import setup_logger

logger = setup_logger()

# 文件稳定性判定参数。
# 注意：只比较"相邻两次采样"是不可靠的——活跃写入的文件在某个瞬间也可能
# 出现两次相同的大小（写入间隙），从而被误判为已写完。因此要求连续
# STABLE_ROUNDS 次采样大小都一致才放行，代价是引入 0.6s 的处理延迟
# （相比后续几十秒的识别求解可以忽略）。
STABILITY_CHECK_INTERVAL = 0.3
STABILITY_ROUNDS = 2
STABILITY_MAX_WAIT = 10.0


def wait_until_stable(
    path: Path,
    *,
    interval: float = STABILITY_CHECK_INTERVAL,
    rounds: int = STABILITY_ROUNDS,
    max_wait: float = STABILITY_MAX_WAIT,
) -> bool:
    """等到文件大小连续多次采样一致，才认为写盘完成。

    Args:
        interval: 两次采样之间的间隔（秒）
        rounds: 需要连续一致的采样次数（越大越保守）
        max_wait: 最长等待时间（秒），超时后仍返回 True 交给调用方处理

    Returns:
        文件是否可读（不存在则返回 False）
    """
    deadline = time.time() + max_wait
    previous: int | None = None
    stable = 0

    while time.time() < deadline:
        if not path.exists():
            return False
        try:
            size = path.stat().st_size
        except OSError:
            time.sleep(interval)
            continue

        if previous is not None and size == previous and size > 0:
            stable += 1
            if stable >= rounds:
                return True
        else:
            stable = 0
        previous = size
        time.sleep(interval)

    logger.warning("等待文件稳定超时（%.1fs）：%s", max_wait, path.name)
    return path.exists()


class ImageEventHandler(FileSystemEventHandler):
    """把符合扩展名的图片交给回调；回调由调用方提供（CLI 传分组器，Web 传分组器）。"""

    def __init__(self, callback: Callable[[Path], None], *, wait_stable: bool = True) -> None:
        self.callback = callback
        self.wait_stable = wait_stable

    def on_created(self, event) -> None:
        if event.is_directory:
            return
        src_path = Path(event.src_path)
        if src_path.suffix.lower() not in config.ALLOWED_EXTENSIONS:
            return

        logger.info("检测到新图片: %s", src_path.name)
        if self.wait_stable and not wait_until_stable(src_path):
            logger.warning("图片未就绪，忽略: %s", src_path.name)
            return
        try:
            self.callback(src_path)
        except Exception as exc:  # 回调异常不应杀死监控线程
            logger.error("处理新图片回调异常 %s: %s", src_path.name, exc, exc_info=True)


def start_monitoring(path: Path, callback: Callable[[Path], None], *, wait_stable: bool = True) -> Observer:
    """启动目录监控。

    Args:
        path: 需要监控的目录
        callback: 收到新图片时调用（接收图片路径）
        wait_stable: 是否等待文件写盘完成

    Returns:
        watchdog 的 Observer 对象，调用方负责在退出时 stop/join
    """
    handler = ImageEventHandler(callback, wait_stable=wait_stable)
    observer = Observer()
    observer.schedule(handler, str(path), recursive=False)
    observer.start()
    logger.info("文件监控已启动，正在监视目录: %s", path)
    return observer
