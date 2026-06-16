"""
vision_client.py - 视觉 API 客户端 (Provider-Agnostic)

本模块封装了所有与多模态视觉模型（图像分类、OCR转录、视觉推理）的交互。
通过 config.py 中的 VISION_BASE_URL / VISION_CLASSIFY_MODEL / VISION_REASONING_MODEL
控制具体使用的模型和端点，切换模型只需修改 config.py 无需改动本文件。

当前配置: Zhipu GLM-4.6V 系列
- 分类: GLM-4.6V-FlashX (chat/completions)
- OCR:   GLM-4.6V-FlashX (chat/completions)
- 视觉推理: GLM-4.6V (chat/completions)
"""
import concurrent.futures
import json
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, TypedDict

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from . import config, image_prep, prompts
from .utils import setup_logger

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
        _vision_client = OpenAI(
            api_key=config.ZHIPU_API_KEY,
            base_url=config.VISION_BASE_URL,
            # 显式设置超时：SDK 默认值偏大，分类/OCR 这类短任务一旦网络异常
            # 会长时间挂起，用户只看到"一直在处理"
            timeout=config.VISION_TIMEOUT,
            max_retries=0,  # 重试由下面的循环统一控制，避免双重重试放大等待
        )
        logger.info("视觉客户端初始化成功。")
    except Exception as e:
        logger.critical(f"初始化视觉客户端失败 (base_url={config.VISION_BASE_URL}): {e}")
        return None
    return _vision_client


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试（网络问题 + 限流）。"""
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    return False



def _build_user_content(image_paths: list[Path], user_prompt: str) -> list[dict[str, Any]]:
    """用 image_prep 预处理后的图片构建多模态消息体。

    预处理会做 EXIF 校正、限制最长边并统一转成 JPEG，因此这里的 MIME
    类型与真实数据始终一致（旧实现把 PNG 也标成 image/jpeg）。
    单张图片处理失败不会中断整次请求，只记录警告。
    """
    user_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    for image_path in image_paths:
        try:
            prepared = image_prep.prepare_for_api(image_path)
        except Exception as exc:  # 文件损坏 / 无法解码
            logger.warning("跳过无法预处理的图片 %s: %s", image_path, exc)
            continue
        user_content.append({
            "type": "image_url",
            "image_url": {"url": prepared.data_uri},
        })
    return user_content


def _call_vision_api(image_paths: list[Path], user_prompt: str, model_name: str,
                     stream: bool = False,
                     extra_params: dict | None = None) -> str | Generator[str, None, None] | None:
    """
    核心视觉API调用函数，内置健壮的自动重试逻辑。

    当遇到可恢复的网络相关错误（连接错误、超时、429 限流、5xx）时，
    根据 config.py 中的配置自动重试；重试间隔按指数退避增长。

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
    client = _get_vision_client()
    if not client:
        logger.error("视觉客户端未初始化，API调用中止。")
        return None

    messages = [{"role": "user", "content": _build_user_content(image_paths, user_prompt)}]

    payload: VisionCompletionPayload = {
        "model": model_name, "messages": messages, "max_tokens": 8192, "stream": stream
    }
    if extra_params:
        payload.update(extra_params)

    # --- 核心：自动重试循环（指数退避）---
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            completion = client.chat.completions.create(**payload)  # type: ignore

            if stream:
                def stream_generator():
                    for chunk in completion:
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content

                return stream_generator()
            else:
                return (completion.choices[0].message.content.strip()
                        if completion.choices[0].message.content else None)

        except Exception as e:
            retryable = _is_retryable(e)
            log_message = (
                f"调用模型 '{model_name}' 失败 "
                f"(尝试 {attempt + 1}/{config.MAX_RETRIES + 1}): {e}"
            )
            if retryable and attempt < config.MAX_RETRIES:
                delay = config.RETRY_DELAY * (2 ** attempt)
                logger.warning(log_message)
                logger.info(f"将在 {delay} 秒后重试...")
                time.sleep(delay)
            elif retryable:
                logger.error(f"达到最大重试次数，调用 '{model_name}' 最终失败。")
                break
            else:
                logger.error(f"调用模型 '{model_name}' 时发生不可重试的错误: {e}", exc_info=True)
                break

    # --- 统一的失败返回逻辑 ---
    if stream:
        def error_generator():
            yield f"\n\n--- ERROR in vision_client: All retries failed for {model_name}. ---\n"

        return error_generator()
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
    """对多张图片并行执行 OCR 转录，返回结构化文本列表。

    严格模式：任何一页失败即整体返回 None。需要"部分成功也继续"的场景
    请使用 `transcribe_images`。
    """
    result = transcribe_images(image_paths)
    if result["failed"]:
        logger.error("有 %d 张图片转录失败，严格模式下放弃整批", len(result["failed"]))
        return None
    return result["pages"]


