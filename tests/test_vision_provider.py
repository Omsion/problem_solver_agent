"""
test_vision_provider.py - 视觉层 provider 分支、回退一致性与密钥校验

为什么单独一个文件：迁移（GLM-4.6V → deepseek-flash）的风险几乎全部集中在
"同一份代码在两个 provider 下发出的**请求体差异**"上——
- DeepSeek 思考模式默认开启且 effort=high，不显式下发 `thinking.type=disabled`
  就会把 max_tokens 吃光、正文为空（S3）；
- DeepSeek 非思考模式下 `temperature`/`top_p` 静默失效，传了只会造成错觉；
- `VISION_PROVIDER=zhipu` 必须能一键回退，且请求形状与迁移前逐字节一致（S5）；
- 密钥缺失时要报出**该 provider 自己的**环境变量名，而不是写死 ZHIPU_API_KEY（S7）。

请求形状只能在 payload 构造的那一刻观察，因此这里的假 client 只做两件事：
记录 `**payload`，并返回一个最小可用的响应对象。**不产生任何真实 API 调用。**

运行：pytest tests/test_vision_provider.py -v
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from problem_solver_agent import config, vision_client

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 打桩：记录请求体
# ---------------------------------------------------------------------------


class _RecordingCompletions:
    """假的 `chat.completions`：记录每次 `**payload`，返回最小可用的响应对象。"""

    def __init__(self, content: str | None = "OK", finish_reason: str | None = "stop") -> None:
        self.payloads: list[dict] = []
        self._content = content
        self._finish_reason = finish_reason

    def create(self, **payload):
        self.payloads.append(payload)
        choice = SimpleNamespace(
            message=SimpleNamespace(content=self._content),
            finish_reason=self._finish_reason,
        )
        return SimpleNamespace(choices=[choice])


@pytest.fixture()
def recorded(monkeypatch) -> _RecordingCompletions:
    """把假 client 注入 `_get_vision_client`，并清掉模块级 client 缓存。

    `_vision_clients` 是模块级 dict：不清掉的话，真实运行（或上一个用例）留下的
    实例会让 monkeypatch 失效，于是用例可能真的打到线上 API。
    """
    monkeypatch.setattr(vision_client, "_vision_clients", {})
    completions = _RecordingCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        vision_client, "_get_vision_client", lambda provider=None: fake_client
    )
    return completions


def _use_deepseek(monkeypatch) -> None:
    """切到 DeepSeek 分支。

    实现里 `_build_extra_body` 走 `config.VISION_PROVIDER_NAME`，
    `_get_vision_client` / `_vision_api_key` 走 `config.VISION_PROVIDER`，
    两个都改，避免"半切换"造成难以理解的结果。
    """
    monkeypatch.setattr(config, "VISION_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "VISION_PROVIDER_NAME", "deepseek")
    monkeypatch.setattr(config, "VISION_DISABLE_THINKING", True)


def _use_zhipu(monkeypatch) -> None:
    monkeypatch.setattr(config, "VISION_PROVIDER", "zhipu")
    monkeypatch.setattr(config, "VISION_PROVIDER_NAME", "zhipu")


def _capture_agent_log() -> tuple[list[logging.LogRecord], logging.Handler]:
    """抓 AgentLogger 的日志。

    `AgentLogger` 显式 `propagate=False`（见 utils.setup_logger），pytest 的 caplog
    抓不到，只能自己挂 handler——与 test_vision_client.py 里的写法一致。
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logging.getLogger("AgentLogger").addHandler(handler)
    return records, handler


# ---------------------------------------------------------------------------
# S3 / 组 B2：DeepSeek 必须显式关闭思考
# ---------------------------------------------------------------------------


