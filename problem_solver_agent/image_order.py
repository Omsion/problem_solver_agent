"""
image_order.py - 把一组题目图片按"拍摄时间"排好序

为什么需要这个模块（2026-09-13 实测）：
手机拍照是按时间顺序的（第 1 页 → 第 8 页），但 Syncthing 是按数据块同步的，
落到监控目录的顺序是**随机的**。日志里就出现过：

    IMG_...164126.jpg → 组里第 1 张
    IMG_...164128.jpg → 第 2 张
    IMG_...164123.jpg → 第 3 张     ← 拍摄最早，却排第三
    IMG_...164131.jpg → 第 4 张

而多图题目的处理是"逐页 OCR → 按顺序拼接成题干 → 求解"，顺序错了整道题就废了。

排序依据按可靠性依次回退：
1. **EXIF `DateTimeOriginal`**：相机/手机原始拍摄时间，最可靠（微信/QQ 转存改名后仍保留）；
2. **文件名里的时间戳**：`IMG_20260913_164123.jpg`、`Screenshot_20260913-164123.png`、
   `屏幕截图 2025-11-20 093340.png`、`IMG-20260913-WA0001.jpg`（只有日期，按日期排）；
3. **文件 mtime**：Syncthing 会保留手机端时间，所以它通常也等于拍摄时间。

任何一步都不依赖文件在磁盘上的出现顺序，因此组内顺序与同步顺序无关。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("AgentLogger")

# EXIF 标签号
_EXIF_DATETIME_ORIGINAL = 36867  # DateTimeOriginal（Exif 子 IFD）
_EXIF_DATETIME_DIGITIZED = 36868  # DateTimeDigitized
_EXIF_DATETIME = 306  # DateTime（IFD0，通常是最后修改时间）
_EXIF_SUB_IFD = 0x8769  # ExifOffset
_EXIF_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")

# 文件名里的时间戳格式（按优先级）
_FILENAME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # IMG_20260913_164123.jpg / Screenshot_20260913-164123.png / VID_20260913_164123
    (re.compile(r"(20\d{6})[_-](\d{6})"), "datetime"),
    # 屏幕截图 2025-11-20 093340.png
    (re.compile(r"(20\d{2})-(\d{2})-(\d{2})[ _T](\d{2})(\d{2})(\d{2})"), "iso_datetime"),
    # 2025-11-20_09-33-40 / 2025-11-20 09.33.40
    (re.compile(r"(20\d{2})[-_.](\d{2})[-_.](\d{2})[ _T](\d{2})[-_.](\d{2})[-_.](\d{2})"), "iso_datetime"),
    # IMG-20260913-WA0001.jpg（WhatsApp：只有日期，时间按 00:00 处理）
    (re.compile(r"(20\d{2})(\d{2})(\d{2})"), "date_only"),
)


def _parse_exif_datetime(raw: object) -> float | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    for fmt in _EXIF_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def _exif_timestamp(path: Path) -> float | None:
    """读 EXIF 拍摄时间；没有或读不动就返回 None。"""
    try:
        from PIL import Image  # 延迟导入：PIL 只在真的需要读 EXIF 时用

        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return None
            candidates: list[object] = []
            try:
                sub = exif.get_ifd(_EXIF_SUB_IFD)
            except Exception:  # pragma: no cover - 某些图没有子 IFD
                sub = {}
            if sub:
                candidates.append(sub.get(_EXIF_DATETIME_ORIGINAL))
                candidates.append(sub.get(_EXIF_DATETIME_DIGITIZED))
            # Pillow 写出来的图，这些标签可能落在 IFD0 上，所以两条路都查
            candidates.append(exif.get(_EXIF_DATETIME_ORIGINAL))
            candidates.append(exif.get(_EXIF_DATETIME))
            for raw in candidates:
                parsed = _parse_exif_datetime(raw)
                if parsed is not None:
                    return parsed
    except Exception as exc:  # 损坏文件 / 非图片 / 无 EXIF 支持
        logger.debug("读取 EXIF 失败 %s: %s", path.name, exc)
    return None


def _filename_timestamp(path: Path) -> float | None:
    """从文件名里抠拍摄时间；解析不出返回 None。"""
    name = path.name
    for pattern, kind in _FILENAME_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        try:
            if kind == "datetime":
                date_part, time_part = match.group(1), match.group(2)
                return datetime.strptime(f"{date_part}{time_part}", "%Y%m%d%H%M%S").timestamp()
            if kind == "iso_datetime":
                y, mo, d, h, mi, s = match.groups()
                return datetime(
                    int(y), int(mo), int(d), int(h), int(mi), int(s)
                ).timestamp()
            # date_only：只保证同一天内的相对顺序（按文件名再兜一层）
            y, mo, d = match.groups()
            return datetime(int(y), int(mo), int(d)).timestamp()
        except ValueError:
            continue
    return None


def photo_timestamp(path: str | Path) -> tuple[float, str]:
    """返回（拍摄时间, 来源）。

    来源取值：`exif` / `filename` / `mtime`。永远有值——最差也会退回 mtime，
    这样调用方不必处理"没有时间"的分支。
    """
    candidate = Path(path)
    exif_time = _exif_timestamp(candidate)
    if exif_time is not None:
        return exif_time, "exif"

    file_time = _filename_timestamp(candidate)
    if file_time is not None:
        return file_time, "filename"

    try:
        return candidate.stat().st_mtime, "mtime"
    except OSError:
        return 0.0, "missing"


def sort_images_by_time(paths: Sequence[str | Path]) -> list[Path]:
    """按拍摄时间升序排列（最早的在最前面 = 题目的第一页）。

    时间相同（或都读不到）时保持传入顺序（稳定排序），避免把用户手工指定的
    顺序打乱。EXIF 里带秒、文件名带秒，正常情况下不会撞车。
    """
    candidates = [Path(p) for p in paths]
    ordered = sorted(
        ((photo_timestamp(path)[0], index, path) for index, path in enumerate(candidates)),
        key=lambda item: (item[0], item[1]),
    )
    return [path for _, _, path in ordered]


def describe_order(paths: Sequence[str | Path]) -> str:
    """给日志用的一行描述：`最早 → 最晚`（附来源标记）。"""
    if not paths:
        return ""
    parts = []
    for path in sort_images_by_time(paths):
        timestamp, source = photo_timestamp(path)
        stamp = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S") if timestamp else "--:--:--"
        parts.append(f"{Path(path).name}({stamp}/{source})")
    return " → ".join(parts)
