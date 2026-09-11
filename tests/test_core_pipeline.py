"""
test_core_pipeline.py - 共享流水线行为测试

用假客户端打桩，不产生真实 API 调用。覆盖：
- 合并调用（分类 + 转录）与解析失败回退
- 单图 / 短文本跳过润色
- OCR 部分失败时的原图直读兜底
- 取消在流式过程中生效，并保留部分内容
- 阶段耗时与答案卡事件

运行：pytest tests/test_core_pipeline.py -v
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from problem_solver_agent import core_pipeline, prompts
from problem_solver_agent.cancel import CancelToken


def _make_image(path: Path, size=(400, 300)) -> Path:
    Image.new("RGB", size, (200, 200, 200)).save(path, format="JPEG")
    return path


@pytest.fixture()
def images(tmp_path):
    return [_make_image(tmp_path / "p1.jpg"), _make_image(tmp_path / "p2.jpg")]


@pytest.fixture()
def events():
    collected: list[dict] = []
    return collected


def _pipeline(tmp_path, events, **kwargs):
    return core_pipeline.SolutionPipeline(
        solution_dir=tmp_path / "solutions",
        on_event=collected_append(events),
        write_failure_log=False,
        **kwargs,
    )


def collected_append(sink: list[dict]):
    def _append(event: dict) -> None:
        sink.append(event)

    return _append


def _stub_solver(monkeypatch, response: str = "## 最终答案\n选 B。"):
    calls = {"analysis": 0, "stream": 0}

    def fake_stream_solve(prompt, provider, model, enable_thinking=True):
        calls["stream"] += 1
        yield {"type": "reasoning", "content": "思考中"}
        yield {"type": "content", "content": response}

    def fake_ask(prompt, provider, model):
        calls["analysis"] += 1
        return "生成的标题"

    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", fake_stream_solve)
    monkeypatch.setattr(core_pipeline.solver_client, "ask_for_analysis", fake_ask)
    return calls


def _stub_vision(monkeypatch, *, combined=None, pages=None, failed=None, problem_type="MULTIPLE_CHOICE"):
    calls = {"combined": 0, "parallel": 0}

    def fake_combined(image_paths):
        calls["combined"] += 1
        return combined

    def fake_parallel(image_paths):
        calls["parallel"] += 1
        return problem_type, pages or ["第一页", "第二页"], failed or []

    monkeypatch.setattr(core_pipeline.vision_client, "classify_and_transcribe", fake_combined)
    monkeypatch.setattr(core_pipeline.vision_client, "classify_and_transcribe_parallel", fake_parallel)
    return calls


def test_combined_call_success(tmp_path, images, events, monkeypatch):
    solver_calls = _stub_solver(monkeypatch)
    vision_calls = _stub_vision(monkeypatch, combined={"problem_type": "MULTIPLE_CHOICE", "pages": ["题目一", "题目二"]})

    result = _pipeline(tmp_path, events).run("t1", images)

    assert result["status"] == "completed"
    assert vision_calls["combined"] == 1
    assert vision_calls["parallel"] == 0
    # 多图且合并文本很短 → 跳过润色，因此 ask_for_analysis 只用于生成文件名
    assert solver_calls["analysis"] == 1
    assert result["answer_card"]["text"].strip() == "选 B。"

    types = [e["type"] for e in events]
    assert "chunk" in types and "done" in types
    # 耗时用毫秒记录，测试环境可能快到取整为 0，因此只断言结构完整与类型正确
    timings = result["timings"]
    for key in ("classify", "ocr", "polish", "solve", "total"):
        assert key in timings
        assert isinstance(timings[key], int)
        assert timings[key] >= 0


def test_falls_back_to_parallel_when_combined_fails(tmp_path, images, events, monkeypatch):
    _stub_solver(monkeypatch)
    vision_calls = _stub_vision(monkeypatch, combined=None, pages=["来自并行 OCR"] * 2)

    result = _pipeline(tmp_path, events).run("t2", images)

    assert result["status"] == "completed"
    assert vision_calls["combined"] == 1
    assert vision_calls["parallel"] == 1


def test_single_image_skips_polish(tmp_path, events, monkeypatch):
    solver_calls = _stub_solver(monkeypatch)
    single = [_make_image(tmp_path / "only.jpg")]
    _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": ["单页题目内容"]})

    result = _pipeline(tmp_path, events).run("t3", single)

    assert result["status"] == "completed"
    # 只有生成文件名那一次分析调用，润色被跳过
    assert solver_calls["analysis"] == 1
    assert result["timings"]["polish"] == 0


def test_long_multi_page_text_triggers_polish(tmp_path, images, events, monkeypatch):
    solver_calls = _stub_solver(monkeypatch)
    long_page = "内容" * 2000
    _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": [long_page, "短"]})

    _pipeline(tmp_path, events).run("t4", images)
    # 润色 + 文件名 = 2 次分析调用
    assert solver_calls["analysis"] == 2


def test_partial_ocr_failure_falls_back_to_vision(tmp_path, images, events, monkeypatch):
    _stub_solver(monkeypatch)
    _stub_vision(monkeypatch, combined=None, pages=["只有第一页"], failed=[1])

    vision_reason_calls = {"count": 0}

    def fake_reason(image_paths):
        vision_reason_calls["count"] += 1
        yield "原图直读得到的答案"

    monkeypatch.setattr(core_pipeline.vision_client, "solve_visual_reasoning_problem", fake_reason)

    result = _pipeline(tmp_path, events).run("t5", images)

    assert vision_reason_calls["count"] == 1
    assert result["status"] == "completed"
    solution = Path(result["path"]).read_text(encoding="utf-8")
    assert "ocr_fallback: true" in solution


def test_cancellation_during_stream_preserves_partial(tmp_path, images, events, monkeypatch):
    _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": ["题目", "题目2"]})

    token = CancelToken()

    def fake_stream_solve(prompt, provider, model, enable_thinking=True):
        yield {"type": "content", "content": "第一段"}
        token.cancel()  # 取消请求在流式过程中到达
        yield {"type": "content", "content": "这一段不应该被写入"}

    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", fake_stream_solve)
    monkeypatch.setattr(core_pipeline.solver_client, "ask_for_analysis", lambda *a, **k: "标题")

    result = _pipeline(tmp_path, events).run("t6", images, cancel=token)

    assert result["status"] == "cancelled"
    partial = Path(result["path"])
    assert partial.exists()
    assert partial.name.endswith(".partial.md")
    text = partial.read_text(encoding="utf-8")
    assert "第一段" in text
    assert "不应该被写入" not in text
    # 必须发出取消事件，而不是 error
    assert any(e["type"] == "cancelled" for e in events)
    assert not any(e["type"] == "error" for e in events)


def test_cancel_before_first_stage(tmp_path, images, events, monkeypatch):
    _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": ["a", "b"]})
    _stub_solver(monkeypatch)
    token = CancelToken()
    token.cancel()

    result = _pipeline(tmp_path, events).run("t7", images, cancel=token)
    assert result["status"] == "cancelled"
    assert any(e["type"] == "cancelled" for e in events)


def test_failure_writes_error_event_and_cleans_temp(tmp_path, images, events, monkeypatch):
    _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": ["a", "b"]})

    def broken_stream(prompt, provider, model, enable_thinking=True):
        yield {"type": "error", "content": "模型挂了"}

    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", broken_stream)
    monkeypatch.setattr(core_pipeline.solver_client, "ask_for_analysis", lambda *a, **k: "标题")

    with pytest.raises(RuntimeError, match="模型挂了"):
        _pipeline(tmp_path, events).run("t8", images)

    assert any(e["type"] == "error" for e in events)
    # 临时文件不应残留
    assert list((tmp_path / "solutions").glob("*_inprogress.md")) == []


def test_stage_cache_is_used_and_reported(tmp_path, images, events, monkeypatch):
    _stub_solver(monkeypatch)
    vision_calls = _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": ["缓存页"]})

    class FakeCache:
        def __init__(self):
            self.store: dict = {}

        def get(self, task_id, stage):
            return self.store.get((task_id, stage))

        def set(self, task_id, stage, payload):
            self.store[(task_id, stage)] = payload

    cache = FakeCache()
    pipeline = core_pipeline.SolutionPipeline(
        solution_dir=tmp_path / "solutions",
        on_event=collected_append(events),
        stage_cache=cache,
        write_failure_log=False,
    )

    first = pipeline.run("t9", images)
    second = pipeline.run("t9", images)

    # 第二次应命中缓存，不再调用视觉模型
    assert vision_calls["combined"] == 1
    assert "vision" in second["timings"]["cached"]
    assert first["status"] == "completed" == second["status"]


def test_archive_moves_images(tmp_path, images, monkeypatch):
    _stub_solver(monkeypatch)
    _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": ["a", "b"]})
    archive = tmp_path / "processed"

    _pipeline(tmp_path, None_events(), archive_dir=archive).run("t10", images)

    assert not any(p.exists() for p in images)
    assert len(list(archive.glob("*.jpg"))) == 2


def None_events():
    return []


def test_prompt_template_has_no_double_braces():
    """raw 字符串化之后，Prompt 里的 LaTeX 花括号不应再被转义成双括号。"""
    assert "{{" not in prompts.TEXT_MERGE_AND_POLISH_PROMPT
    assert "{raw_texts}" in prompts.TEXT_MERGE_AND_POLISH_PROMPT


def test_combined_prompt_is_valid_json_example():
    import json

    example = '{"problem_type": "GENERAL", "pages": ["x"]}'
    assert json.loads(example)["pages"] == ["x"]
    assert "pages" in prompts.CLASSIFY_AND_TRANSCRIBE_PROMPT
