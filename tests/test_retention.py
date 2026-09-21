"""
test_retention.py - 磁盘保留策略测试

覆盖缺陷 N2：原实现只删除解答文件，`uploads/<task_id>/` 从不清理，
实测累积 45.6 MB。这里验证上传目录、孤立目录、图片缓存都被正确回收。

运行：pytest tests/test_retention.py -v
"""

from __future__ import annotations

import os
import time

from webapp.retention import (
    cutoff_timestamp,
    dir_size_bytes,
    prune_image_cache,
    prune_uploads,
    remove_tree,
    stale_uploads,
)


def _make_upload(root, task_id: str, files: int = 2, size: int = 100) -> None:
    task_dir = root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    for i in range(files):
        (task_dir / f"img{i}.jpg").write_bytes(b"x" * size)


def test_dir_size_bytes(tmp_path):
    _make_upload(tmp_path, "t1", files=3, size=10)
    assert dir_size_bytes(tmp_path) == 30


def test_dir_size_missing_directory(tmp_path):
    assert dir_size_bytes(tmp_path / "nope") == 0


def test_remove_tree(tmp_path):
    _make_upload(tmp_path, "t1")
    assert remove_tree(tmp_path / "t1") is True
    assert not (tmp_path / "t1").exists()
    # 不存在时返回 False 而不是抛错
    assert remove_tree(tmp_path / "t1") is False


def test_prune_uploads_keeps_whitelisted(tmp_path):
    for task_id in ("keep1", "keep2", "drop1"):
        _make_upload(tmp_path, task_id)

    removed = prune_uploads(tmp_path, {"keep1", "keep2"})

    assert removed == ["drop1"]
    assert (tmp_path / "keep1").exists()
    assert (tmp_path / "keep2").exists()
    assert not (tmp_path / "drop1").exists()


def test_prune_uploads_ignores_files(tmp_path):
    (tmp_path / ".gitkeep").write_text("")
    _make_upload(tmp_path, "task")
    removed = prune_uploads(tmp_path, {"task"})
    assert removed == []
    assert (tmp_path / ".gitkeep").exists()


def test_stale_uploads_detects_orphans(tmp_path):
    for task_id in ("known", "orphan"):
        _make_upload(tmp_path, task_id)
    assert stale_uploads(tmp_path, {"known"}) == ["orphan"]


# ---------------------------------------------------------------------------
# 上传目录清理的安全护栏（2026-09-20 事故回归测试）
#
# 事故：`uploads/*/` 下 8 个真实上传目录被整批删除，而这 8 个目录都对应真实 DB 里的
# 任务 —— 删除发生在"任务库为空/不是这一份"的上下文里（`prune_uploads` 的
# `keep_task_dirs` 与 `upload_dir` 是两个独立入参，很容易配成不同来源）。
# 删除不可逆，因此空保留集合默认拒绝执行。
# ---------------------------------------------------------------------------


def test_prune_uploads_refuses_when_keep_set_is_empty(tmp_path):
    """空保留集合 + 有子目录 = 配置配错的特征，必须拒绝删除并保留现场。"""
    for task_id in ("a", "b"):
        _make_upload(tmp_path, task_id)

    assert prune_uploads(tmp_path, set()) == []

    assert (tmp_path / "a").exists()
    assert (tmp_path / "b").exists()


def test_prune_uploads_allows_empty_keep_when_explicitly_requested(tmp_path):
    """确实要清空的调用方必须显式承担后果（逃生开关）。"""
    _make_upload(tmp_path, "a")

    assert prune_uploads(tmp_path, set(), allow_empty_keep=True) == ["a"]
    assert not (tmp_path / "a").exists()


def test_prune_uploads_empty_keep_on_empty_dir_is_noop(tmp_path):
    """目录本来就是空的：不报警、不报错（避免噪音）。"""
    assert prune_uploads(tmp_path, set()) == []


def test_startup_cleanup_does_not_wipe_uploads_for_an_empty_task_db(tmp_path, monkeypatch):
    """`_startup_cleanup` 拿到"另一个任务库"时不得删掉真实上传目录。

    这正是事故路径：任务库由调用方传入，上传目录取自全局配置，两者可以来自不同来源。
    """
    from webapp import app as webapp_app
    from webapp import config as web_config
    from webapp.models import TaskManager

    uploads = tmp_path / "uploads"
    for task_id in ("t1", "t2"):
        _make_upload(uploads, task_id)
    monkeypatch.setattr(web_config, "UPLOAD_DIR", uploads, raising=False)

    # 一个与上传目录无关的空任务库
    webapp_app._startup_cleanup(TaskManager(tmp_path / "tasks.db"))  # noqa: SLF001

    assert (uploads / "t1").exists()
    assert (uploads / "t2").exists()


def test_prune_image_cache_removes_oldest_first(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    now = time.time()
    # 三个文件各 100 字节（共 300），容量限制 250：只需删掉最旧的一个
    for index, name in enumerate(["old.jpg", "mid.jpg", "new.jpg"]):
        path = cache / name
        path.write_bytes(b"x" * 100)
        stamp = now - 300 + index * 100
        os.utime(path, (stamp, stamp))

    removed = prune_image_cache(cache, max_bytes=250)

    assert removed == 1
    assert dir_size_bytes(cache) <= 250
    # 最旧的被删，较新的保留
    assert not (cache / "old.jpg").exists()
    assert (cache / "new.jpg").exists()
    assert (cache / "mid.jpg").exists()


def test_prune_image_cache_stops_as_soon_as_under_limit(tmp_path):
    """只要降到上限以内就停手，不做多余的删除。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    now = time.time()
    for index, name in enumerate(["a.jpg", "b.jpg", "c.jpg", "d.jpg"]):
        path = cache / name
        path.write_bytes(b"x" * 100)
        stamp = now - 400 + index * 100
        os.utime(path, (stamp, stamp))

    removed = prune_image_cache(cache, max_bytes=350)

    assert removed == 1  # 400 - 100 = 300 ≤ 350
    assert dir_size_bytes(cache) == 300


def test_prune_image_cache_noop_when_under_limit(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "a.jpg").write_bytes(b"x" * 10)
    assert prune_image_cache(cache, max_bytes=1000) == 0


def test_prune_image_cache_disabled_with_zero(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "a.jpg").write_bytes(b"x" * 1000)
    assert prune_image_cache(cache, max_bytes=0) == 0
    assert (cache / "a.jpg").exists()


def test_cutoff_timestamp():
    assert cutoff_timestamp(0) == 0.0
    now = time.time()
    cutoff = cutoff_timestamp(7)
    assert abs((now - cutoff) - 7 * 86400) < 5


def test_prune_uploads_missing_root(tmp_path):
    assert prune_uploads(tmp_path / "absent", set()) == []
    assert stale_uploads(tmp_path / "absent", set()) == []
