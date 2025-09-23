# -*- coding: utf-8 -*-
"""
deepseek_client.py - DeepSeek API Client (Text-Only Analysis) - 优化版
"""

from typing import Union, List, Dict, Any
from typing_extensions import TypedDict, Literal
from openai import OpenAI
import time
import requests

import config
from utils import setup_logger

logger = setup_logger()


class ChatCompletionPayload(TypedDict):
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int
    temperature: float
    stream: Literal[True]


try:
    if not config.DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_key not found in .env file.")

    # 增加超时和重试配置
    deepseek_client = OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=config.API_TIMEOUT,
        max_retries=config.MAX_RETRIES,
    )
except Exception as e:
    logger.critical(f"Failed to initialize DeepSeek client: {e}")
    deepseek_client = None


def ask_deepseek_for_analysis(final_prompt: str, model_override: str = None) -> Union[str, None]:
    """
    优化的DeepSeek API调用函数，支持动态切换模型。

    Args:
        final_prompt (str): 发送给模型的最终提示词。
        model_override (str, optional): 如果提供，则使用此模型名称覆盖配置文件中的默认模型。
                                        默认为 None。
    """
    if not deepseek_client:
        logger.error("DeepSeek client is not initialized. Aborting analysis.")
        return None

    # 决定本次API调用使用哪个模型
    target_model = model_override if model_override else config.MODEL_NAME

    # 在日志中明确记录正在使用的模型，便于调试
    logger.info(f"Step 4: Sending final prompt to DeepSeek using model '{target_model}'...")

    payload: ChatCompletionPayload = {
        "model": target_model,  # <-- 使用动态决定的模型
        "messages": [{"role": "user", "content": final_prompt}],
        "max_tokens": 8000,
        "temperature": 0.7,
        "stream": True
    }

    # 重试机制
    max_retries = config.MAX_RETRIES
    retry_delay = config.RETRY_DELAY

    for attempt in range(max_retries):
        try:
            logger.info(f"API请求尝试 {attempt + 1}/{max_retries}")

            # 添加请求超时控制
            response = deepseek_client.chat.completions.create(**payload)

            if response and response.choices:
                answer = response.choices[0].message.content
                if answer and len(answer.strip()) > 0:
                    logger.info("Successfully received solution from DeepSeek.")
                    return answer
                else:
                    logger.warning("Received empty response from DeepSeek.")
            else:
                logger.warning("Invalid response structure from DeepSeek.")

        except requests.exceptions.Timeout:
            logger.warning(f"DeepSeek API timeout on attempt {attempt + 1}")
        except requests.exceptions.ConnectionError:
            logger.warning(f"DeepSeek API connection error on attempt {attempt + 1}")
        except Exception as e:
            logger.error(f"DeepSeek API request failed on attempt {attempt + 1}: {e}")

        # 如果不是最后一次尝试，等待后重试
        if attempt < max_retries - 1:
            logger.info(f"Waiting {retry_delay} seconds before retry...")
            time.sleep(retry_delay)
            retry_delay *= 1.5

    logger.error("All DeepSeek API attempts failed.")
    return None


# 添加API健康检查函数
def check_deepseek_health() -> bool:
    """检查DeepSeek API是否可用 - 修复版"""
    if not deepseek_client:
        logger.error("DeepSeek客户端未初始化")
        return False

    try:
        test_response = deepseek_client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=[{"role": "user", "content": "回复'ok'"}],
            max_tokens=5,
            timeout=10.0
        )
        # 只要响应正常就返回True，不检查具体内容
        if test_response and test_response.choices:
            logger.info("DeepSeek API健康检查通过")
            return True
        else:
            logger.warning("DeepSeek API响应结构异常")
            return False
    except Exception as e:
        logger.error(f"DeepSeek健康检查失败: {e}")
        return False
