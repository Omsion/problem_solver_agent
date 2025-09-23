# -*- coding: utf-8 -*-
"""
solver_client.py - 统一求解器客户端

本模块是实现多模型灵活切换的核心。它抽象了与不同LLM提供商
（如DeepSeek, DashScope, Zhipu AI）的交互细节，并向上层
（image_grouper.py）提供一个统一的、流式的调用接口。

核心设计思想：
1.  **配置驱动**: 完全由 config.py 中的 `SOLVER_PROVIDER` 决定使用哪个模型。
2.  **客户端缓存**: 为每个提供商创建一个单例的OpenAI客户端，避免重复初始化。
3.  **统一接口**: `stream_solve` 函数是唯一的入口，无论底层模型是什么。
4.  **智能适配**: 内部处理不同模型的特殊API参数（如GLM-4.5的'thinking'过程）。
"""
from openai import OpenAI
import config
from utils import setup_logger

logger = setup_logger()

# --- 客户端单例缓存 ---
# 使用一个字典来存储已初始化的客户端实例，避免重复创建连接。
_clients = {}


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

    api_key = None
    if provider == 'deepseek':
        api_key = config.DEEPSEEK_API_KEY
    elif provider == 'dashscope':
        api_key = config.DASHSCOPE_API_KEY
    elif provider == 'zhipu':
        api_key = config.ZHIPU_API_KEY

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


def stream_solve(final_prompt: str):
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
        messages = [{"role": "user", "content": final_prompt}]

        # --- 模型专属参数处理 ---
        # 针对需要特殊参数的模型（如智谱的 thinking 功能），在此处进行适配
        if provider == 'zhipu' and model == 'glm-4.5-pro':
            logger.info("检测到 GLM-4.5-Pro 模型，启用 'thinking' 过程流式输出。")
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                # extra_body 是向请求体添加额外参数的标准方式
                extra_body={"tools": [{"type": "web_search", "web_search": {"enable": True}}]}
            )
            # 智谱的流式输出包含思考过程和最终内容，需要分别处理
            is_answering = False
            yield "\n" + "=" * 20 + " 思考过程 " + "=" * 20 + "\n\n"
            for chunk in completion:
                delta = chunk.choices[0].delta
                if not delta.content:
                    continue
                # 简单地将所有内容块都输出
                yield delta.content

        else:
            # 适用于 DeepSeek, Qwen 等标准OpenAI流式接口的模型
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=8000,
                temperature=0.7,
            )
            for chunk in completion:
                content = chunk.choices[0].delta.content
                if content:
                    yield content

    except Exception as e:
        error_message = f"调用模型 '{model}' 时发生严重错误: {e}"
        logger.error(error_message, exc_info=True)
        # 将错误信息也通过流式接口返回，以便上层可以捕获并记录
        yield f"\n\n--- ERROR ---\n{error_message}\n--- END ERROR ---\n"