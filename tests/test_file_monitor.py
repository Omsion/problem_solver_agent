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


# ---------------------------------------------------------------------------
# 改名就位：Syncthing 等同步工具的投递方式
# ---------------------------------------------------------------------------


class _Event:
    """watchdog 事件的最小替身（只要 is_directory 与路径字段）。"""

    def __init__(self, path=None, *, is_directory: bool = False, dest_path=None) -> None:
        self.is_directory = is_directory
        if path is not None:
            setattr(self, "src_path", str(path))
        if dest_path is not None:
            setattr(self, "dest_path", str(dest_path))


def test_on_moved_delivers_image(tmp_path):
    """回归：同步工具是"先写 .syncthing.x.jpg.tmp 再改名"，旧实现漏掉改名这一步，
    导致照片躺在监控目录里永远不被处理。"""
    seen: list[Path] = []
    handler = file_monitor.ImageEventHandler(seen.append, wait_stable=False)
    image = tmp_path / "IMG_20260913_160727.jpg"
    image.write_bytes(b"x")

    handler.on_moved(
        _Event(tmp_path / ".syncthing.IMG_20260913_160727.jpg.tmp", dest_path=image)
    )
    assert seen == [image]


def test_on_moved_ignores_directories_and_non_images(tmp_path):
    seen: list[Path] = []
    handler = file_monitor.ImageEventHandler(seen.append, wait_stable=False)
    (tmp_path / "notes.txt").write_text("hi")

    handler.on_moved(_Event(tmp_path / "a", dest_path=tmp_path / "notes.txt"))
    handler.on_moved(_Event(tmp_path / "a", is_directory=True, dest_path=tmp_path / "dir"))
    assert seen == []


def test_temp_and_aux_files_are_never_candidates(tmp_path):
    for name in (
        ".syncthing.IMG_1.jpg.tmp",
        "IMG_1.jpg.tmp",
        "IMG_1.jpg.part",
        ".stfolder",
        ".stignore",
        ".cache",
        "notes.txt",
    ):
        assert file_monitor.is_candidate_image(tmp_path / name) is False, name
    for name in ("IMG_1.jpg", "IMG_2.JPEG", "shot.png", "x.webp"):
        assert file_monitor.is_candidate_image(tmp_path / name) is True, name


# ---------------------------------------------------------------------------
# 去重账本
# ---------------------------------------------------------------------------


def test_ledger_round_trip(tmp_path):
    ledger_path = tmp_path / "seen.json"
    image = tmp_path / "a.jpg"
    image.write_bytes(b"x")

    ledger = file_monitor.SeenLedger(ledger_path)
    assert ledger.seen(image) is False
    ledger.mark(image)
    ledger.flush()
    assert ledger_path.exists()

    reloaded = file_monitor.SeenLedger(ledger_path)
    assert reloaded.seen(image) is True


def test_ledger_tolerates_corrupt_file(tmp_path):
    ledger_path = tmp_path / "seen.json"
    ledger_path.write_text("{ 这不是 json", encoding="utf-8")
    ledger = file_monitor.SeenLedger(ledger_path)
    assert ledger.seen(tmp_path / "a.jpg") is False


def test_ledger_treats_missing_file_as_unseen(tmp_path):
    ledger = file_monitor.SeenLedger(tmp_path / "seen.json")
    assert ledger.seen(tmp_path / "absent.jpg") is False
    ledger.mark(tmp_path / "absent.jpg")  # 不能抛异常


def test_ledger_keeps_recent_entries_only(tmp_path):
    ledger = file_monitor.SeenLedger(None, limit=3)
    images = []
    for index in range(5):
        image = tmp_path / f"p{index}.jpg"
        image.write_bytes(b"x")
        images.append(image)
        ledger.mark(image)
    # 上限内的最近 3 个仍在账本里
    assert ledger.seen(images[-1]) is True
    assert ledger.seen(images[-3]) is True
    assert ledger.seen(images[0]) is False


# ---------------------------------------------------------------------------
# 补偿扫描
# ---------------------------------------------------------------------------


