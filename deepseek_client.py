# -*- coding: utf-8 -*-
"""
deepseek_client.py - DeepSeek API Client (Text-Only Analysis)

### FINAL CORRECTED VERSION ###
Uses a TypedDict to define the payload structure, providing a robust solution
to IDE type-checking warnings when using the openai library for third-party services.
"""

from typing import Union, List, Dict, Any
from typing_extensions import TypedDict, Literal
from openai import OpenAI

import config
from utils import setup_logger

logger = setup_logger()


# ### NEW ### - Define the payload structure to satisfy the type checker
class ChatCompletionPayload(TypedDict):
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int
    temperature: float
    stream: Literal[False]


# --- Initialize the DeepSeek Client using the OpenAI library structure ---
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


def ask_deepseek_for_analysis(transcribed_text: str) -> Union[str, None]:
    """
    Sends transcribed text to the DeepSeek API for analysis and solution.
    """
    if not deepseek_client:
        logger.error("DeepSeek client is not initialized. Aborting analysis.")
        return None

    logger.info("Step 2: Sending transcribed text to DeepSeek for analysis...")

    final_prompt = config.DEEPSEEK_PROMPT_TEMPLATE.format(
        transcribed_text=transcribed_text
    )

    # Construct the payload using the TypedDict definition
    payload: ChatCompletionPayload = {
        "model": config.MODEL_NAME,
        "messages": [{"role": "user", "content": final_prompt}],
        "max_tokens": 4000,
        "temperature": 0.7,
        "stream": False
    }

    try:
        # Pass the structured payload using kwargs unpacking (**)
        response = deepseek_client.chat.completions.create(**payload)

        answer = response.choices[0].message.content
        logger.info("Successfully received analysis from DeepSeek.")
        return answer

    except Exception as e:
        logger.error(f"DeepSeek API request failed: {e}")
        return None