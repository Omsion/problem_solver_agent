# -*- coding: utf-8 -*-
"""
pytest 配置文件

定义全局 fixtures 和 pytest 插件配置。
"""

import pytest
from pathlib import Path


@pytest.fixture
def sample_image_path(tmp_path):
    """
    创建测试用图片文件的 fixture。

    Args:
        tmp_path: pytest 提供的临时目录路径

    Returns:
        Path: 测试图片文件的路径
    """
    try:
        from PIL import Image, ImageDraw
        import io

        # 创建一个简单的测试图片
        img = Image.new('RGB', (100, 100), color='white')
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), "Test Image", fill='black')

        # 保存到临时目录
        image_path = tmp_path / "test_image.png"
        img.save(image_path, 'PNG')

        return image_path
    except ImportError:
        # Pillow 不可用时创建空文件
        image_path = tmp_path / "test_image.png"
        image_path.write_bytes(b"fake image data")
        return image_path


@pytest.fixture
def mock_config(monkeypatch):
    """
    Mock 配置的 fixture，设置测试用 API 密钥。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture
    """
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("ZHIPU_API_KEY", "test-zhipu-key")


@pytest.fixture
def tmp_config_dir(tmp_path):
    """
    创建临时配置目录的 fixture。

    Args:
        tmp_path: pytest 提供的临时目录路径

    Returns:
        Path: 临时配置目录
    """
    monitor_dir = tmp_path / "monitor"
    processed_dir = tmp_path / "processed"
    solutions_dir = tmp_path / "solutions"

    monitor_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)
    solutions_dir.mkdir(parents=True)

    return {
        "root_dir": tmp_path,
        "monitor_dir": monitor_dir,
        "processed_dir": processed_dir,
        "solutions_dir": solutions_dir,
    }
