# -*- coding: utf-8 -*-
"""
qwen_client.py - Qwen-VL (DashScope) API 客户端 (V2.1 - 职责分离版)

本模块是Agent的“视觉中枢”，利用通义千问视觉语言模型（Qwen-VL）的强大能力，
处理所有直接与图片内容理解相关的任务。

V2.1 版本更新:
- 将 `transcribe_images` 重构为 `transcribe_images_raw`，使其职责更单一：
  只负责并行转录多张图片，并返回原始的文本片段列表。
- 文本的合并与润色任务已完全移交至上层（image_grouper.py）的LLM调用。
"""
import concurrent.futures
from pathlib import Path
from typing import List, Union, Dict, Any
from typing_extensions import TypedDict
from openai import OpenAI
import base64

import config
from utils import setup_logger, encode_image_to_base64

logger = setup_logger()


class VisionCompletionPayload(TypedDict):
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int


# --- Qwen-VL 客户端的单例初始化 ---
# 将客户端的初始化放在模块级别，可以确保在整个程序运行期间只创建一个实例，
# 避免了每次调用都重新建立连接的开销，提高了效率和资源利用率。
try:
    if not config.DASHSCOPE_API_KEY:
        raise ValueError("未在 .env 文件中找到 DASHSCOPE_API_KEY。")
    qwen_client = OpenAI(
        api_key=config.DASHSCOPE_API_KEY,
        base_url=config.QWEN_BASE_URL,
    )
except Exception as e:
    logger.critical(f"初始化Qwen-VL (DashScope)客户端失败: {e}")
    qwen_client = None


def _call_qwen_api(image_paths: List[Path], prompt_template: Dict[str, str], model_name: str, extra_params: Dict = None) -> Union[str, None]:
    """
    通用的Qwen-VL API调用函数，是本模块所有功能的核心。
    """
    if not qwen_client:
        logger.error("Qwen-VL客户端未初始化，API调用中止。")
        return None

        # 【优化】: 构建包含 system 和 user 角色的 messages 列表
    messages = [
        {"role": "system", "content": prompt_template.get("system", "You are a helpful assistant.")}
    ]

    # 构建 user message，它可以包含文本和多张图片
    user_content = [{"type": "text", "text": prompt_template.get("user", "")}]
    for image_path in image_paths:
        base64_image = encode_image_to_base64(image_path)
        if base64_image:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})

    messages.append({"role": "user", "content": user_content})

    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 8192,
        "stream": False  # 视觉模型通常不使用流式
    }
    if extra_params:
        payload.update(extra_params)

    try:
        completion = qwen_client.chat.completions.create(**payload)  # type: ignore
        return completion.choices[0].message.content.strip() if completion.choices[0].message.content else None
    except Exception as e:
        logger.error(f"调用模型 '{model_name}' 时发生错误: {e}", exc_info=True)
        return None


def classify_problem_type(image_paths: List[Path]) -> str:
    """
    步骤 1: 问题分类。调用Qwen-VL对图片内容进行宏观分析。
    """
    logger.info("步骤 1: 正在进行问题类型分类...")
    response = _call_qwen_api(image_paths, config.CLASSIFICATION_PROMPT)
    valid_types = ["CODING", "VISUAL_REASONING", "QUESTION_ANSWERING", "GENERAL"]
    if response and response in valid_types:
        logger.info(f"分类成功，识别类型为: {response}")
        return response
    logger.warning(f"分类失败或返回未知类型 ('{response}')。将默认视为 'GENERAL'。")
    return "GENERAL"


def transcribe_images_raw(image_paths: List[Path]) -> Union[List[str], None]:
    """
    并行地、独立地转录每一张图片，并返回一个包含原始OCR结果的字符串列表。
    """
    logger.info(f"启动对 {len(image_paths)} 张图片的并行独立转录...")
    transcriptions = [""] * len(image_paths)

    def transcribe_single(index, path):
        text = _call_qwen_api([path], config.TRANSCRIPTION_PROMPT)
        return index, text

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_index = {executor.submit(transcribe_single, i, p): i for i, p in enumerate(image_paths)}

        all_successful = True
        for future in concurrent.futures.as_completed(future_to_index):
            try:
                index, text = future.result()
                if text:
                    transcriptions[index] = text
                    logger.info(f"  - 成功完成并行转录，图片 {index + 1}/{len(image_paths)}。")
                else:
                    all_successful = False
                    logger.error(f"  - 并行转录失败，图片 {index + 1}/{len(image_paths)}。")
            except Exception as e:
                all_successful = False
                logger.error(f"转录线程池任务执行时发生异常: {e}")

    if not all_successful:
        logger.error("由于至少有一个图片转录失败，整个转录流程中止。")
        return None

    logger.info("所有图片独立转录完成。")
    return transcriptions

# 专门用于视觉推理的函数
def solve_visual_reasoning_problem(image_paths: List[Path]) -> Union[str, None]:
    """
    调用专用的 `qwen3-vl-thinking` 模型来解决视觉推理问题。
    """
    logger.info(f"步骤 2.2: 正在使用专用视觉思考模型 '{config.QWEN_VL_THINKING_MODEL_NAME}' 进行求解...")
    # 根据API文档，传递 thinking_budget 参数
    extra_params = {
        "extra_body": {"thinking_budget": 4000}
    }
    return _call_qwen_api(
        image_paths,
        config.PROMPT_TEMPLATES["VISUAL_REASONING"],
        config.QWEN_VL_THINKING_MODEL_NAME,
        extra_params
    )
