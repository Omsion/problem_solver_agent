# -*- coding: utf-8 -*-
"""
solver_client.py - 统一求解器客户端

核心设计思想：
1.  **配置驱动**: 完全由 config.py 中的 `SOLVER_PROVIDER` 决定使用哪个模型。
2.  **客户端缓存**: 为每个提供商创建一个单例的OpenAI客户端，避免重复初始化。
3.  **统一接口**: `stream_solve` 函数是唯一的入口，无论底层模型是什么。
4.  **智能适配与类型安全**: 内部构建特定类型的Payload，并使用有注释的
   `# type: ignore` 来处理第三方库的类型限制，实现代码的清晰与正确。
"""
from openai import OpenAI
from typing import Generator, Dict, Any, List
# 导入 TypedDict 以定义结构化的字典类型
from typing_extensions import TypedDict

import config
from utils import setup_logger

logger = setup_logger()

# --- 客户端单例缓存 ---
_clients: Dict[str, OpenAI] = {}


# --- API Payload 类型定义 ---
# 使用 TypedDict 为不同模型的 API 请求体定义明确的、类型安全的数据结构。
# 这样做可以让IDE和静态分析工具理解我们正在构建的数据，从而消除大部分类型警告。

class StandardChatPayload(TypedDict):
    """用于 DeepSeek, Qwen 等标准 OpenAI 兼容接口的请求体结构。"""
    model: str
    messages: List[Dict[str, Any]]
    stream: bool
    max_tokens: int
    temperature: float


class ZhipuChatPayload(TypedDict):
    """用于智谱 GLM-4.5-pro 模型的请求体结构，包含了非标准的 extra_body 参数。"""
    model: str
    messages: List[Dict[str, Any]]
    stream: bool
    extra_body: Dict[str, Any]


def get_client(provider: str) -> OpenAI:
    """
    根据提供商名称，获取或创建一个缓存的OpenAI兼容客户端实例。
    这是一个工厂函数，负责处理不同提供商的认证和端点配置。
    """
    if provider in _clients:
        return _clients[provider]

    logger.info(f"正在为提供商 '{provider}' 初始化API客户端...")

    provider_config = config.SOLVER_CONFIG.get(provider)
    if not provider_config:
        raise ValueError(f"未在 config.py 中找到提供商 '{provider}' 的配置。")

    api_key_map = {
        'deepseek': config.DEEPSEEK_API_KEY,
        'dashscope': config.DASHSCOPE_API_KEY,
        'zhipu': config.ZHIPU_API_KEY
    }
    api_key = api_key_map.get(provider)

    if not api_key:
        raise ValueError(f"未能获取提供商 '{provider}' 的API密钥，请检查 .env 文件。")

    client = OpenAI(
        api_key=api_key,
        base_url=provider_config["base_url"],
        timeout=config.API_TIMEOUT,
    )

    _clients[provider] = client
    logger.info(f"客户端 '{provider}' 初始化成功。")
    return client


def stream_solve(final_prompt: str) -> Generator[str, None, None]:
    """
    根据全局配置，流式调用指定的LLM进行问题求解。
    这是一个生成器函数，会实时地 `yield` 模型生成的文本块。

    Args:
        final_prompt (str): 发送给模型的最终提示词。

    Yields:
        str: 模型生成的文本块 (chunk)。
    """
    provider = config.SOLVER_PROVIDER
    model = config.SOLVER_MODEL_NAME

    logger.info(f"Step 4: 使用模型 '{model}' (提供商: {provider}) 进行流式求解...")

    try:
        client = get_client(provider)
        messages: List[Dict[str, Any]] = [{"role": "user", "content": final_prompt}]

        # --- 模型专属参数处理 ---
        if provider == 'zhipu' and model == 'glm-4.5-pro':
            logger.info("检测到 GLM-4.5-Pro 模型，启用 'web_search' 工具。")

            # 1. 构建一个符合 ZhipuChatPayload 类型的字典
            payload: ZhipuChatPayload = {
                "model": model,
                "messages": messages,
                "stream": True,
                "extra_body": {"tools": [{"type": "web_search", "web_search": {"enable": True}}]}
            }

            # 2. 使用 **payload 解包传入参数
            completion = client.chat.completions.create(**payload)  # type: ignore

            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        else:
            # 适用于 DeepSeek, Qwen 等标准OpenAI流式接口的模型

            # 1. 构建一个符合 StandardChatPayload 类型的字典
            payload: StandardChatPayload = {
                "model": model,
                "messages": messages,
                "stream": True,
                "max_tokens": 8000,
                "temperature": 0.7,
            }

            # 2. 使用 **payload 解包传入参数
            completion = client.chat.completions.create(**payload)  # type: ignore

            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

    except Exception as e:
        error_message = f"调用模型 '{model}' 时发生严重错误: {e}"
        logger.error(error_message, exc_info=True)
        # 将错误信息也通过流式接口返回，以便上层可以捕获并记录在最终文件中。
        yield f"\n\n--- ERROR ---\n{error_message}\n--- END ERROR ---\n"