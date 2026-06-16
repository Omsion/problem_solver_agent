"""
test_image_prep.py - 图片预处理测试

覆盖点：最长边限制、JPEG 输出、透明度白底合成、EXIF 方向、
缓存命中（第二次不重新编码）、失败路径。

运行：pytest tests/test_image_prep.py -v
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from problem_solver_agent import config, image_prep


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """把缓存目录指到临时路径，并清空内存 LRU，避免用例互相影响。"""
    monkeypatch.setattr(config, "IMAGE_CACHE_DIR", tmp_path / "cache", raising=False)
    image_prep.clear_memory_cache()
    yield
    image_prep.clear_memory_cache()


def _jpeg_bytes(size=(2288, 1764), color=(120, 130, 140)) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _save(tmp_path, payload: bytes, name="shot.jpg"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_long_edge_is_limited(tmp_path):
    path = _save(tmp_path, _jpeg_bytes())
    prepared = image_prep.prepare_image(path, max_edge=1600)
    assert max(prepared.width, prepared.height) == 1600
    # 等比缩放：宽高比保持不变
    assert abs(prepared.width / prepared.height - 2288 / 1764) < 0.01


def test_output_is_jpeg(tmp_path):
    path = _save(tmp_path, _jpeg_bytes())
    prepared = image_prep.prepare_image(path)
    assert prepared.mime == "image/jpeg"
    with Image.open(io.BytesIO(prepared.data)) as reopened:
        assert reopened.format == "JPEG"


def test_small_image_is_not_upscaled(tmp_path):
    path = _save(tmp_path, _jpeg_bytes(size=(400, 300)))
    prepared = image_prep.prepare_image(path, max_edge=1600)
    assert (prepared.width, prepared.height) == (400, 300)


def test_rgba_is_composited_on_white(tmp_path):
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))  # 全透明
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    path = tmp_path / "transparent.png"
    path.write_bytes(buffer.getvalue())

    prepared = image_prep.prepare_image(path, max_edge=1600)
    with Image.open(io.BytesIO(prepared.data)) as result:
        assert result.mode == "RGB"
        # 透明区域应变成白色，而不是黑色
        assert result.getpixel((32, 32)) == (255, 255, 255)


def test_png_with_transparency_palette_becomes_white(tmp_path):
    image = Image.new("P", (32, 32))
    image.info["transparency"] = 0
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    path = tmp_path / "palette.png"
    path.write_bytes(buffer.getvalue())

    prepared = image_prep.prepare_image(path, max_edge=1600)
    with Image.open(io.BytesIO(prepared.data)) as result:
        assert result.mode == "RGB"
        assert result.getpixel((16, 16)) == (255, 255, 255)


def test_exif_orientation_is_applied(tmp_path):
    # 100x50，并用 orientation=6（顺时针旋转 90°）标记
    image = Image.new("RGB", (100, 50), (10, 20, 30))
    exif = image.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    path = tmp_path / "rotated.jpg"
    path.write_bytes(buffer.getvalue())

    prepared = image_prep.prepare_image(path, max_edge=1600)
    # 旋转后长宽互换
    assert (prepared.width, prepared.height) == (50, 100)


def test_same_content_hits_cache(tmp_path, monkeypatch):
    payload = _jpeg_bytes()
    path = _save(tmp_path, payload)

    first = image_prep.prepare_image(path)
    assert first.size_bytes > 0

    # 第二次调用不应再写缓存文件（计数写入次数）
    writes = {"count": 0}
    original_write = config.IMAGE_CACHE_DIR.__class__.write_bytes

    def counting_write(self, data):  # noqa: ANN001
        writes["count"] += 1
        return original_write(self, data)

    monkeypatch.setattr("pathlib.Path.write_bytes", counting_write)
    second = image_prep.prepare_image(path)

    assert writes["count"] == 0, "命中缓存时不应重新写盘"
    assert second.data == first.data


def test_cache_disabled_still_works(tmp_path):
    path = _save(tmp_path, _jpeg_bytes())
    prepared = image_prep.prepare_image(path, use_cache=False)
    assert prepared.size_bytes > 0
    assert not config.IMAGE_CACHE_DIR.exists()


def test_corrupt_cache_is_regenerated(tmp_path):
    path = _save(tmp_path, _jpeg_bytes())
    image_prep.prepare_image(path)

    # 把缓存文件写坏
    cached = list(config.IMAGE_CACHE_DIR.glob("*.jpg"))
    assert cached, "应有缓存文件生成"
    cached[0].write_bytes(b"not an image")

    image_prep.clear_memory_cache()
    prepared = image_prep.prepare_image(path)
    with Image.open(io.BytesIO(prepared.data)) as result:
        assert result.format == "JPEG"


def test_invalid_input_raises(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"definitely not an image")
    with pytest.raises(Exception):
        image_prep.prepare_image(path)


def test_size_reduction_is_significant(tmp_path):
    """真实场景的核心收益：体积应缩小数倍以上。"""
    # 用带噪声的大图模拟真实截图，避免纯色图压缩率失真
    image = Image.effect_noise((2288, 1764), 60).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    path = tmp_path / "noisy.jpg"
    path.write_bytes(buffer.getvalue())

    original = path.stat().st_size
    prepared = image_prep.prepare_image(path)
    assert prepared.size_bytes < original / 3, f"体积仅缩小到 {prepared.size_bytes}/{original}"


def test_prepare_images_preserves_order(tmp_path):
    first = _save(tmp_path, _jpeg_bytes(size=(300, 200)), "a.jpg")
    second = _save(tmp_path, _jpeg_bytes(size=(500, 400)), "b.jpg")
    prepared = image_prep.prepare_images([first, second])
    assert [p.source_path for p in prepared] == [first, second]
    assert prepared[0].width == 300
    assert prepared[1].width == 500


def test_data_uri_format(tmp_path):
    path = _save(tmp_path, _jpeg_bytes(size=(100, 100)))
    prepared = image_prep.prepare_image(path)
    assert prepared.data_uri.startswith("data:image/jpeg;base64,")


def test_estimate_savings(tmp_path):
    path = _save(tmp_path, _jpeg_bytes())
    result = image_prep.estimate_savings([path])
    assert result["original_bytes"] > result["prepared_bytes"]
    assert result["ratio"] > 1
