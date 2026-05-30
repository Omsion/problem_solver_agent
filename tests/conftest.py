# -*- coding: utf-8 -*-
"""
conftest.py - pytest 全局 fixtures
"""

import pytest
from pathlib import Path


@pytest.fixture
def sample_image_path(tmp_path):
    """创建测试用图片文件。"""
    try:
        from PIL import Image, ImageDraw

        img = Image.new('RGB', (100, 100), color='white')
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), "Test Image", fill='black')

        image_path = tmp_path / "test_image.png"
        img.save(image_path, 'PNG')
        return image_path
    except ImportError:
        image_path = tmp_path / "test_image.png"
        image_path.write_bytes(b"fake image data")
        return image_path


@pytest.fixture
def mock_config(monkeypatch):
    """Mock API 密钥环境变量。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("ZHIPU_API_KEY", "test-zhipu-key")


@pytest.fixture
def tmp_config_dir(tmp_path):
    """创建临时配置目录。"""
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
