"""
solver_client.py - 统一求解器客户端 (V2.6 - Dict 事件流版)

本模块是实现多模型灵活切换的核心。

V2.7 版本更新:
- 【健壮性】: 思考模式下若 `reasoning_content` 占满 `max_tokens`、导致正文一个字
  都没产出（`finish_reason=length`），自动关闭思考模式重试一次，而不是让整个任务
  以"求解器返回空响应"失败。
- 【可诊断】: 失败时错误事件里带上模型名、`finish_reason`、思考/正文字符数与真实的
  API 异常文本，不再只给一句"空响应"。

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


def _build_payload(
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    enable_thinking: bool,
    *,
    max_tokens: int | None = None,
) -> "StandardChatPayload | DeepSeekChatPayload":
    """按 provider 组装一次请求体（思考模式目前只有 DeepSeek 支持）。

    注意：关闭思考必须**显式**下发 `thinking.type=disabled`。实测只把 `extra_body`
    整个省略时（旧行为）模型照样思考——思考过程把 max_tokens 吃光、正文一个字都
    没有，所以"关闭思考模式重解"其实一直没生效。

    Args:
        max_tokens: 本次调用的输出上限；缺省用 `config.SOLVER_MAX_TOKENS`
            （升级档由流水线显式传入 `config.SOLVER_ESCALATE_MAX_TOKENS`）。
    """
    budget = max_tokens if max_tokens is not None else config.SOLVER_MAX_TOKENS
    if provider == 'deepseek':
        if enable_thinking:
            # 思考深度可配置：low 更省 token，high 想得更久（更容易吃满配额）
            return {"model": model, "messages": messages, "stream": True, "extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": config.SOLVER_REASONING_EFFORT, "max_tokens": budget}
        return {"model": model, "messages": messages, "stream": True, "extra_body": {"thinking": {"type": "disabled"}}, "max_tokens": budget, "temperature": 0.7}
    return {"model": model, "messages": messages, "stream": True,
            "max_tokens": budget, "temperature": 0.7}


def _pump(
    completion,
    *,
    reasoning_char_limit: int = 0,
) -> Generator[dict[str, str], None, dict[str, Any]]:
    """把 chunk 流翻译成事件流，并把统计信息 return 给调用方。

    Args:
        reasoning_char_limit: 思考过程超过该字符数且正文仍为 0 时提前中断本次流
            （0 = 不限制）。用于避免"大配额下思考空转好几分钟"。

    Yields:
        {"type": "reasoning"/"content", "content": "..."}

    Returns:
        {"content_chars": int, "reasoning_chars": int, "finish_reason": str | None,
         "aborted": bool} —— 这是判断"为什么没有正文"的唯一依据，必须回传给调用方。
    """
    content_chars = 0
    reasoning_chars = 0
    finish_reason: str | None = None
    aborted = False

    for chunk in completion:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        choice = choices[0]
        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        # 捕获 DeepSeek 思考模式下的推理内容
        reasoning = getattr(delta, 'reasoning_content', None)
        if reasoning:
            reasoning_chars += len(reasoning)
            yield {"type": "reasoning", "content": reasoning}
            if (
                reasoning_char_limit
                and content_chars == 0
                and reasoning_chars > reasoning_char_limit
            ):
                aborted = True
                break
        content = getattr(delta, "content", None)
        if content:
            content_chars += len(content)
            yield {"type": "content", "content": content}

    return {
        "content_chars": content_chars,
        "reasoning_chars": reasoning_chars,
        "finish_reason": finish_reason,
        "aborted": aborted,
    }


def _close_stream(completion) -> None:
    """尽力关闭底层流（提前中断后不再继续计费）。失败不影响主流程。"""
    close = getattr(completion, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # pragma: no cover - 关闭失败无所谓
            pass


def _meta_event(stats: dict[str, Any], *, model: str, thinking: bool) -> dict[str, Any]:
    """给流水线用的"本次求解画像"事件（不写入解答文件）。

    流水线据此判断答案是否合格（是否被截断、是否过短），决定要不要升级到思考档。
    """
    return {
        "type": "meta",
        "model": model,
        "thinking": thinking,
        "finish_reason": stats.get("finish_reason"),
        "reasoning_chars": int(stats.get("reasoning_chars", 0)),
        "content_chars": int(stats.get("content_chars", 0)),
        "truncated": stats.get("finish_reason") == "length",
        "aborted": bool(stats.get("aborted")),
    }


def _error_events(message: str) -> Generator[dict[str, str], None, None]:
    """只产出一条错误事件（求解彻底失败时的最终返回）。"""
    yield {"type": "error", "content": message}


def stream_solve(
    final_prompt: str,
    provider: str,
    model: str,
    enable_thinking: bool = True,
    *,
    max_tokens: int | None = None,
    reasoning_char_limit: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    流式调用指定的LLM进行问题求解，内置自动重试逻辑。

    Yields dict events with structure:
        {"type": "reasoning", "content": "..."}  — DeepSeek 思考过程
        {"type": "content", "content": "..."}    — 最终解答文本
        {"type": "meta", ...}                    — 本次求解画像（finish_reason 等），
                                                   流水线据此判断答案是否合格
        {"type": "error", "content": "..."}      — 失败原因（含可诊断信息）

    Args:
        enable_thinking: 是否启用思考模式（仅 DeepSeek）。开启后 reasoning_content 将被捕获为
            reasoning 事件；若思考过程占满 max_tokens 导致正文为空，会自动关闭思考模式重试一次。
        max_tokens: 本次调用的输出上限，缺省用 `config.SOLVER_MAX_TOKENS`
        reasoning_char_limit: 思考提前放弃阈值，缺省用 `config.SOLVER_REASONING_CHAR_LIMIT`
    """
    logger.info(f"Step 2.2: 使用动态选择的模型 '{model}' (提供商: {provider}) 进行流式求解...")
    budget = max_tokens if max_tokens is not None else config.SOLVER_MAX_TOKENS
    char_limit = (
        reasoning_char_limit
        if reasoning_char_limit is not None
        else config.SOLVER_REASONING_CHAR_LIMIT
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": final_prompt}]
    client: OpenAI | None = None
    completion = None
    failure = ""

    for attempt in range(config.MAX_RETRIES + 1):
        try:
            client = get_client(provider)
            completion = client.chat.completions.create(  # type: ignore[arg-type]
                **_build_payload(provider, model, messages, enable_thinking, max_tokens=budget)
            )
            break

        except (APIConnectionError, APITimeoutError) as e:
            failure = f"{type(e).__name__}: {e}"
            log_message = f"流式调用模型 '{model}' 时发生网络错误 (尝试 {attempt + 1}/{config.MAX_RETRIES + 1}): {e}"
            if attempt < config.MAX_RETRIES:
                logger.warning(log_message)
                logger.info(f"将在 {config.RETRY_DELAY} 秒后重试...")
                time.sleep(config.RETRY_DELAY)
            else:
                logger.error(f"达到最大重试次数，流式调用 '{model}' 最终失败。")
        except Exception as e:
            failure = f"{type(e).__name__}: {e}"
            logger.error(f"流式调用模型 '{model}' 时发生未知的严重错误: {e}", exc_info=True)
            break

    if completion is None or client is None:
        return _error_events(f"求解器调用失败（模型 {model}）：{failure or '未获得响应'}")

    def _log_profile(stats: dict[str, Any], *, thinking: bool) -> None:
        """每个求解档位打一条画像日志，便于事后对比/调参。"""
        logger.info(
            "求解画像: 模型=%s 思考=%s effort=%s max_tokens=%d 思考=%d 字符 正文=%d 字符 finish=%s%s",
            model,
            "开" if thinking else "关",
            config.SOLVER_REASONING_EFFORT if thinking else "-",
            budget,
            int(stats.get("reasoning_chars", 0)),
            int(stats.get("content_chars", 0)),
            stats.get("finish_reason"),
            "（思考过长提前放弃）" if stats.get("aborted") else "",
        )

    # 返回生成器：同时捕获 reasoning_content（思考过程）和 content（最终解答）
    def stream_generator() -> Generator[dict[str, Any], None, None]:
        try:
            stats: dict[str, Any] = yield from _pump(completion, reasoning_char_limit=char_limit)
        except Exception as e:
            logger.error(f"读取模型 '{model}' 的流式响应时出错: {e}", exc_info=True)
            yield {"type": "error", "content": f"求解器流式响应中断（模型 {model}）：{type(e).__name__}: {e}"}
            return

        _log_profile(stats, thinking=enable_thinking)

        if stats["content_chars"]:
            yield _meta_event(stats, model=model, thinking=enable_thinking)
            return

        detail = (
            f"模型={model}, finish_reason={stats['finish_reason']}, "
            f"思考过程={stats['reasoning_chars']} 字符, 正文=0 字符"
        )
        yield _meta_event(stats, model=model, thinking=enable_thinking)

        if not enable_thinking:
            yield {"type": "error", "content": f"求解器没有返回正文（{detail}）"}
            return

        if stats.get("aborted"):
            # 提前放弃：思考已远超阈值仍无正文，继续等下去只是白烧配额
            _close_stream(completion)
            logger.warning(
                "模型 '%s' 思考已达 %d 字符仍无正文，提前放弃思考模式（%s）。",
                model,
                int(stats["reasoning_chars"]),
                detail,
            )
            notice = "\n\n[思考过程过长且迟迟不产出正文，已提前改用关闭思考模式重新生成解答…]\n\n"
        else:
            # 思考模式最常见的失败模式：思考过程吃满 max_tokens，配额没留给正文
            # （DeepSeek 在思考未结束时直接以 finish_reason=length 收尾，正文一个字都没有）。
            logger.warning("模型 '%s' 只产出思考过程没有正文（%s），改用关闭思考模式重试一次。", model, detail)
            notice = "\n\n[思考过程占满了输出配额，已自动关闭思考模式重新生成解答…]\n\n"
        yield {"type": "reasoning", "content": notice}

        try:
            retry_completion = client.chat.completions.create(  # type: ignore[arg-type]
                **_build_payload(provider, model, messages, False, max_tokens=budget)
            )
            retry_stats: dict[str, Any] = yield from _pump(retry_completion)
        except Exception as e:
            logger.error(f"关闭思考模式重试 '{model}' 失败: {e}", exc_info=True)
            yield {"type": "error", "content": (
                f"求解器没有返回正文（{detail}），关闭思考模式重试亦失败：{type(e).__name__}: {e}"
            )}
            return

        _log_profile(retry_stats, thinking=False)
        yield _meta_event(retry_stats, model=model, thinking=False)

        if not retry_stats["content_chars"]:
            yield {"type": "error", "content": (
                f"求解器没有返回正文（{detail}）；关闭思考模式重试后仍为空"
                f"（finish_reason={retry_stats['finish_reason']}）"
            )}

    return stream_generator()


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
