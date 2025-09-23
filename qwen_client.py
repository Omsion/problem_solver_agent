# -*- coding: utf-8 -*-
"""
qwen_client.py - Qwen-VL (DashScope) API 客户端 (V2.0 - 健壮版)

本模块是Agent的“视觉中枢”，利用通义千问视觉语言模型（Qwen-VL）的强大能力，
处理所有直接与图片内容理解相关的任务。它现在承担三项关键职责：

1.  **问题分类 (classify_problem_type)**:
    作为工作流的第一步，它对图片进行宏观分析，判断问题属于“编程”、“视觉推理”、
    “问答”还是“通用文字”中的哪一类。这个分类结果将决定后续整个处理流程。

2.  **文字转录 (transcribe_images)**:
    对于文本密集型问题，它扮演高精度OCR和文档重构的角色。通过采用“并行分治”策略
    和结构化的提示词，它能高效地将多张图片中的所有内容（包括表格和公式）提取并
    智能合并成一份结构化的文本，为后续的文本分析模型做准备。

3.  **视觉求解 (solve_visual_problem)**:
    对于纯视觉的图形推理题，它将绕过文字转录，直接利用Qwen-VL的多模态能力，
    观察、推理并解答问题。
"""
import concurrent.futures
from pathlib import Path
from typing import List, Union, Dict, Any
from typing_extensions import TypedDict
from openai import OpenAI
import base64

import config
from utils import setup_logger, encode_image_to_base64, preprocess_image_for_ocr, merge_transcribed_texts

logger = setup_logger()


# 使用TypedDict为API的负载（payload）定义一个明确的数据结构，增强代码可读性和健壮性。
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


def _call_qwen_api(image_paths: List[Path], prompt: str, use_preprocessing: bool = False) -> Union[str, None]:
    """
    通用的Qwen-VL API调用函数，是本模块所有功能的核心。

    Args:
        image_paths (List[Path]): 待处理的图片路径列表。
        prompt (str): 发送给模型的指令（提示词）。
        use_preprocessing (bool): 是否对图片进行增强预处理以提升OCR准确率。默认为False。
    """
    if not qwen_client:
        logger.error("Qwen-VL客户端未初始化，API调用中止。")
        return None

    content_payload = [{"type": "text", "text": prompt}]
    for image_path in image_paths:
        base64_image = encode_image_to_base64(image_path)
        if base64_image:
            content_payload.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            })
        else:
            logger.warning(f"编码或读取图片失败，已跳过: {image_path.name}")

    if len(content_payload) <= 1:
        logger.error("没有有效的图片可发送至Qwen-VL，API调用中止。")
        return None

    payload: VisionCompletionPayload = {
        "model": config.QWEN_MODEL_NAME,
        "messages": [{"role": "user", "content": content_payload}],
        "max_tokens": 8192
    }

    try:
        completion = qwen_client.chat.completions.create(**payload)
        response_text = completion.choices[0].message.content
        return response_text.strip() if response_text else None
    except Exception as e:
        logger.error(f"调用Qwen-VL API时发生错误: {e}", exc_info=True)
        return None


def classify_problem_type(image_paths: List[Path]) -> str:
    """
    步骤 1: 问题分类。调用Qwen-VL对图片内容进行宏观分析。
    此任务依赖整体视觉信息，因此不进行图像预处理。
    """
    logger.info("步骤 1: 正在进行问题类型分类...")
    response = _call_qwen_api(image_paths, config.CLASSIFICATION_PROMPT, use_preprocessing=False)

    valid_types = ["CODING", "VISUAL_REASONING", "QUESTION_ANSWERING", "GENERAL"]
    if response and response in valid_types:
        logger.info(f"分类成功，识别类型为: {response}")
        return response

    logger.warning(f"分类失败或返回未知类型 ('{response}')。将默认视为 'GENERAL' 以保证流程健壮性。")
    return "GENERAL"


def transcribe_images(image_paths: List[Path]) -> Union[str, None]:
    """
    【并行分治策略】分别转录每张图片，然后智能合并结果。
    这种方法比将所有图片一次性发给模型要求合并的效果更好，可以处理更长的文档，
    且不易丢失细节。
    """
    logger.info("步骤 3.1: 启动“并行分治”+“结构化识别”合并流程...")

    transcriptions = [None] * len(image_paths)

    # 定义一个内部函数，用于在单独的线程中执行API调用
    def transcribe_single(index, path):
        logger.info(f"  - 开始并行转录图片 {index + 1}/{len(image_paths)}: {path.name}...")
        text = _call_qwen_api([path], config.TRANSCRIPTION_PROMPT, use_preprocessing=False)
        if text:
            logger.info(f"  - 成功完成并行转录，图片 {index + 1}/{len(image_paths)}。")
            return index, text
        else:
            logger.error(f"  - 并行转录失败，图片 {index + 1}/{len(image_paths)}。")
            return index, None

    # 使用线程池来并行执行转录任务，显著缩短多图问题的处理时间。
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # map会阻塞直到所有任务完成，并按提交顺序返回结果。
        future_to_result = {executor.submit(transcribe_single, i, p): i for i, p in enumerate(image_paths)}

        all_successful = True
        for future in concurrent.futures.as_completed(future_to_result):
            try:
                index, text = future.result()
                if text is None:
                    all_successful = False
                transcriptions[index] = text
            except Exception as e:
                logger.error(f"转录线程池任务执行时发生异常: {e}")
                all_successful = False

    if not all_successful:
        logger.error("由于至少有一个图片转录失败，整个合并流程中止。")
        return None

    logger.info("所有图片独立转录完成，开始进行程序化文本合并...")
    final_text = merge_transcribed_texts(transcriptions)
    logger.info("文本合并成功！")
    return final_text


def solve_visual_problem(image_paths: List[Path], prompt_template: str) -> Union[str, None]:
    """
    步骤 3.2: 直接进行视觉推理求解。
    该任务需要原始的图形、颜色和纹理信息，严禁进行任何可能破坏图像细节的预处理。
    """
    logger.info("步骤 3.2: 正在直接进行视觉推理求解...")
    return _call_qwen_api(image_paths, prompt_template, use_preprocessing=False)