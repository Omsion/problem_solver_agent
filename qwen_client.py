# -*- coding: utf-8 -*-
"""
qwen_client.py - Qwen-VL (DashScope) API 客户端

本模块是Agent的“视觉中枢”，利用通义千问视觉语言模型（Qwen-VL）的强大能力，
处理所有直接与图片内容理解相关的任务。它现在承担三项关键职责：

1.  **问题分类 (classify_problem_type)**:
    作为工作流的第一步，它对图片进行宏观分析，判断问题属于“编程题”、“视觉推理题”还是“通用文字题”中的哪一类。这个分类结果将决定后续整个处理流程。

2.  **文字转录 (transcribe_images)**:
    对于文本密集型问题（编程题和通用题），它扮演高精度OCR的角色，将图片中的所有文字信息提取出来，为后续的文本分析模型做准备。

3.  **视觉求解 (solve_visual_problem)**:
    对于纯视觉的图形推理题，它将绕过文字转录，直接利用Qwen-VL的多模态能力，观察、推理并解答问题。
"""

from pathlib import Path
from typing import List, Union, Dict, Any
from typing_extensions import TypedDict
from openai import OpenAI

import config
from utils import setup_logger, encode_image_to_base64

logger = setup_logger()

# 为了代码的健壮性和可读性，使用TypedDict为API的负载（payload）定义一个明确的数据结构。
# 这可以帮助IDE进行类型检查，并解决了之前因模型名称不匹配而产生的警告。
class VisionCompletionPayload(TypedDict):
    model: str
    messages: List[Dict[str, Any]]

# --- Qwen-VL 客户端的单例初始化 ---
# 将客户端的初始化放在模块级别，可以确保在整个程序运行期间只创建一个实例，
# 避免了每次调用都重新建立连接的开销，提高了效率。
try:
    if not config.DASHSCOPE_API_KEY:
        raise ValueError("未在 .env 文件中找到 DASHSCOPE_API_KEY。")
    qwen_client = OpenAI(
        api_key=config.DASHSCOPE_API_KEY,
        base_url=config.DASHSCOPE_BASE_URL,
    )
except Exception as e:
    logger.critical(f"初始化Qwen-VL (DashScope)客户端失败: {e}")
    qwen_client = None


def _call_qwen_api(image_paths: List[Path], prompt: str) -> Union[str, None]:
    """
    一个通用的私有辅助函数，封装了调用Qwen API的核心逻辑，以减少代码重复。
    """
    if not qwen_client:
        logger.error("Qwen-VL客户端未初始化，API调用中止。")
        return None

    # 构建API请求体中的'content'部分，这是一个包含文本和多张图片的列表。
    content_payload = [{"type": "text", "text": prompt}]
    for image_path in image_paths:
        base64_image = encode_image_to_base64(image_path)
        if base64_image:
            content_payload.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            })
        else:
            logger.warning(f"跳过无法进行Base64编码的图片: {image_path}")

    if len(content_payload) <= 1:
        logger.error("没有有效的图片可发送至Qwen-VL。")
        return None

    # 使用TypedDict构建结构化的负载
    payload: VisionCompletionPayload = {
        "model": config.QWEN_MODEL_NAME,
        "messages": [{"role": "user", "content": content_payload}]
    }

    try:
        # 使用kwargs解包(**payload)的方式传递参数，代码更简洁且能配合TypedDict进行类型检查。
        completion = qwen_client.chat.completions.create(**payload)
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Qwen-VL API 请求失败: {e}")
        return None


def classify_problem_type(image_paths: List[Path]) -> str:
    """
    步骤 1: 调用Qwen-VL进行“三元分类”。
    返回 'CODING', 'VISUAL_REASONING', 或 'GENERAL'。
    """
    logger.info("步骤 1: 正在进行问题类型分类...")
    response = _call_qwen_api(image_paths, config.CLASSIFICATION_PROMPT)

    # 对模型的返回结果进行严格校验，确保其在我们预设的分类中。
    if response and response in ["CODING", "VISUAL_REASONING", "GENERAL"]:
        logger.info(f"分类成功，识别类型为: {response}")
        return response

    # 如果分类失败或返回意外结果，提供一个安全的默认值'GENERAL'，保证程序流程的健壮性。
    logger.warning(f"分类失败或返回未知类型 ('{response}')。将默认视为 'GENERAL'。")
    return "GENERAL"


def transcribe_images(image_paths: List[Path]) -> Union[str, None]:
    """
    步骤 3.1: 调用Qwen-VL进行文字转录 (文本类问题路径)。
    """
    logger.info("步骤 3.1: 正在进行图片文字转录...")
    return _call_qwen_api(image_paths, config.TRANSCRIPTION_PROMPT)


def solve_visual_problem(image_paths: List[Path], prompt_template: str) -> Union[str, None]:
    """
    步骤 3.2: 直接调用Qwen-VL进行视觉推理求解 (视觉类问题路径)。
    """
    logger.info("步骤 3.2: 正在直接进行视觉推理求解...")
    # 复用通用的API调用函数，但传入的是专门为视觉推理设计的提示词。
    return _call_qwen_api(image_paths, prompt_template)