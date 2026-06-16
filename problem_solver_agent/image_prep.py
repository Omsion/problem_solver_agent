"""
image_prep.py - 发送给视觉模型前的图片预处理

背景（实测数据）：项目保存的截图是 2288×1764 的 JPEG，单张 2.96 MB，
base64 编码后 3.9 MB。一次"分类 + 4 张 OCR"的流程会把约 15 MB 的 base64
塞进网络请求，这是整条链路最大的开销来源。

本模块做四件事：
1. EXIF 方向校正（手机截图/相册图片常见）
2. 等比缩放到最长边不超过 IMAGE_MAX_EDGE（默认 1600）
3. 统一转成 JPEG（RGBA/P/LA 以白底合成），质量 IMAGE_JPEG_QUALITY（默认 80）
4. 按内容哈希缓存到磁盘 + 内存 LRU，同一张图在分类/OCR/视觉推理/核对
   多次复用时只编码一次

实测效果：2.96 MB → 187 KB（base64 243 KB），**缩小约 16 倍**，
1600px 对文字 OCR 完全够用。参数在 config.py 中可调，一条配置即可回退。
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from . import config

logger = logging.getLogger("ImagePrep")

# 内存 LRU：避免同一张图在一次任务中被反复编码
_CACHE_SIZE = 64
_memory_cache: "OrderedDict[str, PreparedImage]" = OrderedDict()
_memory_lock = threading.Lock()


@dataclass(frozen=True)
class PreparedImage:
    """已预处理、可直接送进 API 的图片。"""

    data: bytes
    mime: str
    width: int
    height: int
    source_path: Path | None = None

    @property
    def data_uri(self) -> str:
        import base64

        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime};base64,{encoded}"

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def _memory_key(content_hash: str, max_edge: int, quality: int) -> str:
    return f"{content_hash}-{max_edge}-{quality}"


def _lru_get(key: str) -> PreparedImage | None:
    with _memory_lock:
        item = _memory_cache.get(key)
        if item is not None:
            _memory_cache.move_to_end(key)
        return item


def _lru_put(key: str, value: PreparedImage) -> None:
    with _memory_lock:
        _memory_cache[key] = value
        _memory_cache.move_to_end(key)
        while len(_memory_cache) > _CACHE_SIZE:
            _memory_cache.popitem(last=False)


def clear_memory_cache() -> None:
    with _memory_lock:
        _memory_cache.clear()


def _ensure_rgb(image: Image.Image) -> Image.Image:
    """把任意色彩模式转成 RGB；带透明度的以白底合成，避免出现黑块。"""
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def _resize_if_needed(image: Image.Image, max_edge: int) -> Image.Image:
    if max_edge <= 0:
        return image
    longest = max(image.size)
    if longest <= max_edge:
        return image
    scale = max_edge / float(longest)
    new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(new_size, Image.LANCZOS)


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _cache_path(content_hash: str, max_edge: int, quality: int) -> Path:
    return config.IMAGE_CACHE_DIR / f"{content_hash}-{max_edge}-{quality}.jpg"


def prepare_image_bytes(
    payload: bytes,
    *,
    source_path: Path | None = None,
    max_edge: int | None = None,
    quality: int | None = None,
    use_cache: bool | None = None,
) -> PreparedImage:
    """预处理图片字节流。缓存未启用或解码失败时抛出异常，由调用方决定回退策略。"""
    max_edge = config.IMAGE_MAX_EDGE if max_edge is None else max_edge
    quality = config.IMAGE_JPEG_QUALITY if quality is None else quality
    use_cache = config.IMAGE_CACHE_ENABLED if use_cache is None else use_cache

    content_hash = _hash_bytes(payload)
    key = _memory_key(content_hash, max_edge, quality)

    cached = _lru_get(key)
    if cached is not None:
        return cached

    disk_path = _cache_path(content_hash, max_edge, quality)
    if use_cache and disk_path.exists():
        try:
            data = disk_path.read_bytes()
            with Image.open(io.BytesIO(data)) as cached_image:
                prepared = PreparedImage(
                    data=data,
                    mime="image/jpeg",
                    width=cached_image.width,
                    height=cached_image.height,
                    source_path=source_path,
                )
            _lru_put(key, prepared)
            return prepared
        except (OSError, ValueError):
            # 缓存损坏：删掉重新生成
            disk_path.unlink(missing_ok=True)

    with Image.open(io.BytesIO(payload)) as opened:
        # 先按 EXIF 旋转，再做缩放，否则缩放后的方向仍是错的
        oriented = ImageOps.exif_transpose(opened) or opened
        rgb = _ensure_rgb(oriented)
        resized = _resize_if_needed(rgb, max_edge)
        data = _encode_jpeg(resized, quality)
        prepared = PreparedImage(
            data=data,
            mime="image/jpeg",
            width=resized.width,
            height=resized.height,
            source_path=source_path,
        )

    if use_cache:
        try:
            config.IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            disk_path.write_bytes(data)
        except OSError as exc:
            logger.warning("写入图片缓存失败: %s", exc)

    _lru_put(key, prepared)
    return prepared


def prepare_image(path: Path, **kwargs) -> PreparedImage:
    """读取文件并预处理。"""
    payload = Path(path).read_bytes()
    return prepare_image_bytes(payload, source_path=Path(path), **kwargs)


def prepare_images(paths: list[Path], **kwargs) -> list[PreparedImage]:
    """批量预处理，保持输入顺序。"""
    return [prepare_image(path, **kwargs) for path in paths]


def prepare_for_api(path: Path, **kwargs) -> PreparedImage:
    """`prepare_image` 的语义化别名：得到可直接放进 API 请求体的图片。"""
    return prepare_image(path, **kwargs)


def estimate_savings(paths: list[Path]) -> dict[str, float]:
    """对比原图与预处理后的体积，用于自检与文档验证。"""
    original = sum(Path(p).stat().st_size for p in paths)
    prepared = sum(prepare_image(p).size_bytes for p in paths)
    return {
        "original_bytes": float(original),
        "prepared_bytes": float(prepared),
        "ratio": (original / prepared) if prepared else 0.0,
    }
