# -*- coding: utf-8 -*-
"""
solver_client.py - 统一求解器客户端 (V2.4 - 最终版)

本模块是实现多模型灵活切换的核心。它抽象了与不同LLM提供商
（如DeepSeek, DashScope, Zhipu AI）的交互细节，并向上层
（image_grouper.py）提供统一的调用接口。

V2.4 版本更新:
- 新增非流式 `ask_for_analysis` 函数，用于辅助任务。
- `stream_solve` 中移除了 Zhipu GLM-4.5 模型的“思考过程”输出。
- 增加了统一的 `check_solver_health` 函数。
"""
from openai import OpenAI
from typing import Generator, Dict, Any, List, Union
from typing_extensions import TypedDict
import config
from utils import setup_logger

logger = setup_logger()

# --- 客户端单例缓存 ---
_clients: Dict[str, OpenAI] = {}


# --- API Payload 类型定义 ---
class StandardChatPayload(TypedDict):
    """用于 DeepSeek 等标准 OpenAI 兼容接口的请求体结构。"""
    model: str
    messages: List[Dict[str, Any]]
    stream: bool
    max_tokens: int
    temperature: float


class ZhipuChatPayload(TypedDict):
    """用于智谱 GLM-4.5 模型的请求体结构。"""
    model: str
    messages: List[Dict[str, Any]]
    stream: bool
    extra_body: Dict[str, Any]


class DashScopeChatPayload(TypedDict):
    """用于 DashScope Qwen3 系列模型的请求体结构。"""
    model: str
    messages: List[Dict[str, Any]]
    stream: bool
    extra_body: Dict[str, Any]


def get_client(provider: str) -> OpenAI:
    """
    根据提供商名称，获取或创建一个缓存的OpenAI兼容客户端实例。
    """
    if provider in _clients: return _clients[provider]
    logger.info(f"正在为提供商 '{provider}' 初始化API客户端...")
    provider_config = config.SOLVER_CONFIG.get(provider)
    if not provider_config: raise ValueError(f"未在 config.py 中找到提供商 '{provider}' 的配置。")
    api_key_map = {
        'deepseek': config.DEEPSEEK_API_KEY,
        'dashscope': config.DASHSCOPE_API_KEY,
        'zhipu': config.ZHIPU_API_KEY
    }
    api_key = api_key_map.get(provider)
    if not api_key: raise ValueError(f"未能获取提供商 '{provider}' 的API密钥，请检查 .env 文件。")
    client = OpenAI(
        api_key=api_key,
        base_url=provider_config["base_url"],
        timeout=config.API_TIMEOUT)
    _clients[provider] = client
    logger.info(f"客户端 '{provider}' 初始化成功。")
    return client


def _prepare_messages(prompt_string: str, format_dict: Dict = None) -> List[Dict[str, Any]]:
    """
    一个辅助函数，用于从扁平化的提示词字符串准备 messages 列表。
    """
    if format_dict is None:
        format_dict = {}

    # 使用传入的字典格式化用户提示字符串
    formatted_prompt = prompt_string.format(**format_dict)

    # 直接返回只包含 user 角色的消息列表
    return [
        {"role": "user", "content": formatted_prompt}
    ]


def stream_solve(prompt_template_string: str, transcribed_text: str) -> Generator[str, None, None]:
    """
    根据全局配置，流式调用指定的LLM进行问题求解。
    """
    provider = config.SOLVER_PROVIDER
    model = config.SOLVER_MODEL_NAME
    logger.info(f"Step 2.2: 使用模型 '{model}' (提供商: {provider}) 进行流式求解...")
    try:
        client = get_client(provider)
        # 使用辅助函数准备 messages
        messages = _prepare_messages(prompt_template_string, {"transcribed_text": transcribed_text})

        if provider == 'zhipu' and model == 'glm-4.5':
            logger.info("检测到 GLM-4.5 模型，启用深度思考模式 (仅输出最终结果)。")
            payload: ZhipuChatPayload = {
                "model": model, "messages": messages, "stream": True,
                "extra_body": {"thinking": {"type": "enabled"}}
            }
            completion = client.chat.completions.create(**payload)  # type: ignore
            for chunk in completion:
                delta = chunk.choices[0].delta
                if hasattr(delta, "content") and delta.content:
                    yield delta.content

        elif provider == 'dashscope' and 'qwen' in model:
            logger.info(f"检测到 DashScope Qwen 系列模型 ({model})，启用思考模式并设置 result_format。")
            payload: DashScopeChatPayload = {
                "model": model, "messages": messages, "stream": True,
                "extra_body": {"enable_thinking": True, "result_format": "message"}
            }
            completion = client.chat.completions.create(**payload)  # type: ignore
            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        else:
            # 适用于 DeepSeek 和其他标准 OpenAI 接口的模型
            logger.info(f"使用标准模式调用模型: {model}")
            payload: StandardChatPayload = {
                "model": model, "messages": messages, "stream": True,
                "max_tokens": 8000, "temperature": 0.7
            }
            completion = client.chat.completions.create(**payload)  # type: ignore
            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
    except Exception as e:
        error_message = f"调用模型 '{model}' 时发生严重错误: {e}"
        logger.error(error_message, exc_info=True)
        yield f"\n\n--- ERROR ---\n{error_message}\n--- END ERROR ---\n"


#  用于辅助任务的非流式调用函数
def ask_for_analysis(prompt_template_string: str, provider: str, model: str, format_dict: Dict = None) -> Union[str, None]:
    logger.info(f"正在使用辅助模型 '{model}' (提供商: {provider}) 进行非流式分析...")
    try:
        client = get_client(provider)
        # 使用辅助函数准备 messages
        messages = _prepare_messages(prompt_template_string, format_dict)

        response = client.chat.completions.create(
            model=model, messages=messages, stream=False,
            temperature=0.2, timeout=120.0
        )  # type: ignore

        if response and response.choices and response.choices[0].message.content:
            return response.choices[0].message.content.strip()
        else:
            return None
    except Exception as e:
        logger.error(f"调用辅助模型 '{model}' 进行分析时发生错误: {e}", exc_info=True)
        return None


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
            max_tokens=5, stream=False, timeout=20.0
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