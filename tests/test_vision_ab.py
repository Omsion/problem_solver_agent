"""
test_vision_ab.py - A/B 闸门工具的自回归保护（F2 / F3 / F9 / F11 / F12）

**为什么这些用例必须存在**：`tools/vision_ab.py` 是"GLM-4.6V → deepseek-flash"迁移的
唯一判据（迁移文档 §7 / §8.2）。闸门工具本身出假 PASS 比没有闸门更危险 —— 它会带着
"✅ 满足验收标准"的结论把人送进切换。本文件锁定的正是两类假 PASS：

1. **F2**：`ok` 曾被无条件写成 True。所有视觉调用都返回 None（一次都没成功）时，
   单 provider 模式下两条硬闸门都标"不适用"，报告照样打印"✅ 满足验收标准"，
   而转录其实是空的；`--reference` 基准全空时"要素缺失 0"也是"没得比"而非"没缺"。
2. **F3**：截断页数曾被换算成"空页计数"。转录在**最后一页中间**被 max_tokens 切断时
   每一页都有文字 → 截断页数 0 → 闸门 PASS，而这正是最典型的截断形态。

另外锁定 F9（兜底单价表必须与生产一致，否则成本被低估约 4 倍）、F11（删除没人读的
模块级 `COMBINED_TIMEOUT`）、F12（`_resolve_provider` 的返回注解），以及"工具只写自己的
`_probe/vision_ab/<stamp>/`，不动生产状态（env / config / DB / uploads / cache）"。

**全程零网络**：视觉调用全部打桩在 `vision_client._call_vision_api` /
`classify_and_transcribe` / `classify_and_transcribe_parallel` 上；仓库 `.env` 里即使有
真实密钥也用不到（`config._vision_api_key` 在用例里被替换成假值）。

运行：py -3.10 -m pytest tests/test_vision_ab.py -v
"""

from __future__ import annotations

import argparse
import os
import sys
import typing
from pathlib import Path

import pytest

from problem_solver_agent import config, vision_client
from tools import vision_ab


# ---------------------------------------------------------------------------
# 夹具与打桩
# ---------------------------------------------------------------------------


def _stub_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """给一个"已配置"的假密钥。

    不这么做的话 `run_provider` 会在密钥检查处提前返回（错误原文=缺少环境变量），
    被测的"有没有可用输出"分支根本走不到，用例会退化成永远通过的假绿。
    """
    monkeypatch.setattr(config, "_vision_api_key", lambda provider=None: "test-key-not-real")


