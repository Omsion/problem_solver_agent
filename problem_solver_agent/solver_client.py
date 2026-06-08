"""
solver_client.py - 统一求解器客户端 (V2.6 - Dict 事件流版)

本模块是实现多模型灵活切换的核心。

V2.6 版本更新:
- 【接口变更】: `stream_solve()` 返回类型从 `Generator[str]` 改为 `Generator[dict]`，
  每个事件为 `{"type": "reasoning"/"content", "content": "..."}` 结构。
- 【思考捕获】: 新增对 DeepSeek `reasoning_content` 的捕获，思考过程以 `type: "reasoning"` 事件独立产出。
- 【向后兼容】: 新增 `stream_solve_text_only()` 包装器，供 CLI Agent 继续使用纯文本流。

V2.5 版本更新:
- 【核心增强】: 为所有外部API调用函数 (`stream_solve`, `ask_for_analysis`)
  都内置了强大的自动重试机制。
- 【错误处理】: 能够智能区分可重试的网络错误 (如 APIConnectionError, APITimeoutError)
  和不可重试的严重错误，提高了程序的整体稳定性。
- 【可配置性】: 重试次数和延迟时间由 config.py 中的 MAX_RETRIES 和 RETRY_DELAY 控制。
- 【日志改进】: 在重试过程中会输出清晰的警告日志，便于追踪网络问题。
"""
import time
from collections.abc import Generator
from typing import Any, TypedDict

from openai import APIConnectionError, APITimeoutError, OpenAI

from . import config
from .utils import setup_logger

logger = setup_logger()

# --- 客户端单例缓存 ---
_clients: dict[str, OpenAI] = {}


# --- API Payload 类型定义 ---
class StandardChatPayload(TypedDict):
    model: str
    messages: list[dict[str, Any]]
    stream: bool
    max_tokens: int
    temperature: float


class DeepSeekChatPayload(TypedDict):
    model: str
    messages: list[dict[str, Any]]
    stream: bool
    max_tokens: int
    extra_body: dict[str, Any]
    reasoning_effort: str


def _get_api_key(provider: str) -> str | None:
    """根据 provider 名称从 config 中查找对应的 API 密钥。"""
    attr_name = f"{provider.upper()}_API_KEY"
    return getattr(config, attr_name, None)


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
    api_key = _get_api_key(provider)
    if not api_key:
        raise ValueError(f"未能获取提供商 '{provider}' 的API密钥，请检查 .env 文件。")
    client = OpenAI(api_key=api_key, base_url=provider_config["base_url"], timeout=config.API_TIMEOUT)
    _clients[provider] = client
    logger.info(f"客户端 '{provider}' 初始化成功。")
    return client


