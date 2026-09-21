"""
vision_client.py - 视觉 API 客户端 (Provider-Agnostic)

本模块封装了所有与多模态视觉模型（图像分类、OCR转录、视觉推理）的交互。
具体模型与端点由 `config.VISION_PROVIDER`（迁移目标 deepseek / 代码默认 zhipu）决定，
切换只需改一个环境变量，代码不用动。

本仓库 .env 当前显式启用 deepseek（VISION_PROVIDER=deepseek）:
- 分类: deepseek-flash (chat/completions)
- OCR:   deepseek-flash (chat/completions)
- 视觉推理: deepseek-flash (chat/completions)

回退（VISION_PROVIDER=zhipu，也是未配置时的代码默认）与迁移前逐字节一致:
- 分类: GLM-4.6V-FlashX / 视觉推理: GLM-4.6V

两个关键约定（迁移踩过的坑，详见 docs/plans/deepseek_vision_migration.md）：
1. **思考模式必须显式关闭**：DeepSeek「思考模式默认打开，且 effort 默认为 high」，
   分类/OCR 这类短任务一旦开思考，思考过程会把 max_tokens 吃光、正文为空。
   `_build_extra_body()` 负责下发 `thinking.type=disabled`（zhipu 不需要）。
2. **合并调用用分隔符协议而不是 JSON**：JSON 与 LaTeX 天然互斥，漏转义时
   `\\frac`→`\\f`、`\\begin`→`\\b`、`\\theta`→`\\t`、`\\neq`→`\\n` 都是**合法转义**，
   解析"成功"但正文被静默破坏。`<<<PAGE n|NEW/CONT>>>` 让正文逐字直出。
"""
import concurrent.futures
import json
import re
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, TypedDict

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from . import config, image_prep, prompts
from .utils import setup_logger

logger = setup_logger()

# 允许的题型标签（分类 prompt、合并调用、并行回退三处共用一份，避免漏改）
VALID_PROBLEM_TYPES = (
    "CODING", "ML_CODING", "VISUAL_REASONING", "QUESTION_ANSWERING",
    "GENERAL", "MULTIPLE_CHOICE", "FILL_IN_THE_BLANKS",
)

# 流式调用彻底失败时 `_call_vision_api` 会产出的兜底文本（用于识别"请求没发出去"）
_STREAM_ERROR_MARKER = "--- ERROR in vision_client:"


# --- 类型定义 ---
class VisionCompletionPayload(TypedDict, total=False):
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int
    stream: bool
    extra_body: dict[str, Any]
    timeout: float


# --- 客户端延迟初始化（按 provider 各建一个，避免切换时互相污染）---
_vision_clients: dict[str, OpenAI] = {}


def _get_vision_client(provider: str | None = None) -> OpenAI | None:
    """获取或延迟初始化视觉 API 客户端（按 provider 缓存）。

    Args:
        provider: "deepseek" / "zhipu"；None 表示用 `config.VISION_PROVIDER_NAME`。

    本模块内所有"运行时生效的 provider"判断统一走 `config.VISION_PROVIDER_NAME`
    （它与 `config.VISION_PROVIDER` 在生产上恒等，但混用会让"同进程只改其中一个"
    的场景得到半切换状态——密钥/端点用一家、请求体按另一家）。
    """
    name = (provider or config.VISION_PROVIDER_NAME).strip().lower()
    if name in _vision_clients:
        return _vision_clients[name]

    cfg = config.VISION_PROVIDER_CONFIG.get(name)
    if cfg is None:
        logger.critical(
            "未知的视觉层 provider '%s'（可选：%s），视觉客户端初始化失败。",
            name, ", ".join(config.VISION_PROVIDER_CONFIG),
        )
        return None

    api_key = config._vision_api_key(name)
    if not api_key and name == config.VISION_PROVIDER_NAME:
        # 允许宿主程序/测试在 env 之外注入默认 provider 的密钥
        api_key = config.VISION_API_KEY
    env_name = cfg["api_key_env"]
    if not api_key:
        logger.critical(
            "未在 .env 文件中找到 %s（当前视觉层 provider=%s），视觉客户端初始化失败。",
            env_name, name,
        )
        return None

    try:
        client = OpenAI(
            api_key=api_key,
            base_url=cfg["base_url"],
            # 显式设置超时：SDK 默认值偏大，分类/OCR 这类短任务一旦网络异常
            # 会长时间挂起，用户只看到"一直在处理"（合并调用另有更长的单请求超时）
            timeout=config.VISION_TIMEOUT,
            max_retries=0,  # 重试由下面的循环统一控制，避免双重重试放大等待
        )
        _vision_clients[name] = client
        logger.info("视觉客户端初始化成功（provider=%s，base_url=%s）。", name, cfg["base_url"])
    except Exception as e:
        logger.critical("初始化视觉客户端失败 (provider=%s, base_url=%s): %s", name, cfg["base_url"], e)
        return None
    return client


def _supports_thinking_control(provider: str | None = None) -> bool:
    """该 provider 是否支持显式关闭思考模式（目前只有 DeepSeek）。

    判定委托给 `config.provider_supports_thinking_control`（求解层也用同一份能力位），
    但 provider 的解析统一走 `VISION_PROVIDER_NAME`（见 `_get_vision_client` 的说明）。
    """
    name = provider or config.VISION_PROVIDER_NAME
    return config.provider_supports_thinking_control(name)


