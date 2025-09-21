# -*- coding: utf-8 -*-
"""
qwen_client.py - Qwen-VL (DashScope) API Client
"""

from pathlib import Path
from typing import List, Union, Dict, Any
from typing_extensions import TypedDict
from openai import OpenAI

import config
from utils import setup_logger, encode_image_to_base64

logger = setup_logger()

class VisionCompletionPayload(TypedDict):
    model: str
    messages: List[Dict[str, Any]]

try:
    if not config.DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY not found in .env file.")
    qwen_client = OpenAI(
        api_key=config.DASHSCOPE_API_KEY,
        base_url=config.DASHSCOPE_BASE_URL,
    )
except Exception as e:
    logger.critical(f"Failed to initialize Qwen-VL (DashScope) client: {e}")
    qwen_client = None


def _call_qwen_api(image_paths: List[Path], prompt: str) -> Union[str, None]:
    if not qwen_client:
        logger.error("Qwen-VL client is not initialized.")
        return None

    content_payload = [{"type": "text", "text": prompt}]
    for image_path in image_paths:
        base64_image = encode_image_to_base64(image_path)
        if base64_image:
            content_payload.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            })
        else:
            logger.warning(f"Skipping image that could not be encoded: {image_path}")

    if len(content_payload) <= 1:
        logger.error("No valid images to send to Qwen-VL.")
        return None

    payload: VisionCompletionPayload = {
        "model": config.QWEN_MODEL_NAME,
        "messages": [{"role": "user", "content": content_payload}]
    }

    try:
        completion = qwen_client.chat.completions.create(**payload)
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Qwen-VL API request failed: {e}")
        return None


def classify_problem_type(image_paths: List[Path]) -> str:
    """
    ### UPDATED ###
    步骤 1: 调用Qwen-VL进行“粗分类”。
    返回 'CODING' 或 'GENERAL'。
    """
    logger.info("步骤 1: 正在进行问题类型粗分类...")
    response = _call_qwen_api(image_paths, config.CLASSIFICATION_PROMPT)

    # 更新期望的关键词列表
    if response and response in ["CODING", "GENERAL"]:
        logger.info(f"粗分类成功，识别类型为: {response}")
        return response

    logger.warning(f"粗分类失败或返回未知类型 ('{response}')。将默认视为 'GENERAL'。")
    return "GENERAL"


def transcribe_images(image_paths: List[Path]) -> Union[str, None]:
    """
    步骤 3: 调用Qwen-VL进行文字转录。
    """
    logger.info("步骤 3: 正在进行图片文字转录...")
    return _call_qwen_api(image_paths, config.TRANSCRIPTION_PROMPT)