def test_deepseek_payload_disables_thinking(recorded, monkeypatch):
    """S3：deepseek 的视觉 payload 必须带 `thinking.type=disabled` 且用配置的输出上限。

    为什么这条是**回归保护**：DeepSeek「思考模式默认打开，且 effort 默认为 high」，
    而 OpenAI SDK 不认顶层的 `thinking`，只能放进 `extra_body`。少了下发这一步，
    分类/OCR 的正文会整个被思考过程吃掉（solver 已经踩过同一个坑）。
    """
    _use_deepseek(monkeypatch)

    text = vision_client._call_vision_api([], "prompt", config.VISION_CLASSIFY_MODEL)

    assert text == "OK"
    payload = recorded.payloads[0]
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}
    # 输出上限必须来自配置（旧实现写死 8192，会把多图合并转录截断）
    assert payload["max_tokens"] == config.VISION_MAX_TOKENS
    assert payload["stream"] is False
    assert payload["model"] == config.VISION_CLASSIFY_MODEL


def test_deepseek_with_thinking_switch_off_omits_extra_body(recorded, monkeypatch):
    """`VISION_DISABLE_THINKING=false` 是逃生开关：此时不下发 extra_body。

    锁定这个开关的语义，避免有人把"关闭思考"改成无条件下发——
    那样就没法用配置回到"让模型思考"的行为了。
    """
    _use_deepseek(monkeypatch)
    monkeypatch.setattr(config, "VISION_DISABLE_THINKING", False)

    vision_client._call_vision_api([], "prompt", config.VISION_CLASSIFY_MODEL)

    assert "extra_body" not in recorded.payloads[0]
    assert vision_client._build_extra_body() == {}


# ---------------------------------------------------------------------------
# S5 / 组 B3：provider 分支的请求形状
# ---------------------------------------------------------------------------


def test_zhipu_payload_has_no_extra_body(recorded, monkeypatch):
    """S5：回退 provider 不支持思考开关，连 `extra_body` 键都不该出现。

    这是"逐字节一致"的一部分：多一个空 `{}` 也是请求体差异，且 `extra_body`
    是给 DeepSeek 的特有能力，不该泄漏到智谱请求上。
    """
    _use_zhipu(monkeypatch)

    vision_client._call_vision_api([], "prompt", config.VISION_PROVIDER_CONFIG["zhipu"]["classify_model"])

    payload = recorded.payloads[0]
    assert "extra_body" not in payload
    assert payload["model"] == "GLM-4.6V-FlashX"
    assert vision_client._build_extra_body() == {}


def test_each_provider_payload_uses_its_own_output_cap(recorded):
    """输出上限必须按 provider 取：GLM 8192 vs DeepSeek 32768。

    S5（回退后与迁移前逐字节一致）的关键一环：迁移前视觉请求写死 `max_tokens=8192`，
    而 GLM 系列的输出上限就是 8192。用一个全局常量会让 `provider="zhipu"` 发出 32768
    —— 要么被 GLM 拒绝（回退路径直接死掉，而它是 C5 单点依赖的唯一安全网）、
    要么被静默截断。这条断言同时锁住"显式指定 provider 时取那一家的值"。
    """
    vision_client._call_vision_api([], "prompt", "GLM-4.6V-FlashX", provider="zhipu")
    zhipu_payload = recorded.payloads[-1]
    assert zhipu_payload["max_tokens"] == int(config.VISION_PROVIDER_CONFIG["zhipu"]["max_tokens"]) == 8192
    assert zhipu_payload["max_tokens"] != config.VISION_MAX_TOKENS

    vision_client._call_vision_api([], "prompt", "deepseek-flash", provider="deepseek")
    deepseek_payload = recorded.payloads[-1]
    assert deepseek_payload["max_tokens"] == int(
        config.VISION_PROVIDER_CONFIG["deepseek"]["max_tokens"]
    ) == 32768


def test_sampling_params_follow_provider(monkeypatch):
    """组 B3：视觉推理的采样参数按 provider 分支。

    DeepSeek 非思考模式下 temperature/top_p **静默失效**（top_p 恒为 1.0），
    传了只造成"以为设了 0.7 其实没用"的错觉 → 只发 extra_body；
    智谱保持迁移前的 top_p=0.8 / temperature=0.7，保证回退路径与现状一致。
    """
    _use_deepseek(monkeypatch)
    assert vision_client._provider_sampling_params() == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }

    _use_zhipu(monkeypatch)
    assert vision_client._provider_sampling_params() == {"top_p": 0.8, "temperature": 0.7}