def _model_for(provider: str | None = None, kind: str = "classify") -> str:
    """按 provider 表取模型名（`kind`：`"classify"` / `"reasoning"`）。

    **为什么必须显式取**：`_get_vision_client(provider="zhipu")` 会把 base_url 与密钥
    换成智谱，而模型名此前一直读 `config.VISION_CLASSIFY_MODEL`（由**默认 provider**
    派生）—— 于是 `provider="zhipu"` 时会把 `deepseek-flash` 发到智谱端点（反向同理）。
    结果就是 A/B 工具的"另一家"那一腿要么 4xx、要么测的其实是同一个 provider，
    而 S1/S2 的判定正是建立在双 provider 对照上的。
    """
    name = (provider or config.VISION_PROVIDER_NAME).strip().lower()
    cfg = config.VISION_PROVIDER_CONFIG.get(name)
    if cfg:
        model = cfg.get(f"{kind}_model")
        if model:
            return model
    logger.critical("provider '%s' 缺少 %s_model 配置，回退到当前 provider 的模型名", name, kind)
    return config.VISION_REASONING_MODEL if kind == "reasoning" else config.VISION_CLASSIFY_MODEL


def _build_extra_body(provider: str | None = None) -> dict[str, Any]:
    """按 provider 组装 `extra_body`。

    **这是迁移最关键的一处**：DeepSeek 思考模式默认开启且 effort=high，而 OpenAI SDK
    不认顶层的 `thinking` 字段，必须放进 `extra_body`。solver_client 已经踩过这个坑
    （只省略 extra_body 时模型照样思考），vision_client 此前完全没有这段逻辑。
    """
    if _supports_thinking_control(provider) and config.VISION_DISABLE_THINKING:
        return {"thinking": {"type": "disabled"}}
    return {}


def _provider_sampling_params(provider: str | None = None) -> dict[str, Any]:
    """视觉推理的采样参数（按 provider 分支）。

    DeepSeek：非思考模式下 `temperature` / `top_p` 均**不生效**（top_p 恒为 1.0），
    传了只会造成"以为设了 0.7 其实没用"的错觉，因此直接不传。
    智谱：保持迁移前的 `top_p=0.8, temperature=0.7` 不变，保证回退路径逐字节一致。

    分支依据是 **provider 是否支持思考开关**，而不是"extra_body 是否为空"：后者在
    `VISION_DISABLE_THINKING=false`（DeepSeek 但故意开思考）时会误判成智谱，
    于是把两个静默失效的参数发出去 —— 恰好是这个函数想要避免的错觉。
    """
    if _supports_thinking_control(provider):
        body = _build_extra_body(provider)
        return {"extra_body": body} if body else {}
    return {"top_p": 0.8, "temperature": 0.7}


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
                     extra_params: dict | None = None,
                     provider: str | None = None,
                     timeout: float | None = None) -> str | Generator[str, None, None] | None:
    """
    核心视觉API调用函数，内置健壮的自动重试逻辑。

    当遇到可恢复的网络相关错误（连接错误、超时、429 限流、5xx）时，
    根据 config.py 中的配置自动重试；重试间隔按指数退避增长。

    Args:
        image_paths: 图片路径列表
        user_prompt: 用户提示词
        model_name: 模型名称 (来自 config 常量)
        stream: 是否流式返回
        extra_params: 额外的 API 参数 (如 top_p, temperature, extra_body)
        provider: 视觉层 provider；None 用 config.VISION_PROVIDER
        timeout: 单次请求超时（秒）；None 用 config.VISION_TIMEOUT。
            合并调用要输出 6–16K token，必须用 VISION_COMBINED_TIMEOUT。

    Returns:
        非流式: 文本响应字符串
        流式: 字符串生成器
        失败: None
    """
    client = _get_vision_client(provider)
    if not client:
        logger.error("视觉客户端未初始化，API调用中止。")
        if stream:
            # 流式调用必须返回**生成器**：返回 None 会让 `_collect_stream` 在
            # `for item in None` 上抛 TypeError，被当成"模型返回的错误"记进日志，
            # 掩盖真正的原因（密钥/端点没配好）。这里给出与"重试耗尽"一致的错误标记，
            # 调用方据此直接回退，不再白等一次 JSON 尝试。
            def _unavailable():
                yield f"\n\n{_STREAM_ERROR_MARKER} vision client unavailable. ---\n"

            return _unavailable()
        return None

    messages = [{"role": "user", "content": _build_user_content(image_paths, user_prompt)}]

    # 输出上限按 provider 取：DeepSeek 32768 vs GLM 8192（见 config._vision_max_tokens）
    max_tokens_used = config._vision_max_tokens(provider)
    payload: VisionCompletionPayload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens_used,
        "stream": stream,
        # 合并调用的输出量比逐页 OCR 大一个数量级，必须用独立超时
        "timeout": timeout or config.VISION_TIMEOUT,
    }
    # DeepSeek 思考模式默认开启且 effort=high：不显式关闭时，分类/OCR 这类短任务
    # 的思考过程会把 max_tokens 吃光、正文为空（与 solver 踩过的是同一个坑）。
    # 智谱不支持该开关，因此**不产生** extra_body 键（回退路径与迁移前逐字节一致）。
    thinking_body = _build_extra_body(provider)
    if thinking_body:
        payload["extra_body"] = thinking_body
    if extra_params:
        payload.update(extra_params)

    # 单请求图片数上限（DeepSeek 600 张）。项目分组窗口通常个位数，
    # 这里只做一次断言级日志，便于真出现异常配置时定位。
    if len(image_paths) > 100:
        logger.warning("单次视觉请求携带 %d 张图片，已接近厂商上限，请检查分组配置", len(image_paths))

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
                choice = completion.choices[0] if completion.choices else None
                # 截断是"合并调用解析失败"最常见的真因，但旧实现把它吞掉了：
                # 输出被 max_tokens 截断时 JSON 必然不完整，必须留下痕迹。
                if getattr(choice, "finish_reason", None) == "length":
                    logger.warning(
                        "视觉调用输出被 max_tokens=%d 截断（模型 %s）：内容可能不完整，"
                        "长转录/合并调用会因此解析失败",
                        max_tokens_used, model_name,
                    )
                content = choice.message.content if choice and choice.message else None
                return content.strip() if content else None

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
            yield f"\n\n{_STREAM_ERROR_MARKER} All retries failed for {model_name}. ---\n"

        return error_generator()
    return None


