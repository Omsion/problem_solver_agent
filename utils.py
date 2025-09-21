# -*- coding: utf-8 -*-
"""
utils.py - 通用工具模块

存放项目中可被多处调用的辅助函数，例如：
- 日志记录器设置
- 图片到Base64的编码
- 从模型响应中解析标题
- 清理字符串以适配为合法的文件名
"""

import base64
import logging
import re
from pathlib import Path


def setup_logger():
    """配置并返回一个全局日志记录器。"""
    # 创建一个日志记录器
    logger = logging.getLogger("AgentLogger")
    logger.setLevel(logging.INFO)

    # 如果已经有处理器了，就不要重复添加，避免日志重复输出
    if not logger.handlers:
        # 创建一个控制台处理器
        console_handler = logging.StreamHandler()

        # 定义日志格式
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(module)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)

        # 将处理器添加到日志记录器
        logger.addHandler(console_handler)

    return logger


def encode_image_to_base64(image_path: Path) -> str | None:
    """
    读取指定路径的图片文件，并将其编码为Base64字符串。

    Args:
        image_path (Path): 图片文件的路径对象。

    Returns:
        str | None: 编码后的Base64字符串，如果文件不存在或读取失败则返回 None。
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


def parse_title_from_response(response_text: str) -> str | None:
    """
    使用正则表达式从模型的响应文本中解析出标题。

    Args:
        response_text (str): DeepSeek模型返回的完整文本。

    Returns:
        str | None: 提取到的标题字符串，如果未找到匹配项则返回 None。
    """
    # 正则表达式匹配以 "TITLE:" 开头，并捕获后面的所有内容直到行尾
    match = re.search(r"TITLE:\s*(.*)", response_text, re.IGNORECASE)
    if match:
        # group(1) 返回第一个捕获组的内容，并去除首尾空白
        return match.group(1).strip()
    return None


def sanitize_filename(filename: str) -> str:
    """
    清理字符串，移除或替换其中不适用于文件名的非法字符。

    Args:
        filename (str): 原始的、可能包含非法字符的字符串。

    Returns:
        str: 清理后、可安全用作文件名的字符串。
    """
    # 定义Windows和Linux/macOS中常见的文件名非法字符
    illegal_chars = r'[\\/:"*?<>|]+'
    # 将所有非法字符替换为空字符串
    return re.sub(illegal_chars, '', filename)