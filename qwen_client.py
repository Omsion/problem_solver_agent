# -*- coding: utf-8 -*-
"""
qwen_client.py - Qwen-VL (DashScope) API 客户端 (结构化重构版)

本模块是Agent的“视觉中枢”，利用通义千问视觉语言模型（Qwen-VL）的强大能力，
处理所有直接与图片内容理解相关的任务。它现在承担三项关键职责：

1.  **问题分类 (classify_problem_type)**:
    作为工作流的第一步，它对图片进行宏观分析，判断问题属于“编程”、“视觉推理”、“问答”还是“通用文字”中的哪一类。这个分类结果将决定后续整个处理流程。

2.  **文字转录 (transcribe_images)**:
    对于文本密集型问题，它扮演高精度OCR和文档重构的角色。通过使用增强后的`TRANSCRIPTION_PROMPT`，
    它能将多张图片中的所有内容（包括表格和公式）提取并智能合并成一份结构化的文本，为后续的文本分析模型做准备。

3.  **视觉求解 (solve_visual_problem)**:
    对于纯视觉的图形推理题，它将绕过文字转录，直接利用Qwen-VL的多模态能力，观察、推理并解答问题。
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

# 为了代码的健壮性和可读性，使用TypedDict为API的负载（payload）定义一个明确的数据结构。
class VisionCompletionPayload(TypedDict):
    model: str
    messages: List[Dict[str, Any]]
    # 增加 max_tokens 以处理长文档
    max_tokens: int

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


# <<< 重构核心API调用函数以支持选择性预处理 >>>
def _call_qwen_api(image_paths: List[Path], prompt: str, use_preprocessing: bool = False) -> Union[str, None]:
    """
    通用的Qwen-VL API调用函数，增加了图像预处理的开关。

    Args:
        image_paths (List[Path]): 待处理的图片路径列表。
        prompt (str): 发送给模型的指令。
        use_preprocessing (bool): 是否激活图像预处理。为True时，图片会先被增强再发送。
                                  默认为False。
    """
    if not qwen_client:
        logger.error("Qwen-VL客户端未初始化，API调用中止。")
        return None

    # 构建API请求体中的'content'部分，这是一个包含文本和多张图片的列表。
    content_payload = [{"type": "text", "text": prompt}]
    for image_path in image_paths:
        base64_image = None
        # 根据开关决定处理路径
        if use_preprocessing:
            logger.info(f"正在对图片应用OCR预处理: {image_path.name}")
            processed_image_bytes = preprocess_image_for_ocr(image_path)
            if processed_image_bytes:
                # 对处理后的二进制数据进行Base64编码
                base64_image = base64.b64encode(processed_image_bytes).decode('utf-8')
        else:
            # 保持原有逻辑，直接对原图进行编码
            base64_image = encode_image_to_base64(image_path)

        if base64_image:
            # 注意：预处理后我们保存为PNG，因此MIME类型统一为 image/png 以保证兼容性
            content_payload.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{base64_image}"}
            })
        else:
            logger.warning(f"跳过无法处理的图片: {image_path}")

    if len(content_payload) <= 1:
        logger.error("没有有效的图片可发送至Qwen-VL。")
        return None

    payload: VisionCompletionPayload = {
        "model": config.QWEN_MODEL_NAME,
        "messages": [{"role": "user", "content": content_payload}],
        "max_tokens": 8192 # 增加token上限以应对复杂的结构化文档
    }

    try:
        completion = qwen_client.chat.completions.create(**payload)
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Qwen-VL API 请求失败: {e}")
        return None


def classify_problem_type(image_paths: List[Path]) -> str:
    """
    步骤 1: 问题分类。
    该任务依赖宏观视觉信息，不应进行可能破坏整体布局的预处理。
    """
    logger.info("步骤 1: 正在进行问题类型分类...")
    response = _call_qwen_api(image_paths, config.CLASSIFICATION_PROMPT, use_preprocessing=False)

    # 对模型的返回结果进行严格校验，确保其在我们预设的分类中。
    valid_types = ["CODING", "VISUAL_REASONING", "QUESTION_ANSWERING", "GENERAL"]
    if response and response in valid_types:
        logger.info(f"分类成功，识别类型为: {response}")
        return response

    # 如果分类失败或返回意外结果，提供一个安全的默认值'GENERAL'，保证程序流程的健-壮性。
    logger.warning(f"分类失败或返回未知类型 ('{response}')。将默认视为 'GENERAL'。")
    return "GENERAL"


def transcribe_images(image_paths: List[Path]) -> Union[str, None]:
    """
    【并行分治 + 结构化识别】
    Stage 1: 使用线程池并行处理，独立、高精度地转录每一张图片。
    Stage 2: 以编程方式，确定性地合并转录后的文本。
    """
    logger.info("步骤 3.1: 启动“并行分治”+“结构化识别”合并流程...")

    individual_transcriptions = [None] * len(image_paths)

    # --- Stage 1: 使用线程池并行转录每一张图片 ---
    # 定义一个辅助函数，用于在线程中调用API
    def transcribe_single_image(index_path_tuple):
        index, image_path = index_path_tuple
        logger.info(f"  - 开始并行转录，图片 {index + 1}/{len(image_paths)}: {image_path.name}...")

        # 每次只调用一张图片，并禁用预处理
        text = _call_qwen_api([image_path], config.TRANSCRIPTION_PROMPT, use_preprocessing=False)

        if text:
            logger.info(f"  - 成功完成并行转录，图片 {index + 1}/{len(image_paths)}。")
        else:
            logger.error(f"  - 并行转录失败，图片 {index + 1}/{len(image_paths)}。")
        return index, text

    # 创建一个线程池来并行执行任务
    # max_workers可以根据您的网络和CPU情况调整，4-8通常是比较合适的值
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # 使用 executor.map 来保持原始顺序
        # enumerate(image_paths) 会生成 (0, path1), (1, path2), ...
        results = executor.map(transcribe_single_image, enumerate(image_paths))

        for index, text in results:
            if text is None:
                logger.error(f"由于图片 {index + 1} 转录失败，整个合并流程中止。")
                return None
            individual_transcriptions[index] = text

    # --- Stage 2: 确定性的程序化合并 ---
    logger.info("所有图片独立转录完成，开始进行程序化文本合并...")
    final_text = merge_transcribed_texts(individual_transcriptions)
    logger.info("文本合并成功！")

    return final_text


def solve_visual_problem(image_paths: List[Path], prompt_template: str) -> Union[str, None]:
    """
    步骤 3.2: 视觉推理求解。
    该任务需要原始的图形、颜色和纹理信息，**严禁进行预处理**。
    """
    logger.info("步骤 3.2: 正在直接进行视觉推理求解...")
    return _call_qwen_api(image_paths, prompt_template, use_preprocessing=False)