def _collect_stream(
    issue_request: Callable[[], Any],
    *,
    model_name: str,
    on_chunk: Callable[[str], None] | None = None,
    max_tokens: int | None = None,
) -> tuple[str, str | None]:
    """把流式响应拼回完整字符串，并返回 `finish_reason`。

    **为什么自带一层重试**：现有重试循环（`_call_vision_api`）只覆盖 `create()` 抛出的
    异常；流式下 `create()` 已经返回，错误发生在**迭代期间**。没有这一层，一次网络
    抖动就会丢掉整次转录（合并调用尤其致命——它承载全部分页）。重试时整次请求重发，
    已收集的半截内容作废。

    `issue_request()` 可以返回两类可迭代对象，这里都要支持：
    1. `client.chat.completions.create(stream=True)` 的原始 chunk 流（对象带 `.choices`）；
    2. `_call_vision_api(stream=True)` 的**纯文本**生成器（只 yield `delta.content` 字符串）。
       实际调用走的是第 2 类，早期只处理对象流会让它静默收集到空字符串。

    Args:
        issue_request: 无参可调用对象，每次调用发起一次新的流式请求。
        model_name: 用于日志。
        on_chunk: 可选的增量回调（便于一边收一边观察）。

    Returns:
        (完整文本, finish_reason)；彻底失败时返回 ("", None)。
    """
    last_error: Exception | None = None
    for attempt in range(config.MAX_RETRIES + 1):
        chunks: list[str] = []
        finish_reason: str | None = None
        try:
            completion = issue_request()
            for item in completion:
                if item is None:
                    continue
                if isinstance(item, str):  # 第 2 类：已经是文本
                    if item:
                        chunks.append(item)
                        if on_chunk is not None:
                            on_chunk(item)
                    continue
                choices = getattr(item, "choices", None)
                if not choices:
                    continue
                choice = choices[0]
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    chunks.append(content)
                    if on_chunk is not None:
                        on_chunk(content)
            text = "".join(chunks)
            if finish_reason == "length":
                # 注意：生产路径上这里几乎不会触发 —— `_call_vision_api(stream=True)` 只
                # yield 文本，不传 chunk 对象，因此 finish_reason 恒为 None。真正的截断
                # 信号是"响应里缺 `<<<END>>>`"（见 classify_and_transcribe 的告警）。
                # 保留这个分支是为了将来把 finish_reason 透出来时不必再改结构。
                logger.warning(
                    "流式视觉输出被 max_tokens=%d 截断（模型 %s）：转录可能不完整",
                    max_tokens if max_tokens is not None else config.VISION_MAX_TOKENS,
                    model_name,
                )
            return text, finish_reason
        except Exception as exc:
            last_error = exc
            retryable = _is_retryable(exc)
            if retryable and attempt < config.MAX_RETRIES:
                delay = config.RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    "流式读取模型 '%s' 中断（尝试 %d/%d）: %s，%d 秒后整次重发",
                    model_name, attempt + 1, config.MAX_RETRIES + 1, exc, delay,
                )
                time.sleep(delay)
                continue
            if retryable:
                logger.error("流式读取模型 '%s' 达到最大重试次数，放弃。", model_name)
            else:
                logger.error("流式读取模型 '%s' 时发生不可重试的错误: %s", model_name, exc, exc_info=True)
            break
    if last_error is not None:
        logger.error("流式收集最终失败（模型 %s）：%s", model_name, last_error)
    return "", None


# ==============================================================================
# 公共 API —— 切换模型只需修改 config.py，以下函数无需改动
# ==============================================================================

def classify_problem_type(image_paths: list[Path], provider: str | None = None) -> str:
    """对图片内容进行问题类型分类，返回 CODING / MULTIPLE_CHOICE / ... 等标签。

    `provider` 用于 A/B 工具按指定 provider 评测：模型名与端点必须**同时**跟着它变，
    否则会拿 A 家的模型名去请求 B 家的端点。
    """
    logger.info("步骤 1: 正在进行问题类型分类...")
    response = _call_vision_api(
        image_paths, prompts.CLASSIFICATION_PROMPT, _model_for(provider, "classify"),
        stream=False, provider=provider,
    )

    if isinstance(response, str) and response in VALID_PROBLEM_TYPES:
        logger.info(f"分类成功，识别类型为: {response}")
        return response

    logger.warning(f"分类失败或返回未知类型 ('{response}')。将默认视为 'GENERAL'。")
    return "GENERAL"