def test_group_by_mtime_gap_splits_distant_photos(tmp_path):
    import os

    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    third = tmp_path / "c.jpg"
    for path in (first, second, third):
        path.write_bytes(b"x")

    base = time.time()
    os.utime(first, (base, base))
    os.utime(second, (base + 3, base + 3))  # 同一次连拍
    os.utime(third, (base + 600, base + 600))  # 另一道题

    groups = file_monitor.group_by_mtime_gap([third, first, second], 8.0)
    assert [[p.name for p in group] for group in groups] == [
        ["a.jpg", "b.jpg"],
        ["c.jpg"],
    ]


def test_scan_once_delivers_only_candidates_once(tmp_path):
    delivered: list = []
    ledger = file_monitor.SeenLedger(None)
    (tmp_path / "p1.jpg").write_bytes(b"1")
    (tmp_path / "note.txt").write_text("x")
    (tmp_path / ".syncthing.p2.jpg.tmp").write_bytes(b"2")

    assert file_monitor.scan_once(tmp_path, delivered.append, ledger, wait_stable=False) == 1
    assert [p.name for group in delivered for p in group] == ["p1.jpg"]

    # 再来一次：同一张图不能重复投递
    assert file_monitor.scan_once(tmp_path, delivered.append, ledger, wait_stable=False) == 0
    assert len(delivered) == 1


def test_scan_once_skips_files_being_processed(tmp_path):
    delivered: list = []
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    (tmp_path / "p1.jpg").write_bytes(b"1")
    (lock_dir / ".p1.lock").touch()
    ledger = file_monitor.SeenLedger(None)

    assert (
        file_monitor.scan_once(
            tmp_path, delivered.append, ledger, lock_dir=lock_dir, wait_stable=False
        )
        == 0
    )
    assert delivered == []


# ---------------------------------------------------------------------------
# 被中断的任务必须能自动重投（2026-09-22：满屏"已投递过，跳过重复事件"的根因）
#
# 事故：`_execute_pipeline` 的 finally 在强杀时不执行 → 锁永久残留；旧版
# `scan_once` 见到锁就把图片记进账本（"记账以避免重复投递"），而它其实从未被处理。
# 于是每次启动都 skip，用户什么都解不出来。两处修复合起来保证可恢复：
#   1. 见到锁只"跳过本轮"，**不记账**；
#   2. 启动时清掉上一次进程残留的锁。
# ---------------------------------------------------------------------------


def test_scan_once_does_not_mark_locked_files_as_delivered(tmp_path):
    """锁只表示"本轮别投"，不表示"已完成" —— 否则强杀后图片会被永久跳过。"""
    delivered: list = []
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    image = tmp_path / "p1.jpg"
    image.write_bytes(b"1")
    lock = lock_dir / ".p1.lock"
    lock.touch()
    ledger = file_monitor.SeenLedger(None)

    file_monitor.scan_once(tmp_path, delivered.append, ledger, lock_dir=lock_dir, wait_stable=False)
    assert ledger.seen(image) is False, "带锁跳过的文件绝不能记账"

    # 锁消失（例如进程下次启动时清理掉了）之后，同一张图必须能被补投
    lock.unlink()
    assert (
        file_monitor.scan_once(
            tmp_path, delivered.append, ledger, lock_dir=lock_dir, wait_stable=False
        )
        == 1
    )
    assert [p.name for group in delivered for p in group] == ["p1.jpg"]


def test_recover_stale_locks_removes_orphan_locks(tmp_path):
    """启动清理：把残留锁删掉，并如实返回被恢复的文件名。"""
    (tmp_path / ".p1.lock").touch()
    (tmp_path / ".p2.lock").touch()
    (tmp_path / "正常解答.md").write_text("x", encoding="utf-8")  # 非锁文件不能碰

    recovered = file_monitor.recover_stale_locks(tmp_path)

    assert recovered == ["p1", "p2"]
    assert not (tmp_path / ".p1.lock").exists()
    assert not (tmp_path / ".p2.lock").exists()
    assert (tmp_path / "正常解答.md").exists()
    # 幂等：没有锁时返回空列表而不是报错
    assert file_monitor.recover_stale_locks(tmp_path) == []


def test_recover_stale_locks_missing_dir_is_noop(tmp_path):
    assert file_monitor.recover_stale_locks(tmp_path / "absent") == []