@pytest.mark.parametrize("provider", ["deepseek", "zhipu"])
def test_visual_reasoning_request_shape_by_provider(provider, monkeypatch):
    """端到端确认 `solve_visual_reasoning_problem` 真的把分支参数透传下去。

    只测 `_provider_sampling_params()` 是不够的：参数没传进 `_call_vision_api`
    一样是白设（这正是 T5 的教训——旧代码"关了思考"其实没下发）。
    """
    monkeypatch.setattr(config, "VISION_DISABLE_THINKING", True)
    captured: dict = {}

    def fake_call(image_paths, user_prompt, model_name, **kwargs):
        captured.update(kwargs)
        captured["model_name"] = model_name
        return iter(())

    monkeypatch.setattr(vision_client, "_call_vision_api", fake_call)

    vision_client.solve_visual_reasoning_problem([], provider=provider)

    assert captured["stream"] is True
    assert captured["provider"] == provider
    # 视觉推理用的是 reasoning 模型，**且必须来自该 provider 的表**
    # （曾经的 bug：模型名读默认 provider 的常量，provider="zhipu" 时会把
    #  deepseek-flash 发到智谱端点，A/B 的"另一家"那一腿因此完全无效）
    assert captured["model_name"] == config.VISION_PROVIDER_CONFIG[provider]["reasoning_model"]
    extra = captured["extra_params"]
    if provider == "deepseek":
        assert extra == {"extra_body": {"thinking": {"type": "disabled"}}}
        assert "top_p" not in extra and "temperature" not in extra
    else:
        assert "extra_body" not in extra
        assert extra == {"top_p": 0.8, "temperature": 0.7}


# ---------------------------------------------------------------------------
# S5 / 组 G3：zhipu 回退分支的模型名与迁移前一致
# ---------------------------------------------------------------------------


def test_zhipu_config_table_matches_pre_migration():
    """provider 表里的 zhipu 条目就是迁移前的模型名/端点/密钥变量。"""
    cfg = config.VISION_PROVIDER_CONFIG["zhipu"]
    assert cfg["classify_model"] == "GLM-4.6V-FlashX"
    assert cfg["reasoning_model"] == "GLM-4.6V"
    assert cfg["api_key_env"] == "ZHIPU_API_KEY"
    assert cfg["base_url"] == "https://open.bigmodel.cn/api/paas/v4/"
    # DeepSeek 不支持该开关，表里必须标 0，否则 extra_body 会漏到 zhipu 请求上
    assert cfg["supports_thinking_control"] == "0"


_PROBE_SCRIPT = (
    "import json;"
    "from problem_solver_agent import config as c;"
    "print(json.dumps({"
    "'VISION_PROVIDER': c.VISION_PROVIDER,"
    "'VISION_PROVIDER_NAME': c.VISION_PROVIDER_NAME,"
    "'DEFAULT_VISION_PROVIDER': c.DEFAULT_VISION_PROVIDER,"
    "'VISION_CLASSIFY_MODEL': c.VISION_CLASSIFY_MODEL,"
    "'VISION_REASONING_MODEL': c.VISION_REASONING_MODEL,"
    "'VISION_BASE_URL': c.VISION_BASE_URL,"
    "'VISION_MAX_TOKENS': c.VISION_MAX_TOKENS,"
    "'MAX_TOKENS_deepseek': c._vision_max_tokens('deepseek'),"
    "'MAX_TOKENS_zhipu': c._vision_max_tokens('zhipu'),"
    "}, ensure_ascii=False))"
)