def transcribe_images_raw(image_paths: list[Path], provider: str | None = None) -> list[str] | None:
    """对多张图片并行执行 OCR 转录，返回结构化文本列表。

    严格模式：任何一页失败即整体返回 None。需要"部分成功也继续"的场景
    请使用 `transcribe_images`。
    """
    result = transcribe_images(image_paths, provider=provider)
    if result["failed"]:
        logger.error("有 %d 张图片转录失败，严格模式下放弃整批", len(result["failed"]))
        return None
    return result["pages"]


def transcribe_images(image_paths: list[Path], provider: str | None = None) -> dict[str, Any]:
    """并行 OCR，允许部分失败。

    `provider` 透传到每一次单页调用（A/B 工具按 provider 对照时需要）。

    Returns:
        {"pages": list[str], "failed": list[int]} — failed 是失败页的下标（0 基）。
        转录成功的页即使为空串也会保留空串，但空串会被记为失败页。
    """
    if not image_paths:
        return {"pages": [], "failed": []}

    logger.info(f"启动对 {len(image_paths)} 张图片的并行转录...")
    model_name = _model_for(provider, "classify")
    pages: list[str] = [""] * len(image_paths)
    failed: list[int] = []

    def transcribe_single(index: int, path: Path):
        return index, _call_vision_api(
            [path], prompts.TRANSCRIPTION_PROMPT, model_name, stream=False, provider=provider,
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


def combined_call_enabled(image_count: int) -> bool:
    """当前配置下，是否值得尝试"合并调用（分类 + 转录）"。

    - `false`：从不尝试，直接走两步流程；
    - `true` ：总是尝试（历史行为）；
    - `auto` ：只在图片数不超过 `COMBINED_VISION_MAX_IMAGES` 时尝试。

    历史背景：旧协议（JSON）要求一次装下**所有**图片的完整转录，输出上限又只有 8192
    token，多图必然被截断、解析失败——实测 12 次尝试只成功 1 次，于是默认值一度被压到 1
    （多图连请求都不发）。现在协议换成 `<<<PAGE n|NEW/CONT>>>`（正文逐字直出）、输出上限
    提到 `VISION_MAX_TOKENS`，多图合并才真正可用，因此上限恢复到 8。
    """
    mode = str(config.USE_COMBINED_VISION_CALL).strip().lower()
    if mode in ("false", "0", "no", "off"):
        return False
    if mode in ("true", "1", "yes", "on"):
        return True
    return image_count <= config.COMBINED_VISION_MAX_IMAGES


# ------------------------------------------------------------------------------
# PAGE 分隔符协议：解析 / 拼接 / 补页
# ------------------------------------------------------------------------------

# `<<<PAGE 3|CONT>>>`：编号 1 基，标记 NEW（新题开头）/ CONT（上一题的延续）。
# **flag 是可选的，也容忍写错**：模型偶尔写成 `<<<PAGE 1>>>` 或 `<<<PAGE 2|NEWY>>>`。
# 计划书的原则是"NEW/CONT 缺失或非法时保守当作 NEW"（不做跨页去重、保留润色）。
# 早先的正则强制 `|NEW`/`|CONT` 精确匹配，于是 `|NEWY` 这种畸形标记**整块不匹配**：
# 它的正文会落进**上一页**的切片（页边界取"下一个标记的起点"），结果是上一页被塞进
# 两页内容、该页槽位为空并触发 refill —— **静默重复**比回退危险得多。
# 因此这里把 flag 放宽为"任意字母 token"，非法值按 NEW 处理。
_PAGE_MARKER_RE = re.compile(r"<<<PAGE\s+(\d+)\s*(?:\|\s*([A-Za-z]+)\s*)?>>>", re.IGNORECASE)
_TYPE_MARKER_RE = re.compile(r"<<<TYPE\s*>>>\s*([A-Za-z_]+)")
_END_MARKER = "<<<END>>>"


def parse_page_protocol(raw: str | None, image_count: int) -> dict | None:
    """解析 `<<<PAGE n|NEW/CONT>>>` 协议，返回按**声明序号**定位的逐页结果。

    为什么按声明序号而不是出现顺序：模型把两张连页合并、把纯图页省略、或者重排页面时，
    按出现顺序会**静默错位**，而"哪张是第一页"错了整道题就废了。按声明的 n 定位后，
    跳号/重复能被直接发现并记入 `failed_pages`。

    正文逐字直出，不经过任何转义层 —— 这正是换协议的目的：JSON 下模型漏转义
    `\\frac`/`\\begin`/`\\theta`/`\\neq` 会被解析成 formfeed/backspace/tab/换行，
    **静默损坏**正文。

    Returns:
        {"problem_type": str|None, "pages": list[str], "continuations": list[bool],
         "failed_pages": list[int], "declared": list[int], "ended": bool}
        完全没有页标记时返回 None（调用方回退到 JSON 协议 / 并行路径）。

    `ended` = 响应里出现了 `<<<END>>>`。流式下拿不到 `finish_reason`（内容生成器只 yield
    文本），**这个标记就是判断"转录被 max_tokens 截断"的协议级信号** —— 它比 finish_reason
    更可靠：被截断时最后一页一定缺 `<<<END>>>`，而缺页本身也会进 failed_pages。
    """
    if not raw:
        return None

    markers = list(_PAGE_MARKER_RE.finditer(raw))
    if not markers:
        return None

    pages: list[str] = [""] * image_count
    continuations: list[bool] = [False] * image_count
    declared: list[int] = []
    conflicting: set[int] = set()

    # `<<<END>>>` 的位置：它之后的内容不属于任何页（模型有时会在结尾补一句客套话）。
    # 对**每一页**都用它截断，而不是只裁最后一页 —— 畸形输出里 END 出现在中间时，
    # 后面的页块本就不可信，清空比把标记混进正文更好（空页会进 failed_pages → refill）。
    end_pos = raw.find(_END_MARKER)

    for index, marker in enumerate(markers):
        number = int(marker.group(1))
        flag = (marker.group(2) or "").upper()
        if number < 1:
            # 0 基编号（`PAGE 0…PAGE N-1`）= 整段页码平移一格：第 1 张图的正文会被
            # 丢弃、其余页全部错配到上一张图。这种平移**无法与"合法跳号"区分**，
            # 因此整段判为不可信（返回 None，由调用方回退 JSON / 并行路径）。
            logger.warning(
                "PAGE 协议出现非法页码 %d（应是 1 基编号）：无法区分编号平移与合法跳号，"
                "整段判为不可信并回退",
                number,
            )
            return None
        if flag not in ("NEW", "CONT"):
            if flag:
                logger.warning("PAGE 协议第 %d 页的标记 '%s' 非法，保守按 NEW 处理", number, flag)
            else:
                # 计划书原则：缺失即保守按 NEW（不跨页去重、保留润色更安全）
                logger.info("PAGE 协议第 %d 页缺少 NEW/CONT 标记，保守按 NEW 处理", number)
        body_start = marker.end()
        body_end = markers[index + 1].start() if index + 1 < len(markers) else len(raw)
        if end_pos != -1:
            body_end = min(body_end, end_pos)
        # body_end < body_start（该页标记出现在 END 之后）时切片天然为空串
        body = raw[body_start:body_end].strip()

        if number > image_count:
            logger.warning("PAGE 协议声明了越界页码 %d（共 %d 张图），该页标记忽略", number, image_count)
            continue
        declared.append(number)
        slot = number - 1
        if pages[slot]:
            conflicting.add(slot)
            logger.warning("PAGE 协议重复声明页码 %d，该页内容可能被覆盖", number)
        pages[slot] = body
        # NEW / CONT 缺失或非法时保守当作 NEW（不做去重、保留润色更安全）
        continuations[slot] = flag == "CONT"

    if not declared:
        return None

    failed_pages = sorted(
        set(conflicting) | {i for i, page in enumerate(pages) if not page.strip()}
    )
    type_match = _TYPE_MARKER_RE.search(raw)
    problem_type = type_match.group(1).strip().upper() if type_match else None

    return {
        "problem_type": problem_type,
        "pages": pages,
        "continuations": continuations,
        "failed_pages": failed_pages,
        "declared": declared,
        "ended": _END_MARKER in raw,
    }


def join_by_continuation(pages: list[str], continuations: list[bool]) -> str:
    """按 `NEW` / `CONT` 标记本地拼接逐页文本（**零 token 成本**）。

    `CONT` 页是模型自己给出的"与上一页是同一道题的延续、已省略重复内容"判断，
    因此本地直接拼接即可，不需要再花一次调用让第二个模型去猜接缝在哪 ——
    而它猜错的方式是**删掉一道题的开头**（见 TEXT_MERGE_AND_POLISH_PROMPT 的场景判断）。
    """
    parts: list[str] = []
    for index, page in enumerate(pages):
        body = (page or "").strip()
        if not body:
            continue
        if parts:
            is_continuation = bool(continuations[index]) if index < len(continuations) else False
            parts.append(("\n" if is_continuation else "\n\n") + body)
        else:
            parts.append(body)
    return "".join(parts)


def refill_pages(
    image_paths: list[Path],
    result: dict,
    *,
    provider: str | None = None,
) -> dict:
    """对失败页做单页 OCR 补做，写回 result（**补 k 页 = k 次调用**，而不是整批重来）。

    只有 `problem_type` 也缺失时才补一次分类调用。

    Returns:
        更新后的 result（原地修改并返回），新增 `refilled` 字段记录补做页数。
    """
    failed = [int(i) for i in (result.get("failed_pages") or [])]
    # 页数可能与图片数不一致（调用方只给了部分页）——补做前先补齐，避免写越界
    page_list = list(result.get("pages") or [])
    if len(page_list) < len(image_paths):
        page_list += [""] * (len(image_paths) - len(page_list))
    result["pages"] = page_list

    # 补做是**互相独立**的单页请求：串行跑最坏情况是 k × (重试 × 超时)，
    # 而 T2 引入 OCR_PARALLEL_WORKERS 的本意就是让逐页转录并行 —— 这里复用同一个上限。
    targets = [index for index in failed if 0 <= index < len(image_paths)]
    refilled = 0
    if targets:

        def _refill_one(index: int) -> tuple[int, object]:
            return index, _call_vision_api(
                [image_paths[index]], prompts.TRANSCRIPTION_PROMPT,
                _model_for(provider, "classify"), stream=False, provider=provider,
            )

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(config.OCR_PARALLEL_WORKERS, len(targets)))
        ) as executor:
            for index, text in executor.map(_refill_one, targets):
                if isinstance(text, str) and text.strip():
                    result["pages"][index] = text.strip()
                    refilled += 1
                    logger.info("refill_pages: 已补做第 %d 页", index + 1)
                else:
                    logger.warning("refill_pages: 第 %d 页补做仍失败", index + 1)
    if failed:
        result["failed_pages"] = [
            i for i in failed if i < len(result["pages"]) and not str(result["pages"][i]).strip()
        ]

    if not result.get("problem_type"):
        result["problem_type"] = classify_problem_type(image_paths, provider=provider)
    result["refilled"] = refilled
    return result


