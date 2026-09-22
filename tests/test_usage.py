"""
test_usage.py - 用量记账与图片 token 口径测试

覆盖迁移文档 §11.6 第 5 项（已修）：旧口径把「每图 token」按 `calls` 再乘一遍，
而 `calls`（请求次数）与「上传了几张图」是两个独立的量 —— 逐页 OCR 是 8 次调用
各带 1 张图（共 8 张），分批合并是 2 次调用合起来 8 张。旧口径在 8 图回退路径上
把图片成本算成 `1024 × 8 × 8`，是真实上限的 8 倍。

判据（本文件锁定的四条）：
1. **图片 token 只跟"上传了几张图"走**，与请求次数无关；
2. 并行回退路径不得因为 `calls=8` 被重复计费（`classify` + `ocr` 合计恰为 8 张图）；
3. 纯文本阶段（润色 / 求解 / 重解 / 文件名）不携带图片 token；
4. **老 payload 兼容**：没有 `images` 键时按旧口径（`calls × pages`）兜底，
   不会因为升级而少扣额度。

运行：pytest tests/test_usage.py -v
"""

from __future__ import annotations

import pytest
from PIL import Image

from problem_solver_agent import config as core_config
from problem_solver_agent import core_pipeline
from webapp.accounts import AccountManager
from webapp.usage import (
    BASE_INPUT_TOKENS,
    CHARS_PER_TOKEN_FACTOR,
    TOKENS_PER_IMAGE,
    UsageRecorder,
    estimate_tokens,
)


@pytest.fixture()
def billing(tmp_path) -> tuple[UsageRecorder, AccountManager, str]:
    accounts = AccountManager(tmp_path / "accounts.db")
    user = accounts.create_user(phone="13800000000", password="pw", budget=100.0)
    return UsageRecorder(accounts), accounts, user.id


def _record(billing: UsageRecorder, user_id: str, **report) -> dict:
    """记账并取回刚写入的那条流水（list_usage 按时间倒序）。"""
    result = billing.record(user_id=user_id, task_id="t1", report=report)
    assert result is not None, "记账不应失败"
    return result


# ---------------------------------------------------------------------------
# 1. 图片 token 与请求次数解耦
# ---------------------------------------------------------------------------


def test_image_tokens_do_not_scale_with_request_count():
    """同样 8 张图，1 次调用与 8 次调用的**图片成本必须完全一致**。"""
    one_input, _ = estimate_tokens(8, 0, calls=1, images=8)
    eight_input, _ = estimate_tokens(8, 0, calls=8, images=8)

    # 差出来的只有"每次调用都要重发一遍"的固定开销，图片部分一模一样
    assert eight_input - one_input == BASE_INPUT_TOKENS * 7, "固定开销按调用次数走"
    assert one_input - BASE_INPUT_TOKENS == 8 * TOKENS_PER_IMAGE
    assert eight_input - BASE_INPUT_TOKENS * 8 == 8 * TOKENS_PER_IMAGE
    # 旧口径（把 8 张图按 8 次调用再乘一遍）会得到 64 张图的成本 = 65536 token，
    # 是真实上传量（8192）的 8 倍；新口径必须远低于它。
    legacy_images = 8 * 8 * TOKENS_PER_IMAGE
    assert eight_input - BASE_INPUT_TOKENS * 8 == legacy_images // 8
    assert eight_input < legacy_images


def test_images_zero_means_text_only_call():
    """显式 `images=0`（润色 / 求解这类纯文本调用）不得带上任何图片 token。"""
    input_tokens, _ = estimate_tokens(8, 100, calls=1, images=0)
    assert input_tokens == BASE_INPUT_TOKENS
    assert input_tokens < TOKENS_PER_IMAGE


def test_missing_images_falls_back_to_legacy_multiplier():
    """老 payload（没有 `images` 键）按旧口径 `calls × pages` 兜底 —— 不能少扣。"""
    legacy_input, _ = estimate_tokens(8, 0, calls=8)
    new_input, _ = estimate_tokens(8, 0, calls=8, images=8)
    assert legacy_input == BASE_INPUT_TOKENS * 8 + 8 * 8 * TOKENS_PER_IMAGE
    assert legacy_input > new_input, "兜底口径方向必须保守（宁可多扣）"


def test_old_positional_signature_still_works():
    """`estimate_tokens(pages, chars)` 的旧调用方式保持逐字节一致的行为。"""
    assert estimate_tokens(3, 1000) == (
        BASE_INPUT_TOKENS + 3 * TOKENS_PER_IMAGE,
        int(1000 * CHARS_PER_TOKEN_FACTOR),
    )


