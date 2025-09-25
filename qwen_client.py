# -*- coding: utf-8 -*-
"""
qwen_client.py - Qwen-VL (DashScope) API 客户端 (V2.2 - 动态流式版)

V2.2 版本更新:
- 【核心修复】: 重构 `_call_qwen_api` 以支持动态的流式（stream=True）和
  非流式（stream=False）调用。
- `solve_visual_reasoning_problem` 现在强制使用流式调用，以满足
  `qwen3-vl-thinking` 等高级模型对流式输出的强制要求，解决了 `400` 错误。
- `classify_problem_type` 和 `transcribe_images_raw` 保持非流式，
  以获取完整的原子结果。
"""
import concurrent.futures
from pathlib import Path
from typing import List, Union, Dict, Any, Generator
from typing_extensions import TypedDict
from openai import OpenAI
import base64

import config
from utils import setup_logger, encode_image_to_base64

logger = setup_logger()


class VisionCompletionPayload(TypedDict, total=False):
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int
    stream: bool
    extra_body: Dict[str, Any]


try:
    if not config.DASHSCOPE_API_KEY:
        raise ValueError("未在 .env 文件中找到 DASHSCOPE_API_KEY。")
    qwen_client = OpenAI(api_key=config.DASHSCOPE_API_KEY, base_url=config.QWEN_BASE_URL)
except Exception as e:
    logger.critical(f"初始化Qwen-VL (DashScope)客户端失败: {e}")
    qwen_client = None


def _call_qwen_api(image_paths: List[Path], prompt_template: Dict[str, str], model_name: str, stream: bool = False,
                   extra_params: Dict = None) -> Union[str, Generator[str, None, None], None]:
    if not qwen_client:
        logger.error("Qwen-VL客户端未初始化，API调用中止。")
        return None

    # 构建包含 system 和 user 角色的 messages 列表
    messages = [{"role": "system", "content": prompt_template.get("system", "You are a helpful assistant.")}]

    # 构建 user message，它可以包含文本和多张图片
    user_content = [{"type": "text", "text": prompt_template.get("user", "")}]
    for image_path in image_paths:
        base64_image = encode_image_to_base64(image_path)
        if base64_image:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})
    messages.append({"role": "user", "content": user_content})

    payload: VisionCompletionPayload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 8192,
        "stream": stream
    }
    if extra_params:
        payload.update(extra_params)

    try:
        completion = qwen_client.chat.completions.create(**payload)  # type: ignore

        if stream:
            def stream_generator():
                for chunk in completion:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

            return stream_generator()
        else:
            return completion.choices[0].message.content.strip() if completion.choices[0].message.content else None

    except Exception as e:
        logger.error(f"调用模型 '{model_name}' 时发生错误: {e}", exc_info=True)
        # 在流式模式下，如果发生错误，返回一个产生错误信息的生成器
        if stream:
            def error_generator():
                yield f"\n\n--- ERROR in qwen_client ---\n{e}\n--- END ERROR ---\n"

            return error_generator()
        return None


def classify_problem_type(image_paths: List[Path]) -> str:
    logger.info("步骤 1: 正在进行问题类型分类...")
    response = _call_qwen_api(image_paths, config.CLASSIFICATION_PROMPT, config.QWEN_MODEL_NAME, stream=False)
    valid_types = ["CODING", "VISUAL_REASONING", "QUESTION_ANSWERING", "GENERAL"]
    if isinstance(response, str) and response in valid_types:
        return response
    return "GENERAL"


def transcribe_images_raw(image_paths: List[Path]) -> Union[List[str], None]:
    logger.info(f"启动对 {len(image_paths)} 张图片的并行独立转录...")
    transcriptions = [""] * len(image_paths)

    def transcribe_single(index, path):
        return index, _call_qwen_api([path], config.TRANSCRIPTION_PROMPT, config.QWEN_MODEL_NAME, stream=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_index = {executor.submit(transcribe_single, i, p): i for i, p in enumerate(image_paths)}
        all_successful = True
        for future in concurrent.futures.as_completed(future_to_index):
            try:
                index, text = future.result()
                if isinstance(text, str):
                    transcriptions[index] = text
                    logger.info(f"  - 成功完成并行转录，图片 {index + 1}/{len(image_paths)}。")
                else:
                    all_successful = False
                    logger.error(f"  - 并行转录失败，图片 {index + 1}/{len(image_paths)}。")
            except Exception as e:
                all_successful = False;
                logger.error(f"转录线程池任务执行时发生异常: {e}")
        if not all_successful: return None
    return transcriptions


# 专门用于视觉推理的函数
def solve_visual_reasoning_problem(image_paths: List[Path]) -> Union[Generator[str, None, None], None]:
    """
    调用专用的 `qwen3-vl-thinking` 模型来解决视觉推理问题。
    """
    logger.info(f"步骤 2.2: 正在使用专用视觉思考模型 '{config.QWEN_VL_THINKING_MODEL_NAME}' 进行求解...")
    extra_params = {"extra_body": {"thinking_budget": 4000}}

    # 强制使用流式调用 (stream=True)
    return _call_qwen_api(
        image_paths,
        config.PROMPT_TEMPLATES["VISUAL_REASONING"],
        config.QWEN_VL_THINKING_MODEL_NAME,
        stream=True,
        extra_params=extra_params
    )