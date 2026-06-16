"""
test_file_monitor.py - 文件监控与稳定性等待测试

覆盖：
- 半截文件（仍在写入）不会被提前交付
- 回调异常不会中断监控
- 非图片扩展名被忽略

运行：pytest tests/test_file_monitor.py -v
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from problem_solver_agent import config, file_monitor


def test_wait_until_stable_detects_finished_file(tmp_path):
    path = tmp_path / "shot.jpg"
    path.write_bytes(b"x" * 100)
    # rounds=2、interval=0.05 → 至少需要两次一致采样
    assert file_monitor.wait_until_stable(path, interval=0.05, rounds=2, max_wait=2.0) is True


def test_wait_until_stable_missing_file(tmp_path):
    assert file_monitor.wait_until_stable(tmp_path / "absent.jpg", interval=0.05, max_wait=0.3) is False


def test_wait_until_stable_requires_consecutive_matches(tmp_path):
    """连续采样一致才算稳定：rounds 越大，返回所需时间越长。"""
    path = tmp_path / "static.jpg"
    path.write_bytes(b"x" * 100)

    started = time.time()
    assert file_monitor.wait_until_stable(path, interval=0.05, rounds=1, max_wait=2.0) is True
    one_round = time.time() - started

    started = time.time()
    assert file_monitor.wait_until_stable(path, interval=0.05, rounds=3, max_wait=2.0) is True
    three_rounds = time.time() - started

    assert three_rounds > one_round


def test_wait_until_stable_waits_while_file_grows(tmp_path):
    """活跃写入的文件不应被立刻放行。

    这是回归测试：早期实现只比较"相邻两次采样"，而正在追加的文件在写入
    间隙也可能出现两次相同大小，导致半截文件被送去 OCR。现在要求连续
    多次采样一致，因此写入期间不会返回。
    """
    path = tmp_path / "growing.jpg"
    path.write_bytes(b"x" * 10)

    stop = threading.Event()

    def writer():
        while not stop.is_set():
            with open(path, "ab") as handle:
                handle.write(b"y" * 50)
            time.sleep(0.02)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        time.sleep(0.1)
        # 文件持续增长：三次反悔都不该在 0.05s 内就判定稳定
        started = time.time()
        file_monitor.wait_until_stable(path, interval=0.05, rounds=3, max_wait=0.4)
        elapsed = time.time() - started
        assert elapsed >= 0.15, f"文件仍在增长却只等了 {elapsed:.3f}s"
    finally:
        stop.set()
        thread.join(timeout=1)


def test_event_handler_ignores_non_images(tmp_path):
    seen: list[Path] = []
    handler = file_monitor.ImageEventHandler(seen.append, wait_stable=False)

    class Event:
        is_directory = False
        src_path = str(tmp_path / "notes.txt")

    (tmp_path / "notes.txt").write_text("hi")
    handler.on_created(Event())
    assert seen == []


def test_event_handler_ignores_directories(tmp_path):
    seen: list[Path] = []
    handler = file_monitor.ImageEventHandler(seen.append, wait_stable=False)

    class Event:
        is_directory = True
        src_path = str(tmp_path)

    handler.on_created(Event())
    assert seen == []


def test_event_handler_delivers_images(tmp_path):
    seen: list[Path] = []
    handler = file_monitor.ImageEventHandler(seen.append, wait_stable=False)
    image = tmp_path / "shot.png"
    image.write_bytes(b"x")

    class Event:
        is_directory = False
        src_path = str(image)

    handler.on_created(Event())
    assert seen == [image]


def test_event_handler_waits_when_stability_enabled(tmp_path):
    """开启稳定性检查后，未写完的文件不会被交付。"""
    seen: list[Path] = []
    handler = file_monitor.ImageEventHandler(seen.append, wait_stable=True)
    image = tmp_path / "shot.png"
    # 不创建文件 → 等待超时后返回 False，不应回调
    class Event:
        is_directory = False
        src_path = str(image)

    handler.on_created(Event())
    assert seen == []


def test_callback_exception_does_not_propagate(tmp_path):
    """回调抛异常时监控线程不能挂掉。"""
    def broken(_path: Path) -> None:
        raise RuntimeError("boom")

    handler = file_monitor.ImageEventHandler(broken, wait_stable=False)
    image = tmp_path / "shot.png"
    image.write_bytes(b"x")

    class Event:
        is_directory = False
        src_path = str(image)

    handler.on_created(Event())  # 不应抛出


def test_allowed_extensions_cover_common_formats():
    for ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        assert ext in config.ALLOWED_EXTENSIONS