# ---------------------------------------------------------------------------
# 2. 记账：并行回退路径不得重复计费
# ---------------------------------------------------------------------------


def test_parallel_fallback_does_not_multiply_image_cost(billing):
    """8 图回退路径：`classify`(1 次调用, 8 张图) + `ocr`(8 次调用, 8 张图)。

    真实图片上传量是 **16 张次**（分类那次把 8 张全带上，逐页 OCR 再各带 1 张），
    因此图片成本恰好是合并路径的 2 倍 —— 这是并行路径的固有代价，不是 bug。
    bug 在旧口径：它按 `calls × pages` 把 OCR 的 8 张图乘成 64 张，
    合计 72 张次 = 73728 token，是真实量（16 张次 = 16384）的 **4.5 倍**。
    """
    usage, accounts, user_id = billing
    pages = 8

    _record(usage, user_id, stage="classify", model="deepseek-flash",
            provider="deepseek", pages=pages, output_chars=0, calls=1, images=pages)
    _record(usage, user_id, stage="ocr", model="deepseek-flash",
            provider="deepseek", pages=pages, output_chars=0, calls=pages, images=pages)

    events = accounts.list_usage(user_id)
    total_input = sum(event["input_tokens"] for event in events)
    # 固定开销按 1 + 8 次调用；图片按真实的 8 + 8 = 16 张次
    assert total_input == BASE_INPUT_TOKENS * 9 + 2 * pages * TOKENS_PER_IMAGE
    # 旧口径会把 OCR 的图片成本乘成 8 倍，这里锁住"没有发生"
    legacy_input = BASE_INPUT_TOKENS * 9 + (pages + pages * pages) * TOKENS_PER_IMAGE
    assert total_input < legacy_input
    assert legacy_input / total_input > 3, "旧口径确实高估了 3 倍以上"


def test_combined_path_records_images_once(billing):
    """合并路径：`calls` 恒为 1（计费倍数），但 `images` 是全部页数。"""
    usage, accounts, user_id = billing
    _record(usage, user_id, stage="vision", model="deepseek-flash",
            provider="deepseek", pages=8, output_chars=0, calls=1, images=8)

    event = accounts.list_usage(user_id)[0]
    assert event["input_tokens"] == BASE_INPUT_TOKENS + 8 * TOKENS_PER_IMAGE


def test_text_only_stage_records_no_image_tokens(billing):
    """润色阶段虽然 `pages=8`（那是任务页数），但一张图都不传 → 不计图片 token。"""
    usage, accounts, user_id = billing
    _record(usage, user_id, stage="polish", model="deepseek-flash",
            provider="deepseek", pages=8, output_chars=2000, calls=1, images=0)

    event = accounts.list_usage(user_id)[0]
    assert event["input_tokens"] == BASE_INPUT_TOKENS
    assert event["output_tokens"] == int(2000 * CHARS_PER_TOKEN_FACTOR)


def test_vision_refill_only_pays_for_the_refilled_pages(billing):
    """补做 1 页 = 1 张图 + 1 次调用，不是整批的成本。"""
    usage, accounts, user_id = billing
    _record(usage, user_id, stage="vision_refill", model="deepseek-flash",
            provider="deepseek", pages=1, output_chars=0, calls=1, images=1)

    event = accounts.list_usage(user_id)[0]
    assert event["input_tokens"] == BASE_INPUT_TOKENS + TOKENS_PER_IMAGE


# ---------------------------------------------------------------------------
# 3. 提交前的额度预检
# ---------------------------------------------------------------------------


def test_estimate_task_cost_uses_high_peak_price_and_single_vision_call(billing):
    """预检金额按峰值单价、1 次视觉调用（带全部图）+ 1 次求解估算。

    回归保护：曾经给视觉留 `images=None` 的旧口径，把预检金额抬到实际的数倍，
    用户会因为一个算错的数字被 `402 insufficient_budget` 拦住。
    """
    usage, _, _ = billing
    pages = 8
    cost = usage.estimate_task_cost(pages)
    expected = (
        (BASE_INPUT_TOKENS + pages * TOKENS_PER_IMAGE) / 1_000_000 * 2.0
        + BASE_INPUT_TOKENS / 1_000_000 * 2.0
        + int(4000 * CHARS_PER_TOKEN_FACTOR) / 1_000_000 * 8.0
    )
    assert cost == pytest.approx(round(expected, 6))
    # 视觉部分不得出现"8 张图 × 8 次"的 8 倍虚高
    assert cost < 0.05


