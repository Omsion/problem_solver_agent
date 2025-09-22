# -*- coding: utf-8 -*-
"""
deepseek_client.py - DeepSeek API Client (Text-Only Analysis)
"""

from typing import Union, List, Dict, Any
from typing_extensions import TypedDict, Literal
from openai import OpenAI

import config
from utils import setup_logger

logger = setup_logger()


class ChatCompletionPayload(TypedDict):
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int
    temperature: float
    stream: Literal[False]


try:
    if not config.DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not found in .env file.")
    deepseek_client = OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL
    )
except Exception as e:
    logger.critical(f"Failed to initialize DeepSeek client: {e}")
    deepseek_client = None


# Function now accepts the prompt_template as an argument
def ask_deepseek_for_analysis(transcribed_text: str, prompt_template: str) -> Union[str, None]:
    """
    Step 4: Sends transcribed text to the DeepSeek API for analysis and solution
    using a strategically selected prompt.
    """
    if not deepseek_client:
        logger.error("DeepSeek client is not initialized. Aborting analysis.")
        return None

    logger.info("Step 4: Sending transcribed text to DeepSeek for solving...")

    final_prompt = prompt_template.format(transcribed_text=transcribed_text)

    payload: ChatCompletionPayload = {
        "model": config.MODEL_NAME,
        "messages": [{"role": "user", "content": final_prompt}],
        "max_tokens": 10000,
        "temperature": 0.7,
        "stream": False
    }

    try:
        response = deepseek_client.chat.completions.create(**payload)
        answer = response.choices[0].message.content
        logger.info("Successfully received solution from DeepSeek.")
        return answer
    except Exception as e:
        logger.error(f"DeepSeek API request failed: {e}")
        return None