def _stub_all_vision_calls_return_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟"一次视觉调用都没成功"：所有 `_call_vision_api` 调用返回 None。

    打桩在**最底层**的 `_call_vision_api` 上，这样被测的是生产函数自己的回退链
    （PAGE 协议 → JSON 协议 → 并行路径），而不是我们伪造出来的返回值。
    """
    def _return_none(*_args, **_kwargs):
        return None

    monkeypatch.setattr(vision_client, "_call_vision_api", _return_none)


def _write_fake_images(directory: Path, count: int) -> list[Path]:
    """写几个最小的 JPEG 占位文件。

    工具只需要"能读扩展名 + 能读出拍摄时间"：EXIF 读不到会退到文件名时间戳 / mtime，
    所以不需要真的图片，也就不需要 PIL 往返，更不会触发任何 API 调用。
    """
    directory.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    for index in range(count):
        path = directory / f"IMG_2026091{index + 1}_12000{index}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xd9")
        images.append(path)
    return images


def _fake_run(
    pages: list[str],
    *,
    ok: bool = True,
    path: str = "classify_and_transcribe",
    problem_type: str = "MULTIPLE_CHOICE",
    finish_reason: str | None = "stop(协议推断)",
    truncation_signal: str = "protocol",
    truncated_pages: int = 0,
    failed_pages: list[int] | None = None,
    corruption_count: int = 0,
    elapsed: float = 1.0,
) -> dict[str, typing.Any]:
    """手搓一个 `_build_verdict` 能吃的 run 字典（只包含判定用到的字段）。

    为什么要手搓而不是都走 `run_provider`：判定逻辑的边界（空基准、并行 unknown 信号）
    用真实调用不好构造，直接构造 run 更能精确锁定 verdict 的行为。
    """
    return {
        "ok": ok,
        "path": path,
        "vision_mode": "combined",
        "problem_type": problem_type,
        "pages": list(pages),
        "failed_pages": list(failed_pages or []),
        "elapsed": elapsed,
        "finish_reason": finish_reason,
        "finish_reason_basis": "用例构造",
        "truncation_signal": truncation_signal,
        "truncated": bool(truncated_pages),
        "truncated_pages": truncated_pages,
        "corruption_count": corruption_count,
        "corrupt_pages": [],
        "usable_pages": sum(1 for page in pages if page.strip()),
        "error": "" if ok else "未产出任何可用内容",
    }


# ---------------------------------------------------------------------------
# F2：没有任何可用输出时，绝不可能是 PASS
# ---------------------------------------------------------------------------


def test_f2_all_calls_return_none_is_never_a_pass(tmp_path, monkeypatch):
    """F2 的原始复现：所有调用返回 None → 旧实现 `ok=True` → 单 provider 假 PASS。

    断言三件事：run 不算成功（带明确原因）、verdict 不通过、报告正文不出现"✅ 满足"。
    """
    _stub_api_key(monkeypatch)
    _stub_all_vision_calls_return_none(monkeypatch)
    images = _write_fake_images(tmp_path / "input", 2)

    payload = vision_ab.build_payload(images, ["deepseek"], None)
    run = payload["runs"]["deepseek"]

    assert run["ok"] is False
    assert "未产出任何可用内容" in run["error"]  # 必须明确说清"为什么不算成功"
    assert run["usable_pages"] == 0
    # 确认打桩确实走到了"并行回退路径"—— 旧实现就是在这里无条件写 ok=True 的
    assert run["path"] == "classify_and_transcribe_parallel"

    verdict = vision_ab._build_verdict(payload["runs"], ["deepseek"], None, {})
    assert verdict["pass"] is False

    ok_item = next(i for i in verdict["items"] if i["label"] == "provider 有可用输出")
    assert ok_item["mark"] == "❌"
    assert "deepseek" in ok_item["detail"]

    report = vision_ab.render_report(payload, {}, verdict, {"tail_head": True})
    assert "❌ 不满足" in report
    assert "✅ 满足" not in report


def test_f2_any_provider_without_output_fails_the_verdict():
    """双 provider 里只要有一家没有可用输出，整体结论就必须失败。

    尤其要保证这家不会被写成"调用成功"（旧实现的 ⑤ 只列出名字，读起来像"都还行，
    只是这家有点问题"）。
    """
    runs = {
        "deepseek": _fake_run(["第 1 页正文", "第 2 页正文"]),
        "zhipu": _fake_run(
            ["", ""],
            ok=False,
            path="classify_and_transcribe_parallel",
            problem_type="",
            finish_reason=None,
            truncation_signal="unknown",
        ),
    }
    comparisons = {
        "zhipu": vision_ab.compare_pages(runs["deepseek"]["pages"], runs["zhipu"]["pages"]),
    }
    verdict = vision_ab._build_verdict(runs, ["deepseek", "zhipu"], "deepseek", comparisons)

    assert verdict["pass"] is False
    item = next(i for i in verdict["items"] if i["label"] == "provider 有可用输出")
    assert item["mark"] == "❌"
    assert "zhipu" in item["detail"]
    assert "无可用输出" in item["detail"] or "未产出" in item["detail"]


def test_f2_verdict_also_rejects_ok_true_with_empty_pages():
    """纵深防御：即使将来有人把 `ok` 又改回无条件 True，只要页全空，verdict 仍必须失败。

    旧实现的 `run_provider` 产出的正是这个形状（`ok=True` + pages 全空）。这条用例把
    它钉在判定层，而不是只依赖"run_provider 别再犯错"。
    """
    runs = {"deepseek": _fake_run([""] * 2, ok=True, problem_type="GENERAL")}
    verdict = vision_ab._build_verdict(runs, ["deepseek"], None, {})

    assert verdict["pass"] is False
    item = next(i for i in verdict["items"] if i["label"] == "provider 有可用输出")
    assert item["mark"] == "❌"
    assert "0 页非空" in item["detail"]


def test_f2_missing_problem_type_fails_even_in_single_provider_mode():
    """题型为空 = 分类输出缺失，单 provider 模式下也不能标"不适用"混过去（F2）。

    生产链路上 `refill_pages` 保证题型非空（最差 GENERAL），所以真出现空题型就说明
    这次调用不完整 —— 与"零可用页"同一类问题，不能算满足。
    """
    runs = {"deepseek": _fake_run(["第 1 页正文"], problem_type="")}
    verdict = vision_ab._build_verdict(runs, ["deepseek"], None, {})

    assert verdict["pass"] is False
    item = next(i for i in verdict["items"] if i["label"] == "题型分类一致率")
    assert item["mark"] == "❌"
    assert "题型为空" in item["detail"]


def test_f2_empty_reference_baseline_is_not_reported_as_zero_missing():
    """空的 `--reference` 基准下，"要素缺失 0"只是没得比，不能打 ✅（F2 第三条）。

    构造：基准 zhipu 全空、候选 deepseek 有正文 → `compare_pages` 的 missing_count
    必然为 0。旧实现会打印"要素缺失 0 个，满足「必须为 0」" —— 这是用空基准伪造 PASS。
    """
    runs = {
        "zhipu": _fake_run(
            [""],
            ok=False,
            path="classify_and_transcribe_parallel",
            problem_type="",
            finish_reason=None,
            truncation_signal="unknown",
        ),
        "deepseek": _fake_run(["题目：1+1=2"]),
    }
    comparisons = {"deepseek": vision_ab.compare_pages([""], ["题目：1+1=2"])}
    # 前提成立：空基准下候选的"缺失数"确实是 0（这正是旧实现打勾的原因）
    assert comparisons["deepseek"]["missing_count"] == 0

    verdict = vision_ab._build_verdict(runs, ["zhipu", "deepseek"], "zhipu", comparisons)

    assert verdict["pass"] is False
    item = next(i for i in verdict["items"] if i["label"].startswith("要素缺失"))
    assert item["mark"] == "❌"
    assert "无法判定" in item["detail"]
    assert "缺失 0 个" not in item["detail"]


# ---------------------------------------------------------------------------
# F3：截断闸门必须能被"缺 <<<END>>>"触发
# ---------------------------------------------------------------------------


def test_f3_missing_end_marker_fails_truncation_gate(tmp_path, monkeypatch):
    """每一页都有文字但响应缺 `<<<END>>>` → 必须计为被截断、闸门必须失败。

    这就是 F3 描述的形态：转录被从最后一页中间切断，空页计数为 0，旧实现据此 PASS。
    """
    _stub_api_key(monkeypatch)
    pages = ["第一页：\\frac{1}{2}", "第二页正文（最后一页被切断）"]

    def fake_combined(image_paths, provider=None):
        return {
            "problem_type": "MULTIPLE_CHOICE",
            "pages": list(pages),
            "failed_pages": [],
            "continuations": [False, False],
            "vision_mode": "combined",
            "ended": False,  # 协议级截断信号
            "refilled": 0,
        }

    monkeypatch.setattr(vision_client, "classify_and_transcribe", fake_combined)
    payload = vision_ab.build_payload(_write_fake_images(tmp_path / "input", 2), ["deepseek"], None)
    run = payload["runs"]["deepseek"]

    assert run["ok"] is True  # 有可用输出（所以 F2 不会替这次失败兜底）
    assert run["truncation_signal"] == "protocol"
    assert run["truncated_pages"] == 1  # 关键：非空页也要算成"被截断"（旧实现是 0）
    assert run["truncated"] is True
    assert run["finish_reason"] == "length(协议推断)"

    verdict = vision_ab._build_verdict(payload["runs"], ["deepseek"], None, {})
    assert verdict["pass"] is False
    item = next(i for i in verdict["items"] if "截断" in i["label"])
    assert item["mark"] == "❌"

    report = vision_ab.render_report(payload, {}, verdict, {"tail_head": True})
    assert "判定为被截断" in report


def test_f3_parallel_unknown_truncation_signal_never_passes(tmp_path, monkeypatch):
    """并行回退路径没有 finish_reason / `<<<END>>>`：空页代理为 0 也不能判满足（F3）。

    旧实现会把"截断页数 = 0"直接当成闸门通过，还会附一条"只能间接判断"的注解 ——
    注解不能替代结论，这里要求它明确写"无法判定"并让整体结论失败。
    """
    _stub_api_key(monkeypatch)
    monkeypatch.setattr(vision_client, "classify_and_transcribe", lambda *a, **k: None)

    def fake_parallel(image_paths):
        return "MULTIPLE_CHOICE", ["第 1 页正文", "第 2 页正文"], []

    monkeypatch.setattr(vision_client, "classify_and_transcribe_parallel", fake_parallel)
    run = vision_ab.enrich(
        vision_ab.run_provider("deepseek", _write_fake_images(tmp_path / "input", 2))
    )

    assert run["ok"] is True
    assert run["finish_reason"] is None
    assert run["truncation_signal"] == "unknown"
    assert run["truncated_pages"] == 0  # 空页代理为 0 ...

    verdict = vision_ab._build_verdict({"deepseek": run}, ["deepseek"], None, {})
    assert verdict["pass"] is False  # ... 但结论不能因此算满足
    item = next(i for i in verdict["items"] if "截断" in i["label"])
    assert item["mark"] == "❌"
    assert "无法判定" in item["detail"]


# ---------------------------------------------------------------------------
# 反向用例：真有输出、没截断、没损坏时，结论必须仍然是 ✅
# ---------------------------------------------------------------------------


def test_single_provider_with_real_output_still_passes(tmp_path, monkeypatch):
    """没有这条，F2/F3 的修复很容易被做成"永远失败"，工具同样会失去判据价值。"""
    _stub_api_key(monkeypatch)

    def fake_combined(image_paths, provider=None):
        return {
            "problem_type": "MULTIPLE_CHOICE",
            "pages": ["题干：$\\frac{1}{2}$ 等于多少？", "A. 0.5　B. 1"],
            "failed_pages": [],
            "continuations": [False, False],
            "vision_mode": "combined",
            "ended": True,  # 有 <<<END>>>：协议级信号说"没被截断"
            "refilled": 0,
        }

    monkeypatch.setattr(vision_client, "classify_and_transcribe", fake_combined)
    payload = vision_ab.build_payload(
        _write_fake_images(tmp_path / "input", 2), ["deepseek"], None
    )
    run = payload["runs"]["deepseek"]

    assert run["ok"] is True
    assert run["truncated_pages"] == 0
    assert run["corruption_count"] == 0

    verdict = vision_ab._build_verdict(payload["runs"], ["deepseek"], None, {})
    assert verdict["pass"] is True
    report = vision_ab.render_report(payload, {}, verdict, {"tail_head": True})
    assert "✅ 满足" in report
    assert "✅ 未发现控制字符或落转义残片" in report


def test_dual_provider_healthy_run_reaches_a_pass(tmp_path, monkeypatch):
    """双 provider 一致、要素无缺失、无截断/无损坏 → 结论 ✅（闸门不是只会红）。"""
    _stub_api_key(monkeypatch)
    pages = ["题干：$1+1=2$", "A. 2　B. 3"]

    def fake_combined(image_paths, provider=None):
        return {
            "problem_type": "MULTIPLE_CHOICE",
            "pages": list(pages),
            "failed_pages": [],
            "continuations": [False, False],
            "vision_mode": "combined",
            "ended": True,
            "refilled": 0,
        }

    monkeypatch.setattr(vision_client, "classify_and_transcribe", fake_combined)
    providers = ["zhipu", "deepseek"]
    reference = "zhipu"
    payload = vision_ab.build_payload(
        _write_fake_images(tmp_path / "input", 2), providers, reference
    )
    # 与 cmd_check 一样：基准之外的每家算一次对比
    comparisons = {
        name: vision_ab.compare_pages(payload["runs"][reference]["pages"], payload["runs"][name]["pages"])
        for name in providers if name != reference
    }
    verdict = vision_ab._build_verdict(payload["runs"], providers, reference, comparisons)

    assert comparisons["deepseek"]["missing_count"] == 0
    assert verdict["pass"] is True
    for item in verdict["items"]:
        assert item["mark"] == "✅", item


# ---------------------------------------------------------------------------
# F9：兜底单价表必须与生产表一致（且是迁移后的价）
# ---------------------------------------------------------------------------


def test_f9_fallback_cost_table_matches_production_table():
    """兜底表与 `webapp/accounts.COST_TABLE` 逐项一致 —— 单价再变时这里先红。"""
    from webapp.accounts import COST_TABLE

    assert vision_ab._FALLBACK_COST_TABLE == {
        name: (float(in_price), float(out_price))
        for name, (in_price, out_price) in COST_TABLE.items()
    }


def test_f9_fallback_prices_are_post_migration(monkeypatch):
    """`webapp.accounts` 导不进来时走兜底表，价格必须是迁移后的（不能被低估约 4 倍）。"""
    # sys.modules 里放 None → `from webapp.accounts import ...` 抛 ImportError
    monkeypatch.setitem(sys.modules, "webapp.accounts", None)

    # deepseek-flash 高峰价 2/8 元每百万 token（迁移前兜底表是 0.5/2.0 → 这里会是 2.5）
    assert vision_ab.estimate_cost("deepseek-flash", 1_000_000, 1_000_000) == pytest.approx(10.0)
    # deepseek-v4-pro 9/27
    assert vision_ab.estimate_cost("deepseek-v4-pro", 1_000_000, 1_000_000) == pytest.approx(36.0)
    # 未知模型走 default（2/8）
    assert vision_ab.estimate_cost("some-unknown-model", 1_000_000, 1_000_000) == pytest.approx(10.0)
    # 报告里必须标注"用的是兜底表"，否则会被当成生产单价
    assert "兜底" in vision_ab.cost_table_source()


# ---------------------------------------------------------------------------
# F11 / F12：死常量与错误的返回注解
# ---------------------------------------------------------------------------


def test_f11_dead_combined_timeout_constant_is_gone():
    """F11：模块级 `COMBINED_TIMEOUT` 没有任何读点 —— 删掉，免得有人以为改它有效。"""
    assert not hasattr(vision_ab, "COMBINED_TIMEOUT")


def test_f12_resolve_provider_annotation_and_behaviour():
    """F12：注解必须是 `str`（不是 `tuple[str, bool]`），且未知名字要显式报错。"""
    assert typing.get_type_hints(vision_ab._resolve_provider)["return"] is str
    assert vision_ab._resolve_provider(" DeepSeek ") == "deepseek"
    with pytest.raises(argparse.ArgumentTypeError):
        vision_ab._resolve_provider("gpt-vision")


# ---------------------------------------------------------------------------
# 只读保证：除了自己的报告目录，不碰任何生产状态
# ---------------------------------------------------------------------------


def test_tool_is_read_only_outside_its_own_report_dir(tmp_path, monkeypatch):
    """工具只写 `REPORT_ROOT/<stamp>/`；env / config / DB / uploads / cache 一律不动。"""
    _stub_api_key(monkeypatch)
    _stub_all_vision_calls_return_none(monkeypatch)
    input_dir = tmp_path / "input"
    images = _write_fake_images(input_dir, 1)

    watched = (
        "VISION_PROVIDER",
        "VISION_CLASSIFY_MODEL",
        "VISION_MAX_TOKENS",
        "VISION_COMBINED_TIMEOUT",
        "VISION_DISABLE_THINKING",
        "USE_COMBINED_VISION_CALL",
        "COMBINED_VISION_MAX_IMAGES",
        "OCR_PARALLEL_WORKERS",
        "FILENAME_MODE",
        "IMAGE_CACHE_DIR",
    )
    env_before = dict(os.environ)
    config_before = {key: getattr(config, key) for key in watched}

    payload = vision_ab.build_payload(images, ["deepseek"], None)
    verdict = vision_ab._build_verdict(payload["runs"], ["deepseek"], None, {})

    report_root = tmp_path / "ab_reports"
    monkeypatch.setattr(vision_ab, "REPORT_ROOT", report_root)
    out_dir = vision_ab.write_outputs(payload, {}, verdict, {"tail_head": True})

    # 1) 生产 config 与环境变量一个都没被改
    assert {key: getattr(config, key) for key in watched} == config_before
    assert dict(os.environ) == env_before

    # 2) 所有落盘文件都在自己的报告目录里（input_dir 是测试自己写的输入图）
    assert (out_dir / "report.md").is_file() and (out_dir / "result.json").is_file()
    assert out_dir.parent == report_root
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.parent != input_dir:
            assert out_dir in path.parents, f"越界写入: {path}"

    # 3) 生产目录（uploads / solutions / data(DB) / stage_cache / 图片缓存）没被创建
    for forbidden in ("uploads", "solutions", "data", "stage_cache", "image_cache"):
        assert not (tmp_path / forbidden).exists(), f"工具动了生产目录: {forbidden}"