def test_estimate_task_cost_follows_the_current_vision_model(billing, monkeypatch):
    """视觉单价必须跟随当前 provider —— 写死模型名会在回退后按错单价预检。"""
    usage, _, _ = billing
    monkeypatch.setattr(core_config, "VISION_CLASSIFY_MODEL", "GLM-4.6V-FlashX")
    cheap = usage.estimate_task_cost(8)
    monkeypatch.setattr(core_config, "VISION_CLASSIFY_MODEL", "deepseek-flash")
    expensive = usage.estimate_task_cost(8)
    assert cheap < expensive, "GLM-4.6V-FlashX(0.5) 必须比 deepseek-flash(2.0) 便宜"


# ---------------------------------------------------------------------------
# 4. 与 core 的契约：真实 run() 上报的 payload 必须带对 images
# ---------------------------------------------------------------------------


def _make_image(path, size=(400, 300)):
    Image.new("RGB", size, (200, 200, 200)).save(path, format="JPEG")
    return path


@pytest.fixture(autouse=True)
def _isolate_dirs(tmp_path_factory, monkeypatch):
    """OCR 归档目录重定向到临时目录，避免在用户真实目录里堆测试产物。"""
    monkeypatch.setattr(core_pipeline.config, "OCR_DIR", tmp_path_factory.mktemp("ocr"))


def _run_pipeline(tmp_path, monkeypatch, *, combined=None, pages=None):
    """跑一次真实 run()（视觉/求解打桩），返回收集到的 usage 事件。"""
    events: list[dict] = []
    images = [_make_image(tmp_path / "p1.jpg"), _make_image(tmp_path / "p2.jpg")]

    def fake_combined(image_paths, provider=None):
        return combined

    def fake_parallel(image_paths, provider=None):
        return "MULTIPLE_CHOICE", pages or ["1、第一题", "2、第二题"], []

    def fake_stream_solve(prompt, provider, model, enable_thinking=True, **kwargs):
        yield {"type": "content", "content": "## 最终答案\n选 B。"}

    monkeypatch.setattr(core_pipeline.vision_client, "classify_and_transcribe", fake_combined)
    monkeypatch.setattr(core_pipeline.vision_client, "classify_and_transcribe_parallel", fake_parallel)
    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", fake_stream_solve)
    monkeypatch.setattr(core_pipeline.solver_client, "ask_for_analysis", lambda *a, **k: "标题")

    pipeline = core_pipeline.SolutionPipeline(
        solution_dir=tmp_path / "solutions",
        on_event=events.append,
        write_failure_log=False,
    )
    pipeline.run("t-usage", images)
    return [event for event in events if event.get("type") == "usage"]


def test_run_reports_images_for_the_combined_path(tmp_path, monkeypatch):
    """合并路径：一条 `vision` 用量事件，`calls=1` 且 `images` == 图片数。"""
    events = _run_pipeline(
        tmp_path, monkeypatch,
        combined={"problem_type": "MULTIPLE_CHOICE", "pages": ["1、第一题", "2、第二题"]},
    )
    vision = [event for event in events if event["stage"] == "vision"]
    assert len(vision) == 1
    assert vision[0]["calls"] == 1
    assert vision[0]["images"] == 2, "合并路径必须上报真实上传的图片数"


def test_run_reports_images_for_the_parallel_path(tmp_path, monkeypatch):
    """回退路径：`ocr` 事件的 `images` 也是图片数（不是 `calls × pages`）。"""
    events = _run_pipeline(tmp_path, monkeypatch, combined=None)
    stages = {event["stage"]: event for event in events}
    assert stages["classify"]["images"] == 2
    assert stages["ocr"]["images"] == 2
    assert stages["ocr"]["calls"] == 2, "2 张图 = 2 次 OCR 请求"


def test_run_text_only_stages_report_zero_images(tmp_path, monkeypatch):
    """求解与文件名（若调了模型）都是纯文本调用，`images` 必须显式等于 0。"""
    events = _run_pipeline(
        tmp_path, monkeypatch,
        combined={"problem_type": "MULTIPLE_CHOICE", "pages": ["1、第一题", "2、第二题"]},
    )
    solve = [event for event in events if event["stage"] == "solve"]
    assert solve and solve[0]["images"] == 0
    for event in events:
        if event["stage"] in ("polish", "filename", "resolve"):
            assert event["images"] == 0, f"{event['stage']} 是纯文本调用，不该有图片 token"
        else:
            assert event["images"] is not None, f"{event['stage']} 必须上报 images"


def test_usage_event_payload_is_json_serialisable(tmp_path, monkeypatch):
    """`usage` 事件会经 SSE 与 JSON 往返，`images` 字段必须可序列化。"""
    import json

    events = _run_pipeline(tmp_path, monkeypatch, combined=None)
    assert json.loads(json.dumps(events)) == events
