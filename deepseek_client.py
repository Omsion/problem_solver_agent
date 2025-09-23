# -*- coding: utf-8 -*-
"""
deepseek_client.py - DeepSeek API 辅助客户端 (非流式分析) - 优化版

这个模块专注于与 DeepSeek API 进行非流式的文本交互。
它的主要用途是：
1. 对OCR转录结果进行文本润色。
2. 在编程题的分步求解中，获取初步的“分析思路”。
3. 提供一个通用的DeepSeek API健康检查功能。
"""

from typing import Union, List, Dict, Any
from typing_extensions import TypedDict, Literal
from openai import OpenAI
import time
import requests

import config
from utils import setup_logger

logger = setup_logger()


# 使用 TypedDict 定义 DeepSeek API 请求的 payload 结构，增强类型安全性
class DeepseekChatCompletionPayload(TypedDict):
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int
    temperature: float
    stream: Literal[False]  # 注意：此客户端设计为非流式，所以 stream 固定为 False


try:
    if not config.DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not found in .env file.")

    deepseek_client = OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.SOLVER_CONFIG["deepseek"]["base_url"],
        timeout=config.API_TIMEOUT,
        max_retries=config.MAX_RETRIES,
    )
except Exception as e:
    logger.critical(f"初始化DeepSeek客户端失败: {e}")
    deepseek_client = None


def ask_deepseek_for_analysis(final_prompt: str, model_override: str = None) -> Union[str, None]:
    """
    优化的DeepSeek API调用函数，用于非流式的文本分析，支持动态切换模型。

    Args:
        final_prompt (str): 发送给模型的最终提示词。
        model_override (str, optional): 如果提供，则使用此模型名称覆盖配置文件中
                                        用于润色和分析的默认模型（config.POLISHING_MODEL_NAME）。
                                        默认为 None。
    """
    if not deepseek_client:
        logger.error("DeepSeek客户端未初始化。中止分析。")
        return None

    #  target_model 的默认值应指向 config.POLISHING_MODEL_NAME。
    # 这是此客户端目前的主要职责（润色和分步分析）所使用的模型。
    target_model = model_override if model_override else config.POLISHING_MODEL_NAME  # <-- 修改此处

    logger.info(f"Step 4: 正在使用DeepSeek模型 '{target_model}' 发送非流式分析请求...")

    payload: DeepseekChatCompletionPayload = {
        "model": target_model,
        "messages": [{"role": "user", "content": final_prompt}],
        "max_tokens": 8000,
        "temperature": 0.7,
        "stream": False  # 此客户端设计为非流式
    }

    max_retries = config.MAX_RETRIES
    retry_delay = config.RETRY_DELAY

    for attempt in range(max_retries):
        try:
            logger.info(f"DeepSeek API请求尝试 {attempt + 1}/{max_retries}")

            response = deepseek_client.chat.completions.create(**payload)  # type: ignore

            if response and response.choices:
                answer = response.choices[0].message.content
                if answer and len(answer.strip()) > 0:
                    logger.info("成功从 DeepSeek 收到解决方案。")
                    return answer
                else:
                    logger.warning("从 DeepSeek 收到空响应。")
            else:
                logger.warning("从 DeepSeek 收到无效响应结构。")

        except requests.exceptions.Timeout:
            logger.warning(f"DeepSeek API 请求超时 (尝试 {attempt + 1})")
        except requests.exceptions.ConnectionError:
            logger.warning(f"DeepSeek API 连接错误 (尝试 {attempt + 1})")
        except Exception as e:
            logger.error(f"DeepSeek API 请求失败 (尝试 {attempt + 1}): {e}", exc_info=True)

        if attempt < max_retries - 1:
            logger.info(f"等待 {retry_delay} 秒后重试...")
            time.sleep(retry_delay)
            retry_delay *= 1.5

    logger.error("所有 DeepSeek API 尝试均失败。")
    return None

