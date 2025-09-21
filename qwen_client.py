# -*- coding: utf-8 -*-
"""
qwen_client.py - Qwen-VL (DashScope) API Client

This module handles two key tasks in the pipeline:
1. Classifying the problem type from the images.
2. Transcribing the text content from the images.
"""

from pathlib import Path
from typing import List, Union
from openai import OpenAI

import config
from utils import setup_logger, encode_image_to_base64

logger = setup_logger()

# --- Initialize the Qwen-VL Client once ---
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
    """A generic helper function to call the Qwen API."""
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

    try:
        completion = qwen_client.chat.completions.create(
            model=config.QWEN_MODEL_NAME,
            messages=[{"role": "user", "content": content_payload}]
        )  # type: ignore
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Qwen-VL API request failed: {e}")
        return None


def classify_problem_type(image_paths: List[Path]) -> str:
    """
    Step 1: Sends images to Qwen-VL to classify the problem type.
    Returns one of 'LEETCODE', 'ACM', or 'GENERAL'.
    """
    logger.info("Step 1: Classifying problem type with Qwen-VL...")
    response = _call_qwen_api(image_paths, config.CLASSIFICATION_PROMPT)

    if response and response in ["LEETCODE", "ACM", "GENERAL"]:
        logger.info(f"Classification successful. Detected type: {response}")
        return response

    logger.warning(f"Classification failed or returned unknown type ('{response}'). Defaulting to 'GENERAL'.")
    return "GENERAL"


def transcribe_images(image_paths: List[Path]) -> Union[str, None]:
    """
    Step 3: Sends images to Qwen-VL to transcribe their text content.
    """
    logger.info("Step 3: Transcribing images with Qwen-VL...")
    return _call_qwen_api(image_paths, config.TRANSCRIPTION_PROMPT)