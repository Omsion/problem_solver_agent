# -*- coding: utf-8 -*-
"""
deepseek_client.py - DeepSeek API客户端模块

使用requests库调用DeepSeek API，避免类型检查问题。
"""

import requests
import json
from pathlib import Path
from typing import List, Union

# 从项目中导入配置和工具函数
import config
from utils import setup_logger, encode_image_to_base64

# 初始化日志记录器
logger = setup_logger()


def ask_deepseek_with_images(image_paths: List[Path]) -> Union[str, None]:
    """
    将一组图片发送给DeepSeek API进行提问，并返回模型的回答。

    Args:
        image_paths (List[Path]): 包含待处理图片路径对象的列表。

    Returns:
        str | None: 成功时返回模型回答的文本内容，失败则返回 None。
    """
    logger.info(f"正在为 {len(image_paths)} 张图片准备API请求...")

    if not config.DEEPSEEK_API_KEY:
        logger.error("DeepSeek API密钥未在配置中设置！请检查 .env 文件。")
        return None

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"
    }

    # 构建消息内容
    content = [{"type": "text", "text": config.PROMPT_TEMPLATE}]

    # 添加图片
    for image_path in image_paths:
        base64_image = encode_image_to_base64(image_path)
        if base64_image:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}"
                }
            })
        else:
            logger.warning(f"跳过无法编码的图片: {image_path}")

    if len(content) <= 1:
        logger.error("没有有效的图片可供处理，取消API请求。")
        return None

    # 构建请求体
    payload = {
        "model": config.MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": content
            }
        ],
        "max_tokens": 4000,
        "temperature": 0.7,
        "stream": False
    }

    try:
        logger.info(f"向DeepSeek API发送请求 (模型: {config.MODEL_NAME})...")
        response = requests.post(
            f"{config.API_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=180
        )
        response.raise_for_status()

        result = response.json()
        answer = result['choices'][0]['message']['content']
        logger.info("成功接收到API响应。")
        return answer

    except Exception as e:
        logger.error(f"API请求失败: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            logger.error(f"API响应内容: {e.response.text}")
        return None