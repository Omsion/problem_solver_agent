# -*- coding: utf-8 -*-
"""
solver_client.py - 统一求解器客户端

官方文档地址：
https://api-docs.deepseek.com/zh-cn/
https://help.aliyun.com/zh/model-studio/use-qwen-by-calling-api?spm=a2c4g.11186623.0.0.bdd47d9djnrCpq#f4514ce9072sb
https://docs.bigmodel.cn/cn/guide/models/text/glm-4.5#python

本模块是实现多模型灵活切换的核心。它抽象了与不同LLM提供商
（如DeepSeek, DashScope, Zhipu AI）的交互细节，并向上层
（image_grouper.py）提供一个统一的、流式的调用接口。

V2.2 版本更新：
- 根据最新的官方API文档，精确适配了 Zhipu GLM-4.5 和 DashScope Qwen3
  模型的“深度思考”模式参数。
- 增强了 TypedDict 定义，使其与各模型的特殊参数（如 `thinking` 和
  `enable_thinking`）完全对齐。
- 优化了 Zhipu 模型的流式处理逻辑，可以分别捕获并格式化输出
  "思考过程 (reasoning_content)" 和最终内容。
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
# 为不同模型的 API 请求体定义明确的、类型安全的数据结构。

class StandardChatPayload(TypedDict):
    """用于 DeepSeek 等标准 OpenAI 兼容接口的请求体结构。"""
    model: str
    messages: List[Dict[str, Any]]
    stream: bool
    max_tokens: int
    temperature: float


class ZhipuChatPayload(TypedDict):
    """
    用于智谱 GLM-4.5 模型的请求体结构。
    根据官方文档，通过 extra_body 传递 thinking 参数来启用深度思考。
    """
    model: str
    messages: List[Dict[str, Any]]
    stream: bool
    extra_body: Dict[str, Any]


class DashScopeChatPayload(TypedDict):
    """
    用于 DashScope Qwen3 系列模型的请求体结构。
    根据官方文档，通过 extra_body 传递 enable_thinking 参数。
    """
    model: str
    messages: List[Dict[str, Any]]
    stream: bool
    extra_body: Dict[str, Any]


def get_client(provider: str) -> OpenAI:
    """
    根据提供商名称，获取或创建一个缓存的OpenAI兼容客户端实例。
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
    """
    provider = config.SOLVER_PROVIDER
    model = config.SOLVER_MODEL_NAME

    logger.info(f"Step 4: 使用模型 '{model}' (提供商: {provider}) 进行流式求解...")

    try:
        client = get_client(provider)
        messages: List[Dict[str, Any]] = [{"role": "user", "content": final_prompt}]

        # --- 模型专属参数处理 ---
        if provider == 'zhipu' and model == 'glm-4.5':
            logger.info("检测到 GLM-4.5 模型，启用深度思考模式。")
            payload: ZhipuChatPayload = {
                "model": model,
                "messages": messages,
                "stream": True,
                # 根据智谱官方文档，启用深度思考模式
                "extra_body": {
                    "thinking": {"type": "enabled"}
                }
            }
            completion = client.chat.completions.create(**payload)  # type: ignore

            # 特别处理 Zhipu 的流式输出，它包含 'reasoning_content' 和 'content'
            is_answering = False
            yield "\n" + "=" * 20 + " 思考过程 " + "=" * 20 + "\n"
            for chunk in completion:
                delta = chunk.choices[0].delta
                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    yield delta.reasoning_content

                if hasattr(delta, "content") and delta.content:
                    if not is_answering:
                        yield "\n\n" + "=" * 20 + " 完整回复 " + "=" * 20 + "\n\n"
                        is_answering = True
                    yield delta.content

        elif provider == 'dashscope' and 'qwen3' in model:
            logger.info(f"检测到 DashScope Qwen3 系列模型 ({model})，启用思考模式。")
            payload: DashScopeChatPayload = {
                "model": model,
                "messages": messages,
                "stream": True,
                # 根据通义千问官方文档，启用思考模式
                "extra_body": {"enable_thinking": True}
            }
            completion = client.chat.completions.create(**payload)  # type: ignore
            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        else:
            # 适用于 DeepSeek 和其他标准 OpenAI 接口的模型
            logger.info(f"使用标准模式调用模型: {model}")
            payload: StandardChatPayload = {
                "model": model,
                "messages": messages,
                "stream": True,
                "max_tokens": 8000,
                "temperature": 0.7,
            }
            completion = client.chat.completions.create(**payload)  # type: ignore
            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

    except Exception as e:
        error_message = f"调用模型 '{model}' 时发生严重错误: {e}"
        logger.error(error_message, exc_info=True)
        yield f"\n\n--- ERROR ---\n{error_message}\n--- END ERROR ---\n"


# 统一的、动态的健康检查函数
def check_solver_health() -> bool:
    """
    对当前在 config.py 中配置的核心求解器进行一次快速的健康检查。
    """
    provider = config.SOLVER_PROVIDER
    model = config.SOLVER_MODEL_NAME

    logger.info(f"正在对当前求解器 '{provider}' ({model}) 进行健康检查...")

    try:
        client = get_client(provider)
        test_response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "你好，请回复'OK'"}],
            max_tokens=5,
            temperature=0.1,
            stream=False,  # 健康检查使用非流式，快速获取结果
            timeout=20.0  # 为健康检查设置一个较短的超时
        )  # type: ignore

        if test_response and test_response.choices and test_response.choices[0].message.content:
            logger.info(f"健康检查成功，收到回复: {test_response.choices[0].message.content.strip()}")
            return True
        else:
            logger.warning("健康检查失败: API响应结构异常。")
            return False
    except Exception as e:
        logger.error(f"健康检查失败: 调用API时发生异常: {e}")
        return False