def _normalize_problem_type(raw: object) -> str:
    problem_type = str(raw or "").strip().upper()
    if problem_type not in VALID_PROBLEM_TYPES:
        logger.warning("合并调用返回未知题型 '%s'，按 GENERAL 处理。", problem_type)
        return "GENERAL"
    return problem_type


def _select_pages(pages: list[str], failed_pages: list[int]) -> list[str]:
    """只要**至少一页**有内容就采用（部分成功可救），剩下的交给 refill_pages。"""
    normalized = [str(p).strip() for p in pages]
    if all(not p for p in normalized):
        return []
    if failed_pages:
        logger.warning(
            "合并调用有 %d/%d 页缺失或重复，将采用部分结果并逐页补做",
            len(failed_pages), len(pages),
        )
    return normalized


def _combined_once(
    image_paths: list[Path], provider: str | None, model_name: str
) -> dict | None:
    """对**一批**图片做一次合并调用（PAGE 首选、JSON 兼容回退），**不做补页**。

    返回的 `pages` / `continuations` / `failed_pages` 都是**这一批内**的局部下标；
    跨批的全局映射由 `_merge_batch_results` 负责。返回 None 表示这批彻底失败
    （连页块都没有），调用方按"这批全部缺页"处理并交给 `refill_pages` 单页补做。

    拆出这个函数是为了分批：单批 ≤ `config.VISION_BATCH_SIZE` 张图，
    多批**并发**执行 —— 一次带 8 张是单序列串行生成（实测 6.8 s），
    而 2 批各 4 张并发只要 ≈3.5 s，同时保住"每张图只上传一次"与批内 NEW/CONT 去重。
    """
    logger.info("合并调用（分类 + 转录）：%d 张图，协议=PAGE，流式…", len(image_paths))
    # --- 第一级：PAGE 分隔符协议（首选）---
    text, finish_reason = _collect_stream(
        lambda: _call_vision_api(
            image_paths, prompts.CLASSIFY_AND_TRANSCRIBE_PROMPT,
            model_name, stream=True, provider=provider,
            timeout=config.VISION_COMBINED_TIMEOUT,
        ),
        model_name=model_name,
        max_tokens=config._vision_max_tokens(provider),
    )
    if _STREAM_ERROR_MARKER in text:
        # 客户端初始化失败/重试耗尽：连请求都没发出去，再走 JSON 协议也是白等
        logger.warning("合并调用（PAGE 协议）未获得有效响应，回退到两步流程。")
        return None

    parsed = parse_page_protocol(text, len(image_paths))
    if parsed:
        pages = _select_pages(parsed["pages"], parsed["failed_pages"])
        if pages:
            if not parsed["ended"]:
                # 流式下没有 finish_reason 可看，缺 <<<END>>> 就是"被截断"的协议级信号
                logger.warning(
                    "PAGE 协议响应缺少 <<<END>>> 标记（模型 %s）：转录可能被 max_tokens=%d 截断，"
                    "已收集 %d/%d 页",
                    model_name, config._vision_max_tokens(provider),
                    sum(1 for p in pages if p.strip()), len(pages),
                )
            result = {
                # `<<<TYPE>>>` 缺失时留 None，由 refill_pages 补一次分类调用
                "problem_type": (
                    _normalize_problem_type(parsed["problem_type"])
                    if parsed["problem_type"] else None
                ),
                "pages": pages,
                "failed_pages": list(parsed["failed_pages"]),
                "continuations": parsed["continuations"],
                "vision_mode": "combined",
                "ended": bool(parsed["ended"]),
            }
            logger.info(
                "PAGE 协议解析成功：题型=%s，%d/%d 页，结束标记=%s",
                result["problem_type"], len(pages) - len(result["failed_pages"]),
                len(pages), "有" if parsed["ended"] else "无",
            )
            return result
        logger.warning("PAGE 协议解析出页块但内容全为空，回退 JSON 协议。")
    else:
        logger.warning("PAGE 协议解析失败（无页标记或响应为空），回退 JSON 协议。")

    # --- 第二级：JSON 协议（兼容保留）---
    response = _call_vision_api(
        image_paths, prompts.CLASSIFY_AND_TRANSCRIBE_JSON_PROMPT,
        model_name, stream=False, provider=provider,
        timeout=config.VISION_COMBINED_TIMEOUT,
    )
    if not isinstance(response, str):
        logger.warning("合并调用（JSON 协议）无有效响应，回退到两步流程。")
        return None

    json_parsed = parse_json_response(response)
    if not json_parsed:
        logger.warning("合并调用（JSON 协议）无法解析，回退到两步流程。")
        return None

    raw_pages = json_parsed.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        logger.warning("合并调用（JSON 协议）缺少 pages 数组，回退到两步流程。")
        return None

    # 页数不符时不再"整批作废"：按位置采用已给出的页，缺的走 refill
    pages = [str(p).strip() for p in raw_pages[: len(image_paths)]]
    pages += [""] * (len(image_paths) - len(pages))
    failed_pages = [i for i, page in enumerate(pages) if not page]
    if len(raw_pages) != len(image_paths):
        logger.warning(
            "合并调用（JSON 协议）返回页数 %d 与图片数 %d 不符，缺页将逐页补做",
            len(raw_pages), len(image_paths),
        )
    pages = _select_pages(pages, failed_pages)
    if not pages:
        logger.warning("合并调用（JSON 协议）返回的转录全为空，回退到两步流程。")
        return None

    result = {
        "problem_type": (
            _normalize_problem_type(json_parsed.get("problem_type"))
            if json_parsed.get("problem_type") else None
        ),
        "pages": pages,
        "failed_pages": failed_pages,
        # JSON 协议没有 NEW/CONT 标记 —— 保守当作"每页都是新题"，不做内联去重、保留润色
        "continuations": [False] * len(pages),
        "vision_mode": "json",
        # 截断的 JSON 必然不完整、走不到这里，因此能解析出来就视为完整
        "ended": True,
    }
    logger.info("合并调用（JSON 协议）成功：题型=%s，共 %d 页", result["problem_type"], len(pages))
    return result