def test_interrupted_run_is_retried_after_startup_recovery(tmp_path):
    """端到端回归：投递 → 建锁 → 强杀（锁残留）→ 重启清理 → 重新投递。"""
    delivered: list = []
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    image = tmp_path / "p1.jpg"
    image.write_bytes(b"1")
    ledger = file_monitor.SeenLedger(None)

    # 第一次运行：投递成功并记账，随后流水线建锁（模拟 `_execute_pipeline`）
    assert file_monitor.scan_once(tmp_path, delivered.append, ledger, lock_dir=lock_dir, wait_stable=False) == 1
    (lock_dir / ".p1.lock").touch()
    # 强杀：`finally` 没跑，锁留在盘上
    assert ledger.seen(image) is True
    assert file_monitor.scan_once(tmp_path, delivered.append, ledger, lock_dir=lock_dir, wait_stable=False) == 0

    # 用户发现没解出来 → 重启（新的账本实例读同一个文件）+ 启动清锁
    file_monitor.recover_stale_locks(lock_dir)
    restarted_ledger = file_monitor.SeenLedger(None)
    # 账本里仍是"已投递"，所以还需要清掉那条记录（tools/requeue.py 做的事）
    restarted_ledger.forget(["p1.jpg"])

    assert (
        file_monitor.scan_once(
            tmp_path, delivered.append, restarted_ledger, lock_dir=lock_dir, wait_stable=False
        )
        == 1
    )
    assert len(delivered) == 2, "被中断的图片必须能被重新投递"


def test_deliver_now_bypasses_the_age_gate(tmp_path):
    """显式重投必须绕开年龄闸门 —— 被中断的图片往往已经放了好几天。

    事故现场：那批图是 6 天前拍的，清掉账本后 `scan_once(max_age_minutes=120)`
    仍然全部跳过，用户重启多少次都等不到结果。
    """
    import os

    monitor = tmp_path / "monitor"
    monitor.mkdir()
    old = monitor / "old.jpg"
    old.write_bytes(b"1")
    stale = time.time() - 6 * 24 * 3600
    os.utime(old, (stale, stale))

    ledger = file_monitor.SeenLedger(None)
    delivered: list = []

    # 自动补偿路径：受年龄闸门约束，什么都不做
    assert (
        file_monitor.scan_once(
            monitor, delivered.append, ledger, max_age_minutes=120, wait_stable=False
        )
        == 0
    )
    assert delivered == []

    # 显式重投路径：用户点名要它，年龄不是理由
    result = file_monitor.deliver_now(monitor, ["old.jpg"], delivered.append, ledger)
    assert [p.name for p in result] == ["old.jpg"]
    assert len(delivered) == 1
    assert ledger.seen(old) is True, "投递成功后必须记账，避免下次重复投"


def test_deliver_now_groups_by_mtime_gap(tmp_path):
    """分组规则与正常运行时一致：同一段的连拍算一题，孤立的老图各自成题。"""
    import os

    monitor = tmp_path / "monitor"
    monitor.mkdir()
    base = time.time() - 3600
    names = ["a.jpg", "b.jpg", "c.jpg"]
    for index, name in enumerate(names):
        path = monitor / name
        path.write_bytes(b"x")
        stamp = base + (index * 5 if index < 2 else 600)  # a,b 相邻；c 隔了 10 分钟
        os.utime(path, (stamp, stamp))

    ledger = file_monitor.SeenLedger(None)
    groups: list = []
    file_monitor.deliver_now(monitor, names, groups.append, ledger)

    assert [[p.name for p in group] for group in groups] == [["a.jpg", "b.jpg"], ["c.jpg"]]


def test_deliver_now_ignores_missing_and_non_images(tmp_path):
    monitor = tmp_path / "monitor"
    monitor.mkdir()
    (monitor / "note.txt").write_text("x", encoding="utf-8")
    ledger = file_monitor.SeenLedger(None)
    groups: list = []

    result = file_monitor.deliver_now(
        monitor, ["note.txt", "不存在.jpg"], groups.append, ledger
    )

    assert result == []
    assert groups == []