def stream_solve(final_prompt: str, provider: str, model: str, enable_thinking: bool = True) -> Generator[dict[str, str], None, None]:
    """
    流式调用指定的LLM进行问题求解，内置自动重试逻辑。

    Yields dict events with structure:
        {"type": "reasoning", "content": "..."}  — DeepSeek 思考过程
        {"type": "content", "content": "..."}    — 最终解答文本

    Args:
        enable_thinking: 是否启用思考模式（仅 DeepSeek）。开启后 reasoning_content 将被捕获为 reasoning 事件。
    """
    logger.info(f"Step 2.2: 使用动态选择的模型 '{model}' (提供商: {provider}) 进行流式求解...")

    for attempt in range(config.MAX_RETRIES + 1):
        try:
            client = get_client(provider)
            messages: list[dict[str, Any]] = [{"role": "user", "content": final_prompt}]
            payload: StandardChatPayload | DeepSeekChatPayload

            if provider == 'deepseek':
                if enable_thinking:
                    # DeepSeek 思考模式
                    payload = {"model": model, "messages": messages, "stream": True, "extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "high", "max_tokens": 8000}
                else:
                    payload = {"model": model, "messages": messages, "stream": True, "max_tokens": 8000, "temperature": 0.7}
            else:
                payload = {"model": model, "messages": messages, "stream": True,
                           "max_tokens": 8000, "temperature": 0.7}

            completion = client.chat.completions.create(**payload)  # type: ignore

            # 返回生成器：同时捕获 reasoning_content（思考过程）和 content（最终解答）
            def stream_generator() -> Generator[dict[str, str], None, None]:
                for chunk in completion:
                    delta = chunk.choices[0].delta
                    # 捕获 DeepSeek 思考模式下的推理内容
                    reasoning = getattr(delta, 'reasoning_content', None)
                    if reasoning:
                        yield {"type": "reasoning", "content": reasoning}
                    if delta.content:
                        yield {"type": "content", "content": delta.content}

            return stream_generator()

        except (APIConnectionError, APITimeoutError) as e:
            log_message = f"流式调用模型 '{model}' 时发生网络错误 (尝试 {attempt + 1}/{config.MAX_RETRIES + 1}): {e}"
            if attempt < config.MAX_RETRIES:
                logger.warning(log_message)
                logger.info(f"将在 {config.RETRY_DELAY} 秒后重试...")
                time.sleep(config.RETRY_DELAY)
            else:
                logger.error(f"达到最大重试次数，流式调用 '{model}' 最终失败。")
                break
        except Exception as e:
            logger.error(f"流式调用模型 '{model}' 时发生未知的严重错误: {e}", exc_info=True)
            break

    # 所有重试失败后的最终返回
    def error_generator() -> Generator[dict[str, str], None, None]:
        yield {"type": "error", "content": f"\n\n--- ERROR in solver_client: All retries failed for model {model}. ---\n"}

    return error_generator()


def stream_solve_text_only(final_prompt: str, provider: str, model: str, enable_thinking: bool = True) -> Generator[str, None, None]:
    """
    stream_solve() 的向后兼容包装器：仅 yield 纯文本内容，丢弃 reasoning 事件。
    供 CLI Agent（image_grouper.py）使用，因为它只需要将文本 join 后写入文件。

    Args:
        enable_thinking: 是否启用思考模式。即使开启，reasoning 内容也会被过滤掉。
    """
    logger.info(f"text_only wrapper: 调用 stream_solve(provider='{provider}', model='{model}', thinking={enable_thinking})")
    for event in stream_solve(final_prompt, provider, model, enable_thinking):
        yield event["content"]


def ask_for_analysis(final_prompt: str, provider: str, model: str) -> str | None:
    """
    非流式调用LLM进行分析任务，内置自动重试逻辑。
    """
    logger.info(f"正在使用辅助模型 '{model}' (提供商: {provider}) 进行非流式分析...")

    for attempt in range(config.MAX_RETRIES + 1):
        try:
            client = get_client(provider)
            messages: list[dict[str, Any]] = [{"role": "user", "content": final_prompt}]

            response = client.chat.completions.create(
                model=model, messages=messages, stream=False, temperature=0.7, timeout=120.0
            )  # type: ignore

            # 如果成功，返回结果并退出重试循环
            if response and response.choices and response.choices[0].message.content:
                return response.choices[0].message.content.strip()
            else:
                logger.warning(f"模型 '{model}' 返回了空内容。")
                return None  # 即使是空内容也算成功，不再重试

        except (APIConnectionError, APITimeoutError) as e:
            log_message = f"分析任务调用 '{model}' 时发生网络错误 (尝试 {attempt + 1}/{config.MAX_RETRIES + 1}): {e}"
            if attempt < config.MAX_RETRIES:
                logger.warning(log_message)
                logger.info(f"将在 {config.RETRY_DELAY} 秒后重试...")
                time.sleep(config.RETRY_DELAY)
            else:
                logger.error(f"达到最大重试次数，分析任务调用 '{model}' 最终失败。")
                return None
        except Exception as e:
            logger.error(f"分析任务调用 '{model}' 时发生未知的严重错误: {e}", exc_info=True)
            return None
    return None


def check_solver_health(provider: str, model: str) -> bool:
    """
    对指定的求解器进行一次快速的健康检查（此处为简洁，暂不添加重试）。
    """
    logger.info(f"正在对求解器 '{provider}' ({model}) 进行健康检查...")
    try:
        client = get_client(provider)
        messages_for_check = [{"role": "user", "content": "Say 'OK' if you are working."}]
        test_response = client.chat.completions.create(
            model=model, messages=messages_for_check, max_tokens=10, stream=False, timeout=20.0
        )
        if test_response and test_response.choices:
            content = test_response.choices[0].message.content
            logger.info(f"健康检查成功，收到回复: {content.strip() if content else 'OK (空回复)'}")
            return True
        else:
            logger.warning("健康检查失败: API响应结构异常。")
            return False
    except Exception as e:
        logger.error(f"健康检查失败: 调用API时发生异常: {e}")
        return False
