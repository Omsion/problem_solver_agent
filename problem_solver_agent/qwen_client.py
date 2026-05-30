# -*- coding: utf-8 -*-
"""
qwen_client.py - GLM-4.6V (Zhipu) 视觉API 客户端 (V3.0 - 智谱适配版)

V3.0 版本更新:
- 【供应商切换】: 从 DashScope (Qwen-VL) 迁移到 Zhipu (GLM-4.6V) 视觉模型。
- 【API密钥】: 使用 ZHIPU_API_KEY。
- 【端点更新】: base_url 指向 Zhipu OpenAI 兼容端点。
- 【参数清理】: 移除 Qwen 专用的 thinking_budget 参数。
"""
import concurrent.futures
import time
from pathlib import Path
from typing import List, Union, Dict, Any, Generator
from typing_extensions import TypedDict
from openai import OpenAI, APIConnectionError, APITimeoutError

from . import config
from .utils import setup_logger, encode_image_to_base64

logger = setup_logger()


# --- 类型定义 ---
class VisionCompletionPayload(TypedDict, total=False):
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int
    stream: bool
    extra_body: Dict[str, Any]


# --- 客户端初始化 ---
try:
    if not config.ZHIPU_API_KEY:
        raise ValueError("未在 .env 文件中找到 ZHIPU_API_KEY。")
    qwen_client = OpenAI(api_key=config.ZHIPU_API_KEY, base_url=config.VISION_BASE_URL)
except Exception as e:
    logger.critical(f"初始化视觉 (Zhipu GLM-4.6V) 客户端失败: {e}")
    qwen_client = None


def _call_qwen_api(image_paths: List[Path], user_prompt: str, model_name: str, stream: bool = False,
                   extra_params: Dict = None) -> Union[str, Generator[str, None, None], None]:
    """
    核心API调用函数，内置了健壮的自动重试逻辑。

    当遇到可恢复的网络相关错误时 (如连接错误、超时)，它会根据 config.py
    中的配置自动重试，从而极大地提高在不稳定网络环境下的成功率。
    """
    if not qwen_client:
        logger.error("Qwen-VL客户端未初始化，API调用中止。")
        return None

    # --- 构建请求体 (Payload) ---
    user_content = [{"type": "text", "text": user_prompt}]
    for image_path in image_paths:
        base64_image = encode_image_to_base64(image_path)
        if base64_image:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})

    messages = [{"role": "user", "content": user_content}]

    payload: VisionCompletionPayload = {
        "model": model_name, "messages": messages, "max_tokens": 8192, "stream": stream
    }
    if extra_params:
        payload.update(extra_params)

    # --- 核心：自动重试循环 ---
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            # 尝试执行API调用
            completion = qwen_client.chat.completions.create(**payload)  # type: ignore

            # --- 成功路径 ---
            # 如果API调用成功，则处理响应并立即返回，跳出重试循环
            if stream:
                def stream_generator():
                    for chunk in completion:
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content

                return stream_generator()
            else:
                return completion.choices[0].message.content.strip() if completion.choices[0].message.content else None

        except (APIConnectionError, APITimeoutError) as e:
            # --- 可重试错误的捕获路径 ---
            log_message = f"调用模型 '{model_name}' 时发生网络错误 (尝试 {attempt + 1}/{config.MAX_RETRIES + 1}): {e}"
            if attempt < config.MAX_RETRIES:
                logger.warning(log_message)
                logger.info(f"将在 {config.RETRY_DELAY} 秒后重试...")
                time.sleep(config.RETRY_DELAY)
            else:
                # 如果已达到最大重试次数，则记录严重错误并放弃
                logger.error(f"达到最大重试次数，调用 '{model_name}' 最终失败。")
                break  # 跳出循环，执行下面的失败返回逻辑

        except Exception as e:
            # --- 不可重试错误的捕获路径 ---
            # 捕获其他所有意外错误（如认证失败、无效请求），记录后立即放弃，不进行重试
            logger.error(f"调用模型 '{model_name}' 时发生未知的严重错误: {e}", exc_info=True)
            break  # 跳出循环，执行下面的失败返回逻辑

    # --- 统一的失败返回逻辑 ---
    # 只有当所有重试都失败或遇到不可重试错误时，才会执行到这里
    if stream:
        def error_generator():
            yield f"\n\n--- ERROR in qwen_client: All retries failed. ---\n"

        return error_generator()
    return None


# ==============================================================================
# 以下所有公共函数都无需修改，它们会自动继承 _call_qwen_api 的重试能力
# ==============================================================================
def classify_problem_type(image_paths: List[Path]) -> str:
    logger.info("步骤 1: 正在进行问题类型分类...")
    response = _call_qwen_api(image_paths, config.CLASSIFICATION_PROMPT, config.VISION_CLASSIFY_MODEL, stream=False)
    valid_types = ["CODING", "VISUAL_REASONING", "QUESTION_ANSWERING", "GENERAL", "MULTIPLE_CHOICE",
                   "FILL_IN_THE_BLANKS"]

    if isinstance(response, str) and response in valid_types:
        logger.info(f"分类成功，识别类型为: {response}")
        return response

    logger.warning(f"分类失败或返回未知类型 ('{response}')。将默认视为 'GENERAL'。")
    return "GENERAL"


def transcribe_images_raw(image_paths: List[Path]) -> Union[List[str], None]:
    logger.info(f"启动对 {len(image_paths)} 张图片的并行独立转录...")
    transcriptions = [""] * len(image_paths)

    def transcribe_single(index, path):
        return index, _call_qwen_api([path], config.TRANSCRIPTION_PROMPT, config.VISION_CLASSIFY_MODEL, stream=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_index = {executor.submit(transcribe_single, i, p): i for i, p in enumerate(image_paths)}
        all_successful = True
        for future in concurrent.futures.as_completed(future_to_index):
            try:
                index, text = future.result()
                if isinstance(text, str) and text:
                    transcriptions[index] = text
                    logger.info(f"  - 成功完成并行转录，图片 {index + 1}/{len(image_paths)}。")
                else:
                    all_successful = False
                    logger.error(f"  - 并行转录失败（返回空内容），图片 {index + 1}/{len(image_paths)}。")
            except Exception as e:
                all_successful = False
                logger.error(f"转录线程池任务执行时发生异常: {e}")
        if not all_successful: return None
    return transcriptions


def solve_visual_reasoning_problem(image_paths: List[Path]) -> Union[Generator[str, None, None], None]:
    """
    调用专用的视觉推理模型 (GLM-4.6V) 来解决视觉推理问题。
    """
    logger.info(f"步骤 2.2: 正在使用专用视觉推理模型 '{config.VISION_REASONING_MODEL}' 进行求解...")
    extra_params = {
        "top_p": 0.8,
        "temperature": 0.7
    }
    return _call_qwen_api(
        image_paths,
        config.PROMPT_TEMPLATES["VISUAL_REASONING"],
        config.VISION_REASONING_MODEL,
        stream=True,
        extra_params=extra_params
    )
