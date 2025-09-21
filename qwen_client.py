# -*- coding: utf-8 -*-
"""
qwen_client.py - Qwen-VL (DashScope) API Client

This module handles the first step of the pipeline: converting a group of images
into a single block of transcribed text using the Qwen-VL model.
"""

from pathlib import Path
from typing import List, Union

# Uses the openai library as per DashScope documentation
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


def describe_images(image_paths: List[Path]) -> Union[str, None]:
    """
    Sends images to the Qwen-VL API and returns the transcribed text.
    """
    if not qwen_client:
        logger.error("Qwen-VL client is not initialized. Aborting transcription.")
        return None

    logger.info(f"Step 1: Transcribing {len(image_paths)} images with Qwen-VL...")

    # Build the 'content' list for the payload
    content_payload = []
    # Add the text prompt first
    content_payload.append({"type": "text", "text": config.QWEN_PROMPT})

    # Add each image
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
        logger.error("No valid images to transcribe for Qwen-VL.")
        return None

    try:
        completion = qwen_client.chat.completions.create(
            model=config.QWEN_MODEL_NAME,
            messages=[{"role": "user", "content": content_payload}]
        )

        transcribed_text = completion.choices[0].message.content
        logger.info("Successfully transcribed images with Qwen-VL.")
        return transcribed_text.strip()

    except Exception as e:
        logger.error(f"Qwen-VL API request failed: {e}")
        return None