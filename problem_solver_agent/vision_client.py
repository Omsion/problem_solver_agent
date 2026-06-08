"""
vision_client.py - 视觉 API 客户端 (Provider-Agnostic)

本模块封装了所有与多模态视觉模型（图像分类、OCR转录、视觉推理）的交互。
通过 config.py 中的 VISION_BASE_URL / VISION_CLASSIFY_MODEL / VISION_REASONING_MODEL
控制具体使用的模型和端点，切换模型只需修改 config.py 无需改动本文件。

当前配置: Zhipu GLM-4.6V 系列
- 分类: GLM-4.6V-FlashX (chat/completions)
- OCR:   GLM-OCR (layout_parsing)
- 视觉推理: GLM-4.6V (chat/completions)
"""
import json
import random
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, TypedDict

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI

from . import config, prompts
from .utils import encode_image_to_base64, setup_logger

logger = setup_logger()


# --- 类型定义 ---
class VisionCompletionPayload(TypedDict, total=False):
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int
    stream: bool
    extra_body: dict[str, Any]


# --- 客户端延迟初始化 ---
_vision_client: OpenAI | None = None


def _get_vision_client() -> OpenAI | None:
    """获取或延迟初始化视觉 API 客户端（单例）。"""
    global _vision_client
    if _vision_client is not None:
        return _vision_client
    if not config.ZHIPU_API_KEY:
        logger.critical("未在 .env 文件中找到 ZHIPU_API_KEY，视觉客户端初始化失败。")
        return None
    try:
        _vision_client = OpenAI(api_key=config.ZHIPU_API_KEY, base_url=config.VISION_BASE_URL)
        logger.info("视觉客户端初始化成功。")
    except Exception as e:
        logger.critical(f"初始化视觉客户端失败 (base_url={config.VISION_BASE_URL}): {e}")
        return None
    return _vision_client


def _call_vision_api(image_paths: list[Path], user_prompt: str, model_name: str,
                     stream: bool = False,
                     extra_params: dict = None) -> str | Generator[str, None, None] | None:
    """
    核心视觉API调用函数，内置健壮的自动重试逻辑。

    当遇到可恢复的网络相关错误时 (如连接错误、超时)，根据 config.py
    中的配置自动重试，提高不稳定网络环境下的成功率。

    Args:
        image_paths: 图片路径列表
        user_prompt: 用户提示词
        model_name: 模型名称 (来自 config 常量)
        stream: 是否流式返回
        extra_params: 额外的 API 参数 (如 top_p, temperature)

    Returns:
        非流式: 文本响应字符串
        流式: 字符串生成器
        失败: None
    """
    if not _get_vision_client():
        logger.error("视觉客户端未初始化，API调用中止。")
        return None

    # --- 构建请求体 (Payload) ---
    user_content = [{"type": "text", "text": user_prompt}]
    for image_path in image_paths:
        base64_image = encode_image_to_base64(image_path)
        if base64_image:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            })

    messages = [{"role": "user", "content": user_content}]

    payload: VisionCompletionPayload = {
        "model": model_name, "messages": messages, "max_tokens": 8192, "stream": stream
    }
    if extra_params:
        payload.update(extra_params)

    # --- 核心：自动重试循环 ---
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            completion = _get_vision_client().chat.completions.create(**payload)  # type: ignore

            if stream:
                def stream_generator():
                    for chunk in completion:
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content

                return stream_generator()
            else:
                return (completion.choices[0].message.content.strip()
                        if completion.choices[0].message.content else None)

        except (APIConnectionError, APITimeoutError) as e:
            log_message = (
                f"调用模型 '{model_name}' 时发生网络错误 "
                f"(尝试 {attempt + 1}/{config.MAX_RETRIES + 1}): {e}"
            )
            if attempt < config.MAX_RETRIES:
                logger.warning(log_message)
                logger.info(f"将在 {config.RETRY_DELAY} 秒后重试...")
                time.sleep(config.RETRY_DELAY)
            else:
                logger.error(f"达到最大重试次数，调用 '{model_name}' 最终失败。")
                break

        except Exception as e:
            logger.error(f"调用模型 '{model_name}' 时发生未知的严重错误: {e}", exc_info=True)
            break

    # --- 统一的失败返回逻辑 ---
    if stream:
        def error_generator():
            yield f"\n\n--- ERROR in vision_client: All retries failed for {model_name}. ---\n"

        return error_generator()
    return None


# ==============================================================================
# GLM-OCR — 专用文档解析 API
# ==============================================================================

