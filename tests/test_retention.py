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