def transcribe_images(image_paths: list[Path]) -> dict[str, Any]:
    """并行 OCR，允许部分失败。

    Returns:
        {"pages": list[str], "failed": list[int]} — failed 是失败页的下标（0 基）。
        转录成功的页即使为空串也会保留空串，但空串会被记为失败页。
    """
    if not image_paths:
        return {"pages": [], "failed": []}

    logger.info(f"启动对 {len(image_paths)} 张图片的并行转录...")
    pages: list[str] = [""] * len(image_paths)
    failed: list[int] = []

    def transcribe_single(index: int, path: Path):
        return index, _call_vision_api(
            [path], prompts.TRANSCRIPTION_PROMPT, config.VISION_CLASSIFY_MODEL, stream=False
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.OCR_PARALLEL_WORKERS) as executor:
        future_to_index = {
            executor.submit(transcribe_single, i, p): i
            for i, p in enumerate(image_paths)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                _, text = future.result()
                if isinstance(text, str) and text.strip():
                    pages[index] = text
                    logger.info(f"  - 成功完成并行转录，图片 {index + 1}/{len(image_paths)}。")
                else:
                    failed.append(index)
                    logger.error(f"  - 并行转录失败（返回空内容），图片 {index + 1}/{len(image_paths)}。")
            except Exception as e:
                failed.append(index)
                logger.error(f"转录线程池任务执行时发生异常: {e}")

    return {"pages": pages, "failed": sorted(failed)}

def parse_json_response(raw: str | None) -> dict | None:
    """从模型回复中提取 JSON 对象。

    模型经常会用 ```json 代码块包裹，或在前后加一句说明文字，
    这里做容错解析；解析不出来返回 None（调用方负责回退）。
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        # 去掉首行 ```json 与结尾的 ```
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            return None
    return parsed if isinstance(parsed, dict) else None


def classify_and_transcribe(image_paths: list[Path]) -> dict | None:
    """一次视觉调用同时得到题型与逐页文本。

    相比"先分类再逐页 OCR"的两轮调用，这里只发一次请求：省一轮网络往返，
    也省掉一次重复的图片 token 计费。

    Returns:
        {"problem_type": str, "pages": list[str]}；解析失败返回 None，
        调用方应回退到 classify_problem_type + transcribe_images 两步路径。
    """
    if not config.USE_COMBINED_VISION_CALL:
        return None

    logger.info("步骤 1+2: 合并调用（分类 + 转录）...")
    response = _call_vision_api(
        image_paths, prompts.CLASSIFY_AND_TRANSCRIBE_PROMPT, config.VISION_CLASSIFY_MODEL, stream=False
    )
    if not isinstance(response, str):
        logger.warning("合并调用无有效响应，将回退到两步流程。")
        return None

    parsed = parse_json_response(response)
    if not parsed:
        logger.warning("合并调用返回内容无法解析为 JSON，将回退到两步流程。")
        return None

    pages = parsed.get("pages")
    if not isinstance(pages, list) or len(pages) != len(image_paths):
        logger.warning(
            "合并调用返回的页数（%s）与实际图片数（%d）不符，将回退到两步流程。",
            len(pages) if isinstance(pages, list) else "N/A",
            len(image_paths),
        )
        return None

    normalized_pages = [str(p).strip() for p in pages]
    if all(not p for p in normalized_pages):
        logger.warning("合并调用返回的转录全为空，将回退到两步流程。")
        return None

    problem_type = str(parsed.get("problem_type", "")).strip().upper()
    valid_types = {
        "CODING", "VISUAL_REASONING", "QUESTION_ANSWERING",
        "GENERAL", "MULTIPLE_CHOICE", "FILL_IN_THE_BLANKS",
    }
    if problem_type not in valid_types:
        logger.warning("合并调用返回未知题型 '%s'，按 GENERAL 处理。", problem_type)
        problem_type = "GENERAL"

    logger.info("合并调用成功：题型=%s，共 %d 页", problem_type, len(normalized_pages))
    return {"problem_type": problem_type, "pages": normalized_pages}


def classify_and_transcribe_parallel(image_paths: list[Path]) -> tuple[str, list[str], list[int]]:
    """兜底路径：分类与逐页 OCR 并行执行。

    两条调用互不依赖，串行执行会把延迟相加；并行后关键路径变成
    max(T分类, T_OCR)，用户能更早开始等待求解结果。

    Returns:
        (problem_type, pages, failed_indices)
    """
    logger.info("步骤 1+2: 并行执行分类与转录...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        classify_future = executor.submit(classify_problem_type, image_paths)
        transcribe_future = executor.submit(transcribe_images, image_paths)
        try:
            problem_type = classify_future.result()
        except Exception as exc:
            logger.error("分类任务异常，按 GENERAL 处理: %s", exc)
            problem_type = "GENERAL"
        try:
            result = transcribe_future.result()
        except Exception as exc:
            logger.error("转录任务异常: %s", exc)
            result = {"pages": [""] * len(image_paths), "failed": list(range(len(image_paths)))}

    return problem_type, result["pages"], result["failed"]


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
