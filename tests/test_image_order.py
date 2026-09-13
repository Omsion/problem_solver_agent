"""
test_image_order.py - 按"拍摄时间"给题目图片排序的测试

背景（2026-09-13 实测）：手机按时间顺序拍摄（第 1 页 → 第 8 页），但 Syncthing
按数据块同步，落到监控目录的顺序是随机的。日志里的真实片段：

    IMG_...164126.jpg → 组里第 1 张
    IMG_...164128.jpg → 第 2 张
    IMG_...164123.jpg → 第 3 张     ← 拍摄最早，却排第三
    IMG_...164131.jpg → 第 4 张

而多图题目是"逐页 OCR → 按顺序拼接题干 → 求解"，顺序错了整道题就废了。

运行：pytest tests/test_image_order.py -v
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PIL import Image

from problem_solver_agent import image_order
from problem_solver_agent.image_grouper import ImageGrouper


def _jpeg(path: Path, when: datetime | None = None) -> Path:
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path, format="JPEG")
    if when is not None:
        stamp = when.timestamp()
        os.utime(path, (stamp, stamp))
    return path


def _at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 时间来源：EXIF → 文件名 → mtime
# ---------------------------------------------------------------------------


def test_phone_filename_timestamp(tmp_path):
    path = _jpeg(tmp_path / "IMG_20260913_164123.jpg")
    stamp, source = image_order.photo_timestamp(path)
    assert source == "filename"
    assert datetime.fromtimestamp(stamp) == _at("2026-09-13 16:41:23")


def test_android_screenshot_filename(tmp_path):
    path = _jpeg(tmp_path / "Screenshot_20260913-164131.png")
    stamp, source = image_order.photo_timestamp(path)
    assert source == "filename"
    assert datetime.fromtimestamp(stamp) == _at("2026-09-13 16:41:31")


def test_chinese_screenshot_filename(tmp_path):
    path = _jpeg(tmp_path / "屏幕截图 2025-11-20 093340.png")
    stamp, source = image_order.photo_timestamp(path)
    assert source == "filename"
    assert datetime.fromtimestamp(stamp) == _at("2025-11-20 09:33:40")


def test_whatsapp_filename_keeps_date_only(tmp_path):
    """IMG-20260913-WA0001.jpg 只有日期，至少能保证按天排序。"""
    path = _jpeg(tmp_path / "IMG-20260913-WA0001.jpg")
    stamp, source = image_order.photo_timestamp(path)
    assert source == "filename"
    assert datetime.fromtimestamp(stamp).date() == _at("2026-09-13 00:00:00").date()


def test_falls_back_to_mtime(tmp_path):
    path = _jpeg(tmp_path / "随便起的名字.jpg", _at("2026-09-13 16:41:23"))
    stamp, source = image_order.photo_timestamp(path)
    assert source == "mtime"
    assert datetime.fromtimestamp(stamp) == _at("2026-09-13 16:41:23")


def test_exif_wins_over_filename(tmp_path):
    """转存/改名后文件名不再可信，EXIF 里的原始拍摄时间优先。"""
    path = tmp_path / "IMG_20260913_164123.jpg"
    image = Image.new("RGB", (8, 8), (10, 20, 30))
    exif = Image.Exif()
    exif[36867] = "2020:01:02 03:04:05"  # DateTimeOriginal
    image.save(path, format="JPEG", exif=exif)

    stamp, source = image_order.photo_timestamp(path)
    assert source == "exif"
    assert datetime.fromtimestamp(stamp) == _at("2020-01-02 03:04:05")


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------


def test_sort_puts_earliest_photo_first(tmp_path):
    """用户的实际场景：4 张图随便传到目录，排序后必须是拍摄顺序。"""
    ordered = [
        _jpeg(tmp_path / "IMG_20260913_164123.jpg"),
        _jpeg(tmp_path / "IMG_20260913_164126.jpg"),
        _jpeg(tmp_path / "IMG_20260913_164128.jpg"),
        _jpeg(tmp_path / "IMG_20260913_164131.jpg"),
    ]
    scrambled = [ordered[1], ordered[2], ordered[0], ordered[3]]

    assert image_order.sort_images_by_time(scrambled) == ordered


def test_sort_is_stable_when_timestamps_match(tmp_path):
    """时间完全相同时保持传入顺序，不要把用户手工排好的顺序打乱。"""
    first = _jpeg(tmp_path / "b.jpg", _at("2026-09-13 16:41:23"))
    second = _jpeg(tmp_path / "a.jpg", _at("2026-09-13 16:41:23"))

    assert image_order.sort_images_by_time([first, second]) == [first, second]


def test_describe_order_shows_time_and_source(tmp_path):
    paths = [
        _jpeg(tmp_path / "IMG_20260913_164131.jpg"),
        _jpeg(tmp_path / "IMG_20260913_164123.jpg"),
    ]
    description = image_order.describe_order(paths)
    assert "164123" in description and "164131" in description
    assert description.index("164123") < description.index("164131")
    assert "filename" in description


# ---------------------------------------------------------------------------
# 分组器：入队前就已经排好序
# ---------------------------------------------------------------------------


def test_grouper_submits_group_in_capture_order(tmp_path):
    grouper = ImageGrouper(num_workers=0)
    try:
        paths = [
            _jpeg(tmp_path / "IMG_20260913_164126.jpg"),
            _jpeg(tmp_path / "IMG_20260913_164128.jpg"),
            _jpeg(tmp_path / "IMG_20260913_164123.jpg"),
            _jpeg(tmp_path / "IMG_20260913_164131.jpg"),
        ]
        for path in paths:  # 模拟"随机到达"
            grouper.add_image(path)
        grouper._submit_group_to_queue()
        if grouper.timer is not None:
            grouper.timer.cancel()

        queued = grouper.task_queue.get_nowait()
        assert [p.name for p in queued] == [
            "IMG_20260913_164123.jpg",
            "IMG_20260913_164126.jpg",
            "IMG_20260913_164128.jpg",
            "IMG_20260913_164131.jpg",
        ]
    finally:
        if grouper.timer is not None:
            grouper.timer.cancel()