def _merge_batch_results(
    batch_lists: list[list[Path]], results: list[dict | None]
) -> dict | None:
    """把多批的局部结果拼成一份**全局**结果（下标按原始图片顺序）。

    - 页序：按下标写回，**不按请求完成顺序** —— 并发返回顺序是随机的，
      而"哪张是第一页"决定整道题对不对；
    - 批首页一律按 `NEW`：批与批之间模型互相看不见，跨批接缝**没有**可信判断，
      保守处理（不跨批去重）比误判成 CONT 更有价值（后者会丢掉一道题的开头）；
    - 批内页数不足/为空的槽位 → 全局 `failed_pages`，由调用方 `refill_pages` 单页补做；
    - `seams`：只有**每一批**都拿到 NEW/CONT 信息才为 True（决定能否本地内联拼接）；
    - `ended`：所有批都出现 `<<<END>>>` 才算 True（协议级截断信号）。

    Returns:
        全局结果；**所有批都彻底失败**时返回 None（调用方回退并行路径）。
    """
    image_count = sum(len(batch) for batch in batch_lists)
    pages: list[str] = [""] * image_count
    continuations: list[bool] = [False] * image_count
    declared_failed: set[int] = set()
    problem_types: list[str] = []
    modes: list[str] = []
    ended = True
    produced_any = False
    # 只要有一批没拿到 PAGE 接缝（彻底失败后由单页 OCR 补做、或走了 JSON 协议），
    # 整段就不能算"有可信接缝" → 交给润色（那里有"场景判断"，不会跨片段删内容）
    all_batches_seamed = True

    offset = 0
    for batch_index, (batch, result) in enumerate(zip(batch_lists, results), start=1):
        size = len(batch)
        if result is None:
            logger.warning(
                "第 %d/%d 批（第 %d-%d 张图）合并调用失败，其页面交给 refill_pages 单页补做",
                batch_index, len(batch_lists), offset + 1, offset + size,
            )
            # 这一批没有任何内容：整个批的槽位保持为空 → 全部进 failed_pages；
            # 补做出来的是**单页独立转录**（页间没有接缝判断），因此必须放弃内联
            ended = False
            all_batches_seamed = False
            offset += size
            continue

        produced_any = True
        local_pages = [str(p).strip() for p in (result.get("pages") or [])]
        local_conts = list(result.get("continuations") or [])
        for local in range(size):
            pages[offset + local] = local_pages[local] if local < len(local_pages) else ""
            # 批首没有跨批接缝信息 → 保守按 NEW；跨批重复宁可留着，也不丢内容
            continuations[offset + local] = bool(local_conts[local]) if (local and local < len(local_conts)) else False
        for index in (result.get("failed_pages") or []):
            if 0 <= int(index) < size:
                declared_failed.add(offset + int(index))
        if result.get("problem_type"):
            problem_types.append(str(result["problem_type"]))
        modes.append(str(result.get("vision_mode") or ""))
        if not result.get("ended", True):
            ended = False
        offset += size

    if not produced_any:
        return None

    failed_pages = sorted(
        declared_failed | {i for i, page in enumerate(pages) if not page.strip()}
    )
    # 题型：多批不一致时取**多数**（并列时取最早出现的）。一组互不相关的题本来就没有
    # 唯一题型，而"哪一批先返回"是随机的，取多数比取"第一个完成的"稳定。
    problem_type = None
    if problem_types:
        problem_type = max(
            dict.fromkeys(problem_types), key=lambda label: problem_types.count(label)
        )

    seams = bool(modes) and all_batches_seamed and all(mode == "combined" for mode in modes)
    # `vision_mode` 描述"页面从哪条路径来"，`seams` 描述"能不能本地内联" —— 两者不能混用：
    # 多批时即便某批失败/走了 JSON（seams=False），整体仍然是 batched（页面来自分批合并
    # + 单页补做），把它标成 parallel 会让统计与排障都读错。
    if len(batch_lists) > 1:
        vision_mode = "batched"
    elif seams:
        vision_mode = "combined"
    elif modes and modes[0] == "json":
        vision_mode = "json"
    else:
        vision_mode = "parallel"

    logger.info(
        "分批合并完成：%d 批 → %d 页，题型=%s，模式=%s，缺页=%d，结束标记=%s",
        len(batch_lists), image_count, problem_type, vision_mode,
        len(failed_pages), "有" if ended else "无",
    )
    return {
        "problem_type": problem_type,
        "pages": pages,
        "failed_pages": failed_pages,
        "continuations": continuations,
        "vision_mode": vision_mode,
        "ended": ended,
        # 真实 HTTP 请求数（= 批数）。**不**用作计费放大倍数：每张图只上传一次，
        # 输入 token 已按页数折算，乘批数会把成本算成 N 倍。
        "calls": len(batch_lists),
        "seams": seams,
    }