def _call_glm_ocr(image_path: Path) -> str | None:
    """调用 GLM-OCR layout_parsing API 进行单张图片的文档解析。

    GLM-OCR 是智谱 0.9B 参数的专用 OCR 模型，使用 layout_parsing 端点，
    在文档解析、表格识别、公式提取等场景中达到 SOTA 水平。

    Args:
        image_path: 单张图片路径

    Returns:
        解析后的 Markdown 文本，失败返回 None
    """
    base64_data = encode_image_to_base64(image_path)
    if not base64_data:
        logger.error(f"GLM-OCR: 图片编码失败 {image_path.name}")
        return None

    url = f"{config.VISION_BASE_URL.rstrip('/')}/layout_parsing"
    headers = {
        "Authorization": f"Bearer {config.ZHIPU_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.VISION_OCR_MODEL,
        "file": f"data:image/jpeg;base64,{base64_data}",
    }

    for attempt in range(config.MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=config.API_TIMEOUT) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    return content.strip()
                logger.error(f"GLM-OCR 返回空内容: {image_path.name}")
                return None

        except httpx.HTTPStatusError as e:
            is_rate_limited = e.response.status_code == 429
            log_msg = (
                f"GLM-OCR {'限流(429)' if is_rate_limited else 'HTTP错误'} (尝试 {attempt + 1}/{config.MAX_RETRIES + 1}): {e}"
            )
            if attempt < config.MAX_RETRIES:
                # 429 限流使用指数退避 + 随机抖动，避免重试风暴
                delay = config.RETRY_DELAY * (2 ** attempt) + random.uniform(0, 3)
                logger.warning(log_msg)
                logger.info(f"将在 {delay:.1f} 秒后重试...")
                time.sleep(delay)
            else:
                logger.error(f"GLM-OCR 达到最大重试次数，最终失败: {image_path.name}")
                return None
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            log_msg = (
                f"GLM-OCR 网络错误 (尝试 {attempt + 1}/{config.MAX_RETRIES + 1}): {e}"
            )
            if attempt < config.MAX_RETRIES:
                logger.warning(log_msg)
                logger.info(f"将在 {config.RETRY_DELAY} 秒后重试...")
                time.sleep(config.RETRY_DELAY)
            else:
                logger.error(f"GLM-OCR 达到最大重试次数，最终失败: {image_path.name}")
                return None
        except Exception as e:
            logger.error(f"GLM-OCR 未知错误: {e}", exc_info=True)
            return None

    return None


# ==============================================================================
# 公共 API —— 切换模型只需修改 config.py，以下函数无需改动
# ==============================================================================

def classify_problem_type(image_paths: list[Path]) -> str:
    """对图片内容进行问题类型分类，返回 CODING / MULTIPLE_CHOICE / ... 等标签。"""
    logger.info("步骤 1: 正在进行问题类型分类...")
    response = _call_vision_api(
        image_paths, prompts.CLASSIFICATION_PROMPT, config.VISION_CLASSIFY_MODEL, stream=False
    )
    valid_types = [
        "CODING", "VISUAL_REASONING", "QUESTION_ANSWERING",
        "GENERAL", "MULTIPLE_CHOICE", "FILL_IN_THE_BLANKS"
    ]

    if isinstance(response, str) and response in valid_types:
        logger.info(f"分类成功，识别类型为: {response}")
        return response

    logger.warning(f"分类失败或返回未知类型 ('{response}')。将默认视为 'GENERAL'。")
    return "GENERAL"


def transcribe_images_raw(image_paths: list[Path]) -> list[str] | None:
    """对多张图片串行执行 OCR 转录（GLM-OCR），返回结构化 Markdown 文本列表。

    返回值仅在所有图片均失败时为 None；部分成功时返回成功部分，
    后续文本合并步骤将仅处理已成功转录的图片。
    """
    logger.info(f"启动 GLM-OCR 对 {len(image_paths)} 张图片的串行转录...")
    transcriptions: list[str] = []
    failures = 0

    for i, p in enumerate(image_paths):
        text = _call_glm_ocr(p)
        if isinstance(text, str) and text:
            transcriptions.append(text)
            logger.info(f"  - GLM-OCR 成功，图片 {i + 1}/{len(image_paths)}。")
        else:
            failures += 1
            logger.error(f"  - GLM-OCR 失败（返回空内容），图片 {i + 1}/{len(image_paths)}。")

    if not transcriptions:
        logger.error(f"GLM-OCR 全部 {len(image_paths)} 张图片转录失败。")
        return None

    if failures:
        logger.warning(f"GLM-OCR 部分失败: {failures}/{len(image_paths)} 张图片未能转录，"
                       f"将继续处理 {len(transcriptions)} 张成功的图片。")

    return transcriptions


def solve_visual_reasoning_problem(image_paths: list[Path]) -> Generator[str, None, None] | None:
    """使用专用视觉推理模型解决图形/规律推理类问题。"""
    logger.info(f"步骤 2.2: 正在使用视觉推理模型 '{config.VISION_REASONING_MODEL}' 进行求解...")
    return _call_vision_api(
        image_paths,
        prompts.PROMPT_TEMPLATES["VISUAL_REASONING"],
        config.VISION_REASONING_MODEL,
        stream=True,
        extra_params={"top_p": 0.8, "temperature": 0.7}
    )