def test_deliver_now_does_not_mark_a_failed_group(tmp_path):
    """投递抛异常时不记账 —— 否则这次失败会变成永久跳过。"""
    monitor = tmp_path / "monitor"
    monitor.mkdir()
    image = monitor / "p.jpg"
    image.write_bytes(b"x")
    ledger = file_monitor.SeenLedger(None)

    def boom(group):
        raise RuntimeError("队列挂了")

    assert file_monitor.deliver_now(monitor, ["p.jpg"], boom, ledger) == []
    assert ledger.seen(image) is False


def test_ledger_forget_removes_only_named_entries(tmp_path):
    """`forget` 只清指定文件名，不清空整个账本（避免把已解出的图重新投一遍）。"""
    ledger_path = tmp_path / "seen.json"
    ledger = file_monitor.SeenLedger(ledger_path)
    for name in ("keep.jpg", "drop.jpg"):
        path = tmp_path / name
        path.write_bytes(b"x")
        ledger.mark(path)

    removed = ledger.forget(["drop.jpg"])

    assert removed == ["drop.jpg"]
    assert ledger.names() == ["keep.jpg"]
    # 落盘后重新加载仍是同一份
    assert file_monitor.SeenLedger(ledger_path).names() == ["keep.jpg"]


def test_ledger_forget_all(tmp_path):
    ledger_path = tmp_path / "seen.json"
    ledger = file_monitor.SeenLedger(ledger_path)
    for name in ("a.jpg", "b.jpg"):
        path = tmp_path / name
        path.write_bytes(b"x")
        ledger.mark(path)

    assert ledger.forget() == ["a.jpg", "b.jpg"]
    assert ledger.names() == []
    assert file_monitor.SeenLedger(ledger_path).names() == []
    assert ledger.forget() == []  # 幂等


def test_scan_once_skips_too_old_files(tmp_path):
    import os

    delivered: list = []
    ledger = file_monitor.SeenLedger(None)
    old = tmp_path / "old.jpg"
    old.write_bytes(b"1")
    stale = time.time() - 3 * 3600
    os.utime(old, (stale, stale))

    assert (
        file_monitor.scan_once(
            tmp_path, delivered.append, ledger, max_age_minutes=60, wait_stable=False
        )
        == 0
    )
    assert delivered == []


def test_scan_once_retries_next_round_when_delivery_fails(tmp_path):
    ledger = file_monitor.SeenLedger(None)
    (tmp_path / "p1.jpg").write_bytes(b"1")
    attempts: list = []

    def boom(group):
        attempts.append(group)
        raise RuntimeError("队列挂了")

    assert file_monitor.scan_once(tmp_path, boom, ledger, wait_stable=False) == 0
    assert len(attempts) == 1
    # 投递失败不记账，所以下一轮还会补上
    assert file_monitor.scan_once(tmp_path, boom, ledger, wait_stable=False) == 0
    assert len(attempts) == 2


def _wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_start_monitoring_catches_up_and_handles_rename(tmp_path, monkeypatch):
    """端到端：启动补投 + 改名就位 + 停止时不悬挂线程。"""
    monkeypatch.setattr(config, "MONITOR_RESCAN_INTERVAL", 0.2)
    monkeypatch.setattr(config, "MONITOR_CATCHUP_MAX_AGE_MINUTES", 0)

    delivered: list[Path] = []
    pre_existing = tmp_path / "before.jpg"
    pre_existing.write_bytes(b"1")

    handle = file_monitor.start_monitoring(
        tmp_path,
        delivered.append,
        on_group=lambda group: delivered.extend(group),
        ledger=file_monitor.SeenLedger(None),
        wait_stable=False,
    )
    try:
        # 启动前就存在的文件由首次补偿扫描补上
        assert _wait_for(lambda: pre_existing in delivered)

        # 模拟 Syncthing：先写临时文件，再改名就位
        temp_file = tmp_path / ".syncthing.after.jpg.tmp"
        temp_file.write_bytes(b"2")
        renamed = tmp_path / "after.jpg"
        temp_file.replace(renamed)
        assert _wait_for(lambda: renamed in delivered)

        # 临时文件本身绝不能被投递
        assert all(p.suffix.lower() != ".tmp" for p in delivered)
    finally:
        handle.stop()
        handle.join(timeout=3)

    assert handle.is_alive() is False
    assert handle.rescan_thread is None or not handle.rescan_thread.is_alive()

