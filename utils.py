# -*- coding: utf-8 -*-
"""
utils.py - 通用工具模块

存放项目中可被多处调用的辅助函数，例如：
- 日志记录器设置
- 图片到Base64的编码
- 从模型响应中解析标题
- 清理字符串以适配为合法的文件名
- (新增) 用于提升OCR准确率的图像预处理功能
"""

import base64
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
import io
from typing import List

from PIL import Image, ImageEnhance, ImageFilter


def setup_logger():
    """配置并返回一个全局日志记录器。"""
    logger = logging.getLogger("AgentLogger")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        console_handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(module)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def encode_image_to_base64(image_path: Path) -> str | None:
    """
    读取指定路径的图片文件，并将其编码为Base64字符串。
    """
    logger = setup_logger()
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except FileNotFoundError:
        logger.error(f"图片文件未找到: {image_path}")
        return None
    except Exception as e:
        logger.error(f"编码图片 '{image_path}' 时发生错误: {e}")
        return None


def sanitize_filename(filename: str) -> str:
    """
    清理字符串，移除或替换其中不适用于文件名的非法字符。
    """
    illegal_chars = r'[\\/:"*?<>|]+'
    return re.sub(illegal_chars, '', filename)


def preprocess_image_for_ocr(image_path: Path) -> bytes:
    """
    对图片进行一系列预处理，以最大化OCR的准确率。
    该函数执行以下操作：
    1.  **转换为灰度图**: 消除颜色干扰，让模型专注于文字的形状和结构。
    2.  **增强对比度**: 将文字与背景的差异拉到最大，使其更易于识别。
    3.  **应用锐化滤镜**: 使文字的边缘更加清晰、锐利。

    Args:
        image_path (Path): 原始图片文件的路径。

    Returns:
        bytes: 返回处理后图片的二进制数据（PNG格式），如果处理失败则返回 None。
    """
    logger = setup_logger()
    try:
        with Image.open(image_path) as img:
            # 步骤 1: 转换为灰度图 ('L' a.k.a. Luminance)
            processed_img = img.convert('L')

            # 步骤 2: 实例化对比度增强器并应用
            enhancer = ImageEnhance.Contrast(processed_img)
            processed_img = enhancer.enhance(2.0)  # 2.0 表示增强2倍对比度，这是一个经验值

            # 步骤 3: 应用锐化滤镜
            processed_img = processed_img.filter(ImageFilter.SHARPEN)

            # 将处理后的图片保存到内存中的字节流，避免写入临时文件
            img_byte_arr = io.BytesIO()
            # 保存为PNG格式，PNG是无损的，能最好地保留处理后的细节
            processed_img.save(img_byte_arr, format='PNG')

            logger.info(f"图片预处理成功: {image_path.name}")
            return img_byte_arr.getvalue()

    except Exception as e:
        logger.error(f"预处理图片 '{image_path.name}' 时发生严重错误: {e}")
        return None

