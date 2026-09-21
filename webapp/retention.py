"""
retention.py - 磁盘保留策略

背景（缺陷 N2）：原实现只删除超期任务的**解答文件**：
    def _cleanup_old(self, keep=100):
        paths = self.task_manager.cleanup_old_tasks(keep=100)
        for p in paths: Path(p).unlink(missing_ok=True)

而 `webapp/uploads/<task_id>/` 里的原图从不清理，实测已累积 16 个文件 / 45.6 MB
（数据库里只有 4 条任务）。截图类工具的磁盘占用会无限增长，这里统一治理：

- 超出保留数量的任务：解答文件 + 上传目录一起删除
- 超出保留天数的任务：同上
- 图片预处理缓存：按总量做 LRU 清理
- 阶段缓存：随任务记录删除

所有清理都是"尽力而为"，单个条目失败不影响其余清理。
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger("Retention")


def dir_size_bytes(path: Path) -> int:
    """统计目录内所有文件的总字节数（目录不存在返回 0）。"""
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def remove_tree(path: Path) -> bool:
    """删除目录树，失败只记录日志。"""
    if not path.exists():
        return False
    try:
        shutil.rmtree(path, ignore_errors=False)
        return True
    except OSError as exc:
        logger.warning("删除目录失败 %s: %s", path, exc)
        return False


def prune_image_cache(cache_dir: Path, max_bytes: int) -> int:
    """按最后修改时间做 LRU 清理，把缓存目录压回 max_bytes 以内。

    Returns:
        删除的文件数。
    """
    if max_bytes <= 0 or not cache_dir.exists():
        return 0

    entries: list[tuple[float, Path, int]] = []
    for item in cache_dir.rglob("*"):
        try:
            if item.is_file():
                stat = item.stat()
                entries.append((stat.st_mtime, item, stat.st_size))
        except OSError:
            continue

    total = sum(size for _, _, size in entries)
    if total <= max_bytes:
        return 0

    removed = 0
    for _, item, size in sorted(entries):  # 最旧的先删
        if total <= max_bytes:
            break
        try:
            item.unlink()
            total -= size
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info("图片缓存清理完成，删除 %d 个文件，当前占用 %.1f MB", removed, total / 1024 / 1024)
    return removed


def prune_uploads(
    upload_dir: Path, keep_task_dirs: set[str], *, allow_empty_keep: bool = False
) -> list[str]:
    """删除不在保留集合中的上传目录。

    Args:
        upload_dir: 上传根目录（其下每个子目录对应一个任务）
        keep_task_dirs: 需要保留的任务 id 集合
        allow_empty_keep: `keep_task_dirs` 为空时是否仍然执行删除。

    为什么默认**不**允许空集合：2026-09-20 事故中 `uploads/*/` 下 8 个真实上传目录
    被整批删除，而这 8 个目录都对应真实 DB 里的任务 —— 删除发生在"任务库为空/不是
    这一份"的调用上下文里。该函数把「保留集合」与「上传目录」当成两个独立入参，
    调用方很容易配错（测试用 tmp 库 + 全局上传目录、`DB_PATH` 被覆盖的第二实例等），
    而这种配错的表现恰好就是 **空集合**。删除不可逆，因此默认拒绝并告警；
    确实要清空的调用方显式传 `allow_empty_keep=True` 承担后果。

    Returns:
        实际删除的目录名列表。
    """
    if not upload_dir.exists():
        return []
    if not keep_task_dirs and not allow_empty_keep:
        candidates = [child.name for child in upload_dir.iterdir() if child.is_dir()]
        if candidates:
            logger.warning(
                "拒绝清理上传目录 %s：保留集合为空但有 %d 个子目录。"
                "这通常意味着 DB_PATH 与 UPLOAD_DIR 不是同一套配置"
                "（继续删除会不可逆地丢掉真实上传原图）。"
                "确实要清空请显式传 allow_empty_keep=True。",
                upload_dir, len(candidates),
            )
        return []
    removed: list[str] = []
    for child in upload_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name in keep_task_dirs:
            continue
        if remove_tree(child):
            removed.append(child.name)
    if removed:
        logger.info("清理上传目录 %d 个: %s", len(removed), ", ".join(removed))
    return removed


def stale_uploads(upload_dir: Path, db_task_ids: set[str]) -> list[str]:
    """找出"数据库里已经没有记录"的上传目录（历史上被删任务留下的残留）。"""
    if not upload_dir.exists():
        return []
    return [
        child.name
        for child in upload_dir.iterdir()
        if child.is_dir() and child.name not in db_task_ids
    ]


def cutoff_timestamp(days: int) -> float:
    """返回 N 天前的时间戳（秒）。days <= 0 表示不按时间清理。"""
    if days <= 0:
        return 0.0
    return time.time() - days * 86400
