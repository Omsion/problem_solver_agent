# -*- coding: utf-8 -*-
"""
deepseek_client.py - DeepSeek API Client (Text-Only Analysis)

### FINAL CORRECTED VERSION ###
Uses the official 'openai' library to interact with the DeepSeek API endpoint.
This is the industry-standard method for OpenAI-compatible APIs and resolves all previous import errors.
"""

from typing import Union
# ### UPDATED ### - Import the correct, standard 'OpenAI' client
from openai import OpenAI

import config
from utils import setup_logger

logger = setup_logger()

# --- Initialize the DeepSeek Client using the OpenAI library structure ---
try:
    if not config.DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not found in .env file.")

    # ### UPDATED ### - Create an OpenAI client instance pointed at the DeepSeek URL
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

    messages_payload = [{"role": "user", "content": final_prompt}]

    try:
        # The API call remains the same, as it follows the OpenAI library's standard.
        response = deepseek_client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=messages_payload,
            max_tokens=4000,
            temperature=0.7,
            stream=False
        )
        answer = response.choices[0].message.content
        logger.info("Successfully received analysis from DeepSeek.")
        return answer

    except Exception as e:
        logger.error(f"DeepSeek API request failed: {e}")
        return None