def _config_snapshot_in_fresh_process(provider: str, extra_env: dict | None = None) -> dict:
    """在**子进程**里用指定 `VISION_PROVIDER` 导入 config，回读派生常量。

    为什么用子进程而不是 `importlib.reload`：派生常量是模块级、导入期算出来的，
    同进程 reload 会把 config 改成 zhipu 状态，污染同一次 pytest 会话里其它用例
    （它们的模块级常量、client 缓存都按 deepseek 建的）。子进程天然隔离。
    """
    env = dict(os.environ)
    env["VISION_PROVIDER"] = provider
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env or {})

    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, f"子进程导入 config 失败：{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_zhipu_env_derives_glm_models():
    """`VISION_PROVIDER=zhipu` 时派生常量必须回到 GLM 系列（证明回退可用）。"""
    snapshot = _config_snapshot_in_fresh_process("zhipu")

    assert snapshot["VISION_PROVIDER"] == "zhipu"
    assert snapshot["VISION_PROVIDER_NAME"] == "zhipu"
    assert snapshot["VISION_CLASSIFY_MODEL"] == "GLM-4.6V-FlashX"
    assert snapshot["VISION_REASONING_MODEL"] == "GLM-4.6V"
    assert snapshot["VISION_BASE_URL"] == "https://open.bigmodel.cn/api/paas/v4/"


@pytest.mark.parametrize("raw_value", ["ZHIPU", "  zhipu  "])
def test_provider_env_is_normalised(raw_value):
    """大小写与空白都要能容错（.env 里手写 `Zhipu ` 不该让服务起不来）。"""
    snapshot = _config_snapshot_in_fresh_process(raw_value)

    assert snapshot["VISION_PROVIDER"] == "zhipu"
    assert snapshot["VISION_CLASSIFY_MODEL"] == "GLM-4.6V-FlashX"


def test_unknown_provider_falls_back_to_default_without_raising():
    """组 G4 / S5：未知 VISION_PROVIDER 值回落**当前默认 provider**，**不抛异常**。

    默认值由计划书 §6 第 5 步决定：A/B 闸门（S1/S2）通过后默认就是迁移目标 deepseek
    （2026-09-21 判定通过，数据见计划书 §8.6）。这条用例锁住"默认值是什么"，
    让将来任何一次默认值变更都必须是一次**显式**改动。
    """
    snapshot = _config_snapshot_in_fresh_process("definitely-not-a-provider")

    assert snapshot["DEFAULT_VISION_PROVIDER"] == "deepseek"
    assert snapshot["VISION_PROVIDER"] == snapshot["DEFAULT_VISION_PROVIDER"]
    assert snapshot["VISION_PROVIDER_NAME"] == snapshot["DEFAULT_VISION_PROVIDER"]
    assert snapshot["VISION_CLASSIFY_MODEL"] == "deepseek-flash"
    assert snapshot["VISION_REASONING_MODEL"] == "deepseek-flash"
    assert snapshot["VISION_BASE_URL"] == "https://api.deepseek.com"


def test_deepseek_env_derives_flash_models():
    """`VISION_PROVIDER=deepseek` 显式启用时必须派生 deepseek-flash（迁移目标可达）。"""
    snapshot = _config_snapshot_in_fresh_process("deepseek")

    assert snapshot["VISION_PROVIDER"] == "deepseek"
    assert snapshot["VISION_PROVIDER_NAME"] == "deepseek"
    assert snapshot["VISION_CLASSIFY_MODEL"] == "deepseek-flash"
    assert snapshot["VISION_REASONING_MODEL"] == "deepseek-flash"
    assert snapshot["VISION_BASE_URL"] == "https://api.deepseek.com"
    assert snapshot["VISION_MAX_TOKENS"] == 32768


def test_output_cap_env_override_does_not_leak_to_the_other_provider():
    """`VISION_MAX_TOKENS` 只是**当前** provider 的现场覆盖值，不能串到另一家。

    现场调试时常把上限调大（例如 65536）；若不按 provider 分支，A/B 工具按
    `provider="zhipu"` 跑时就会带着这个值去请求 GLM（上限 8192）。
    """
    snapshot = _config_snapshot_in_fresh_process(
        "deepseek", extra_env={"VISION_MAX_TOKENS": "65536"}
    )

    assert snapshot["VISION_MAX_TOKENS"] == 65536
    assert snapshot["MAX_TOKENS_deepseek"] == 65536          # 当前 provider：尊重覆盖
    assert snapshot["MAX_TOKENS_zhipu"] == 8192              # 另一家：始终用表值