def classify_and_transcribe(image_paths: list[Path], provider: str | None = None) -> dict | None:
    """一次（或**分批并行**的多次）视觉调用同时得到题型与逐页文本。

    回退链（把「整批作废」降级为「按页补齐」）：:

        PAGE 协议解析 → 部分成功即采用，缺页走 refill_pages 单页补做
          ↓ 完全无法解析
        JSON 协议（保留兼容）
          ↓ 失败
        1 次分类 + N 次 OCR 并行路径（返回 None，由调用方走 parallel）

    图片数超过 `config.VISION_BATCH_SIZE` 时**分批 + 批间并行**（组 H2）：
    一次带 8 张是单序列串行生成（实测中位数 6.8 s），拆成 2 批各 4 张并发只需 ≈3.5 s，
    同时保住"每张图只上传一次"与批内 NEW/CONT 去重。跨批接缝没有可信判断，
    因此每批首页按 NEW 处理（见 `_merge_batch_results`）。

    流式是**必须的**：`stream=False` 时整段生成必须在单个超时内完成，而合并调用
    要输出 6–16K token，非流式几乎必然超时。

    Returns:
        {"problem_type": str, "pages": list[str], "failed_pages": list[int],
         "continuations": list[bool], "vision_mode": "combined" | "batched" | "json",
         "refilled": int, "ended": bool, "calls": int, "seams": bool}
        失败返回 None，调用方回退到 `classify_and_transcribe_parallel`。

    `ended`：PAGE 协议下 = 响应里出现了 `<<<END>>>`；JSON 协议下 = True（能解析出
    完整 JSON 就说明没有被截断到不可解析）。**流式下拿不到 `finish_reason`**
    （`_call_vision_api(stream=True)` 只 yield 文本），所以 `ended` 就是判断
    "转录是否被 max_tokens 截断"的**协议级信号**，webapp 告警与 A/B 报告都用它。
    """
    if not combined_call_enabled(len(image_paths)):
        logger.info(
            "跳过合并调用，直接走「分类 + 逐页转录」两步（模式=%s，图片数=%d，上限=%d）",
            config.USE_COMBINED_VISION_CALL,
            len(image_paths),
            config.COMBINED_VISION_MAX_IMAGES,
        )
        return None

    # provider 只解析一次：模型名、输出上限与端点必须来自**同一家**（见 _model_for）
    model_name = _model_for(provider, "classify")
    batch_size = max(1, int(config.VISION_BATCH_SIZE))
    batches = [
        image_paths[start:start + batch_size]
        for start in range(0, len(image_paths), batch_size)
    ]

    if len(batches) == 1:
        result = _combined_once(batches[0], provider, model_name)
        if result is None:
            return None
        result["calls"] = 1
        result["seams"] = result.get("vision_mode") == "combined"
    else:
        workers = max(1, min(len(batches), int(config.VISION_BATCH_WORKERS)))
        logger.info(
            "步骤 1+2: 分批合并调用（%d 张图 → %d 批，每批 ≤%d 张，%d 批并发）",
            len(image_paths), len(batches), batch_size, workers,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(
                executor.map(
                    lambda batch: _combined_once(batch, provider, model_name), batches
                )
            )
        result = _merge_batch_results(batches, results)
        if result is None:
            logger.warning("分批合并的每一批都失败，回退到「分类 + 逐页转录」两步流程。")
            return None

    if not any(str(page).strip() for page in result["pages"]):
        # 一页都没拿到：交给调用方走并行路径（那里每页都是独立请求，重试面更大）
        logger.warning("合并调用未产出任何页面内容，回退到两步流程。")
        return None

    return refill_pages(image_paths, result, provider=provider)



def classify_and_transcribe_parallel(
    image_paths: list[Path], provider: str | None = None
) -> tuple[str, list[str], list[int]]:
    """兜底路径：分类与逐页 OCR 并行执行。

    两条调用互不依赖，串行执行会把延迟相加；并行后关键路径变成
    max(T分类, T_OCR)，用户能更早开始等待求解结果。

    `provider` 必须透传：A/B 工具的"并行腿"过去不接这个参数，于是按
    `--provider zhipu` 评测时实际打的是**默认 provider**，对照结论无效。

    这里**不**做合并去重，因此返回的 `failed_pages` 会原样交给调用方；调用方按
    `vision_mode="parallel"` 保留润色调用（第二段 prompt 已加"场景判断"，
    多道独立题时不会跨片段删内容）。

    Returns:
        (problem_type, pages, failed_indices)
    """
    logger.info("步骤 1+2: 并行执行分类与转录...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        classify_future = executor.submit(classify_problem_type, image_paths, provider)
        transcribe_future = executor.submit(transcribe_images, image_paths, provider)
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


def solve_visual_reasoning_problem(
    image_paths: list[Path], provider: str | None = None
) -> Generator[str, None, None] | None:
    """使用视觉推理模型解决图形/规律推理类问题。

    DeepSeek 分支：非思考模式下 `temperature` / `top_p` 均不生效，因此只下发
    `thinking.type=disabled`，避免"设了 0.7 其实没用"的错觉（组 B3）。
    智谱分支：保持迁移前的 `top_p=0.8, temperature=0.7`，保证回退路径逐字节一致。
    """
    model_name = _model_for(provider, "reasoning")
    logger.info("步骤 2.2: 正在使用视觉推理模型 '%s' 进行求解...", model_name)
    return _call_vision_api(
        image_paths,
        prompts.PROMPT_TEMPLATES["VISUAL_REASONING"],
        model_name,
        stream=True,
        extra_params=_provider_sampling_params(provider),
        provider=provider,
    )