# ---------------------------------------------------------------------------
# S7：密钥缺失时报出正确的环境变量名
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "env_name"),
    [("deepseek", "DEEPSEEK_API_KEY"), ("zhipu", "ZHIPU_API_KEY")],
)
def test_missing_api_key_returns_none_and_names_the_right_env_var(
    provider, env_name, monkeypatch
):
    """S7：`_get_vision_client()` 返回 None，且日志点名**该 provider 的**环境变量。

    旧实现写死 `ZHIPU_API_KEY`，于是 `VISION_PROVIDER=deepseek` 缺密钥时，
    用户会照着日志去配一个根本没用的变量。这里同时断言"另一个变量名不出现"，
    否则"文案里两个名字都提一遍"也能骗过只查 in 的断言。
    """
    monkeypatch.setattr(vision_client, "_vision_clients", {})
    monkeypatch.setattr(config, "VISION_PROVIDER", provider)
    monkeypatch.setattr(config, "VISION_PROVIDER_NAME", provider)
    # 真实 .env 里可能确实配了密钥（模块级常量已经吃到），必须一并置空，
    # 否则会走"宿主程序注入默认 provider 密钥"的分支，用例就测不到缺失路径了。
    monkeypatch.setattr(config, "VISION_API_KEY", None)
    monkeypatch.delenv(env_name, raising=False)

    records, handler = _capture_agent_log()
    try:
        client = vision_client._get_vision_client()
    finally:
        logging.getLogger("AgentLogger").removeHandler(handler)

    assert client is None
    messages = [record.getMessage() for record in records]
    assert any(env_name in message for message in messages), messages

    other_name = "ZHIPU_API_KEY" if env_name == "DEEPSEEK_API_KEY" else "DEEPSEEK_API_KEY"
    assert all(other_name not in message for message in messages), messages


def test_unknown_provider_argument_is_reported(monkeypatch):
    """显式传一个不存在的 provider：记 critical 并返回 None（不静默用默认密钥）。"""
    monkeypatch.setattr(vision_client, "_vision_clients", {})

    records, handler = _capture_agent_log()
    try:
        client = vision_client._get_vision_client("no-such-provider")
    finally:
        logging.getLogger("AgentLogger").removeHandler(handler)

    assert client is None
    assert any("no-such-provider" in record.getMessage() for record in records)


def test_each_provider_builds_its_own_client(monkeypatch):
    """provider 切换不能复用上一个 provider 的 client（key/base_url 都不同）。

    真实 `OpenAI(...)` 构造不发网络请求，所以这里不违背"不产生真实 API 调用"。
    用例结束后 monkeypatch 会把 `_vision_clients` 还原，缓存不会泄漏给其它用例。
    """
    monkeypatch.setattr(vision_client, "_vision_clients", {})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-test-zhipu")
    monkeypatch.setattr(config, "VISION_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "VISION_PROVIDER_NAME", "deepseek")

    deepseek_client = vision_client._get_vision_client("deepseek")
    zhipu_client = vision_client._get_vision_client("zhipu")

    assert deepseek_client is not None and zhipu_client is not None
    assert deepseek_client is not zhipu_client
    assert str(deepseek_client.base_url).rstrip("/") == "https://api.deepseek.com"
    assert (
        str(zhipu_client.base_url).rstrip("/")
        == "https://open.bigmodel.cn/api/paas/v4"
    )
    assert set(vision_client._vision_clients) == {"deepseek", "zhipu"}
    # 同一个 provider 第二次调用走缓存，不重复构造
    assert vision_client._get_vision_client("deepseek") is deepseek_client
