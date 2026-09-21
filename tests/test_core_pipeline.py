"""
test_core_pipeline.py - 共享流水线行为测试

用假客户端打桩，不产生真实 API 调用。覆盖：
- 合并调用（分类 + 转录）与解析失败回退
- 单图 / 短文本跳过润色
- OCR 部分失败时的原图直读兜底
- 取消在流式过程中生效，并保留部分内容
- 阶段耗时与答案卡事件
- 组 H/I：合并路径内联拼接（S9）、阶段缓存的模型名比对、OCR 归档（S11）、
  求解首行 FILE: 建议的剥离与三档文件名生成（T4）

运行：pytest tests/test_core_pipeline.py -v
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from PIL import Image

from problem_solver_agent import config, core_pipeline, prompts
from problem_solver_agent.cancel import CancelToken


def _capture_agent_log(logger_name: str = "CorePipeline") -> tuple[list[logging.LogRecord], logging.Handler]:
    """抓取指定 logger 的日志（core_pipeline 的是 "CorePipeline"，它自身的 handler 会
    把记录打到 stderr；这里挂自己的 handler 以便断言文案）。"""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logging.getLogger(logger_name).addHandler(handler)
    return records, handler


def _make_image(path: Path, size=(400, 300)) -> Path:
    Image.new("RGB", size, (200, 200, 200)).save(path, format="JPEG")
    return path


@pytest.fixture(autouse=True)
def _isolate_ocr_dir(tmp_path_factory, monkeypatch):
    """把 OCR 归档目录重定向到临时目录。

    组 I 之后每次 run() 都会往 `config.OCR_DIR`（默认是用户真实图片根目录下的 ocr/）
    落盘一份归档；不隔离开的话，跑一次测试就会在用户目录里堆一批 t1.md / t2.md。
    """
    monkeypatch.setattr(core_pipeline.config, "OCR_DIR", tmp_path_factory.mktemp("ocr"))


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


def _stub_solver(monkeypatch, response: str = "## 最终答案\n选 B。", analysis: str = "生成的标题"):
    calls = {"analysis": 0, "stream": 0, "prompts": []}

    def fake_stream_solve(prompt, provider, model, enable_thinking=True, **kwargs):
        calls["stream"] += 1
        yield {"type": "reasoning", "content": "思考中"}
        yield {"type": "content", "content": response}

    def fake_ask(prompt, provider, model):
        calls["analysis"] += 1
        calls["prompts"].append(prompt)
        return analysis

    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", fake_stream_solve)
    monkeypatch.setattr(core_pipeline.solver_client, "ask_for_analysis", fake_ask)
    return calls


# 润色 prompt 的首段（占位符之前的部分），用来把"润色调用"和"文件名生成调用"分开计数
_POLISH_PROMPT_HEAD = prompts.TEXT_MERGE_AND_POLISH_PROMPT.split("{raw_texts}")[0]


def _polish_calls(calls: dict) -> int:
    return sum(1 for prompt in calls["prompts"] if prompt.startswith(_POLISH_PROMPT_HEAD))


def _stub_vision(
    monkeypatch,
    *,
    combined=None,
    pages=None,
    failed=None,
    problem_type="MULTIPLE_CHOICE",
    vision_mode=None,
    continuations=None,
):
    """打桩分类 + 转录。

    `vision_mode` / `continuations` 缺省（None）时**不往 dict 里塞**这两个键，
    以模拟旧假数据 —— 契约规定缺失即按 "parallel" 处理（保守：保留润色）。
    """
    calls = {"combined": 0, "parallel": 0, "providers": []}

    def fake_combined(image_paths, provider=None):
        calls["combined"] += 1
        calls["providers"].append(provider)
        if combined is None:
            return None
        result = dict(combined)
        if vision_mode is not None:
            result.setdefault("vision_mode", vision_mode)
        if continuations is not None:
            result.setdefault("continuations", continuations)
        return result

    def fake_parallel(image_paths, provider=None):
        calls["parallel"] += 1
        calls["providers"].append(provider)
        return problem_type, pages or ["第一页", "第二页"], failed or []

    monkeypatch.setattr(core_pipeline.vision_client, "classify_and_transcribe", fake_combined)
    monkeypatch.setattr(core_pipeline.vision_client, "classify_and_transcribe_parallel", fake_parallel)
    return calls


def test_combined_call_success(tmp_path, images, events, monkeypatch):
    solver_calls = _stub_solver(monkeypatch)
    # 假数据故意不带 vision_mode：契约规定缺失即按 "parallel" 处理（保守），
    # 因此这里仍然保留润色分支，只是合并文本太短而被跳过
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

    # 组 I 契约：run() 还要回传三个转录视图（供 webapp 落库）
    assert result["vision_mode"] == "parallel"      # 假数据没给 vision_mode
    assert result["problem_text"] == "题目一\n---[NEXT]---\n题目二"
    assert "题目一" in result["ocr_raw_text"] and "题目二" in result["ocr_raw_text"]


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
    """`vision_mode` 缺失 → 按 "parallel" 处理，因此并行路径**保留**润色调用。

    这条用例是组 H 的"保守性"锚点：只有合并路径（combined/json）且无失败页才允许
    跳过润色；旧缓存/旧假数据缺失该字段时不能当成合并结果去内联。
    """
    solver_calls = _stub_solver(monkeypatch)
    long_page = "内容" * 2000
    _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": [long_page, "短"]})

    result = _pipeline(tmp_path, events).run("t4", images)
    # 润色 + 文件名 = 2 次分析调用
    assert solver_calls["analysis"] == 2
    assert _polish_calls(solver_calls) == 1
    assert result["vision_mode"] == "parallel"


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

    def fake_stream_solve(prompt, provider, model, enable_thinking=True, **kwargs):
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

    def broken_stream(prompt, provider, model, enable_thinking=True, **kwargs):
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


def test_combined_prompt_uses_page_protocol_not_json():
    """合并调用的首选协议是 `<<<PAGE n|NEW/CONT>>>`。

    换协议的原因：JSON 字符串里的 LaTeX 反斜杠漏转义会产生**静默损坏**
    （`\\frac` → `␌rac`），而分隔符协议下正文逐字直出，不经过任何转义层。
    JSON 版降级为第二顺位回退，`pages` 字段名仍在它里面。
    """
    import json

    assert "<<<TYPE>>>" in prompts.CLASSIFY_AND_TRANSCRIBE_PROMPT
    assert "<<<PAGE 1|NEW>>>" in prompts.CLASSIFY_AND_TRANSCRIBE_PROMPT
    assert "<<<END>>>" in prompts.CLASSIFY_AND_TRANSCRIBE_PROMPT

    example = '{"problem_type": "GENERAL", "pages": ["x"]}'
    assert json.loads(example)["pages"] == ["x"]
    assert "pages" in prompts.CLASSIFY_AND_TRANSCRIBE_JSON_PROMPT


# ---------------------------------------------------------------------------
# 按需升级：首选档（不开思考）不合格 → 开思考 + 大配额重跑一次
# ---------------------------------------------------------------------------


def _stub_escalating_solver(
    monkeypatch,
    *,
    first_text: str = "",
    escalate_text: str = "## 最终答案\n升级档写出来的完整解答" * 40,
):
    """首选档返回 first_text，升级档返回 escalate_text；记录每次调用的参数。"""
    calls: list[dict] = []

    def fake_stream_solve(
        prompt, provider, model, enable_thinking=True, max_tokens=None, **kwargs
    ):
        calls.append({"thinking": enable_thinking, "max_tokens": max_tokens})
        text = escalate_text if enable_thinking else first_text
        if text:
            yield {"type": "content", "content": text}
            yield {
                "type": "meta",
                "thinking": enable_thinking,
                "finish_reason": "stop",
                "truncated": False,
                "content_chars": len(text),
                "reasoning_chars": 0,
            }
            return
        # 首选档一个字都没写出来：先给画像、再报错（与 solver_client 的行为一致）
        yield {
            "type": "meta",
            "thinking": enable_thinking,
            "finish_reason": "length",
            "truncated": True,
            "content_chars": 0,
            "reasoning_chars": 999,
        }
        yield {"type": "error", "content": "求解器没有返回正文"}

    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", fake_stream_solve)
    monkeypatch.setattr(core_pipeline.solver_client, "ask_for_analysis", lambda *a, **k: "标题")
    return calls


@pytest.fixture()
def _deterministic_thinking(monkeypatch):
    """测试不依赖用户 .env：首选档不开思考、允许升级。"""
    monkeypatch.setattr(core_pipeline.config, "SOLVER_THINKING_DEFAULT", False)
    monkeypatch.setattr(core_pipeline.config, "SOLVER_ESCALATE_TO_THINKING", True)


def test_escalates_to_thinking_when_preferred_attempt_is_empty(
    tmp_path, images, events, monkeypatch, _deterministic_thinking
):
    _stub_vision(monkeypatch, combined={"problem_type": "CODING", "pages": ["题目"]})
    calls = _stub_escalating_solver(monkeypatch)

    result = _pipeline(tmp_path, events).run("t11", images)

    assert result["status"] == "completed"
    assert "升级档写出来的完整解答" in result["text"]
    assert [call["thinking"] for call in calls] == [False, True]
    assert calls[0]["max_tokens"] is None  # 首选档用默认配额
    assert calls[1]["max_tokens"] == core_pipeline.config.SOLVER_ESCALATE_MAX_TOKENS


def test_keeps_first_answer_when_escalation_also_fails(
    tmp_path, images, events, monkeypatch, _deterministic_thinking
):
    _stub_vision(monkeypatch, combined={"problem_type": "CODING", "pages": ["题目"]})
    first = "首选档写出的短解答"
    calls = _stub_escalating_solver(monkeypatch, first_text=first, escalate_text="")

    result = _pipeline(tmp_path, events).run("t12", images)

    assert result["text"].strip() == first
    assert len(calls) == 2, "升级档也没写出正文时不该再跑第三次"


def test_short_answer_is_not_escalated_for_multiple_choice(
    tmp_path, images, events, monkeypatch, _deterministic_thinking
):
    """选择题答案天然很短（"选 B"），不能因为短就白跑一次思考。"""
    _stub_vision(monkeypatch, combined={"problem_type": "MULTIPLE_CHOICE", "pages": ["题目"]})
    calls = _stub_escalating_solver(monkeypatch, first_text="选 B")

    result = _pipeline(tmp_path, events).run("t13", images)

    assert result["text"].strip() == "选 B"
    assert len(calls) == 1


def test_explicit_thinking_request_skips_escalation(
    tmp_path, images, events, monkeypatch, _deterministic_thinking
):
    """显式要求思考时直接走思考档，不再多跑一遍。"""
    _stub_vision(monkeypatch, combined={"problem_type": "CODING", "pages": ["题目"]})
    calls = _stub_escalating_solver(monkeypatch)

    result = _pipeline(tmp_path, events).run("t14", images, enable_thinking=True)

    assert result["status"] == "completed"
    assert [call["thinking"] for call in calls] == [True]


# ---------------------------------------------------------------------------
# 图片顺序：落盘顺序随机，送进 OCR 前必须按拍摄时间重排
# ---------------------------------------------------------------------------


def test_pipeline_reorders_images_by_capture_time(tmp_path, events, monkeypatch):
    """Syncthing 按数据块同步，落盘顺序是乱的；题干必须按拍摄顺序拼。"""
    scrambled = [
        _make_image(tmp_path / "IMG_20260913_164126.jpg"),
        _make_image(tmp_path / "IMG_20260913_164128.jpg"),
        _make_image(tmp_path / "IMG_20260913_164123.jpg"),
        _make_image(tmp_path / "IMG_20260913_164131.jpg"),
    ]
    received: list[list[str]] = []

    def fake_combined(image_paths, provider=None):
        received.append([p.name for p in image_paths])
        return {"problem_type": "GENERAL", "pages": ["第1页", "第2页", "第3页", "第4页"]}

    _stub_solver(monkeypatch)
    monkeypatch.setattr(
        core_pipeline.vision_client, "classify_and_transcribe", fake_combined
    )

    result = _pipeline(tmp_path, events).run("t15", scrambled)

    assert result["status"] == "completed"
    assert received == [
        [
            "IMG_20260913_164123.jpg",
            "IMG_20260913_164126.jpg",
            "IMG_20260913_164128.jpg",
            "IMG_20260913_164131.jpg",
        ]
    ]
    # 解答文件头里的 images: 也应当是拍摄顺序
    solution = Path(result["path"]).read_text(encoding="utf-8")
    assert solution.index("IMG_20260913_164123.jpg") < solution.index("IMG_20260913_164131.jpg")


# ---------------------------------------------------------------------------
# 组 H：合并路径内联拼接（S9）—— 有 NEW/CONT 标记就不再花一次润色调用
# ---------------------------------------------------------------------------


def test_inline_merge_skips_polish_on_combined_path(tmp_path, images, events, monkeypatch):
    """merged 路径：`<<<PAGE n|CONT>>>` 就是模型给出的接缝判断，本地拼接即可。

    文本长到远超 MERGE_SKIP_THRESHOLD，唯一的跳过理由只能是"内联"。
    """
    solver_calls = _stub_solver(monkeypatch)
    long_page = "内容" * 2000
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": [long_page, "续页内容"]},
        vision_mode="combined",
        continuations=[False, True],
    )

    result = _pipeline(tmp_path, events).run("t16", images)

    assert result["timings"]["polish"] == 0
    assert _polish_calls(solver_calls) == 0
    # CONT 页用单换行紧接上一页（NEW 才用空行分隔），这是内联拼接与润色的关键差别
    assert result["problem_text"] == f"{long_page}\n续页内容"
    assert result["vision_mode"] == "combined"


def test_inline_merge_can_be_disabled(tmp_path, images, events, monkeypatch):
    """`VISION_INLINE_MERGE=false` 是一键回退开关：仍要老实走润色。"""
    monkeypatch.setattr(core_pipeline.config, "VISION_INLINE_MERGE", False)
    solver_calls = _stub_solver(monkeypatch, analysis="润色后的题目文本")
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["内容" * 2000, "续页"]},
        vision_mode="combined",
        continuations=[False, True],
    )

    result = _pipeline(tmp_path, events).run("t17", images)

    assert _polish_calls(solver_calls) == 1
    assert result["problem_text"] == "润色后的题目文本"


# ---------------------------------------------------------------------------
# 组 H：阶段缓存 —— 模型名写进 payload，provider 切换后旧缓存自动失效
# ---------------------------------------------------------------------------


class _DictCache:
    """最小可用的阶段缓存（存在内存里，payload 原样存放）。"""

    def __init__(self, store: dict | None = None) -> None:
        self.store = store or {}

    def get(self, task_id, stage):
        return self.store.get((task_id, stage))

    def set(self, task_id, stage, payload):
        self.store[(task_id, stage)] = payload


def test_cached_stage_with_other_model_is_a_miss(tmp_path, images, events, monkeypatch):
    """缓存键不含模型名：payload 里带的模型名不匹配时必须重算（旧 provider 的转录作废）。"""
    _stub_solver(monkeypatch)
    vision_calls = _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["当前 provider 的转录"]},
        vision_mode="combined",
    )

    stale = {
        "_meta": {"model": "GLM-4.6V-FlashX"},
        "value": {
            "problem_type": "GENERAL",
            "pages": ["旧 provider 的转录"],
            "failed_pages": [],
            "continuations": [],
            "vision_mode": "combined",
        },
    }
    cache = _DictCache({("t18", "vision"): stale})
    pipeline = core_pipeline.SolutionPipeline(
        solution_dir=tmp_path / "solutions",
        on_event=collected_append(events),
        stage_cache=cache,
        write_failure_log=False,
    )

    first = pipeline.run("t18", images)

    assert vision_calls["combined"] == 1, "旧模型写的缓存必须失效"
    assert "旧 provider 的转录" not in first["problem_text"]
    # 回写时带上当前模型名，供下一次比对
    assert cache.store[("t18", "vision")]["_meta"]["model"] == core_pipeline.config.VISION_CLASSIFY_MODEL

    # 同模型再读 → 命中，不再调用视觉模型
    second = pipeline.run("t18", images)
    assert vision_calls["combined"] == 1
    assert "vision" in second["timings"]["cached"]
    assert "当前 provider 的转录" in second["problem_text"]


def test_cached_stage_without_meta_is_a_miss(tmp_path, images, events, monkeypatch):
    """迁移前的旧缓存是裸 payload（结构不符）→ 一样按未命中处理，必须重算。"""
    _stub_solver(monkeypatch)
    vision_calls = _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["新转录"]},
        vision_mode="combined",
    )
    legacy = {"problem_type": "GENERAL", "pages": ["旧格式缓存"], "failed_pages": []}
    cache = _DictCache({("t19", "vision"): legacy})

    result = core_pipeline.SolutionPipeline(
        solution_dir=tmp_path / "solutions",
        on_event=collected_append(events),
        stage_cache=cache,
        write_failure_log=False,
    ).run("t19", images)

    assert vision_calls["combined"] == 1
    assert result["problem_text"] == "新转录"


def test_cache_fingerprint_invalidates_when_output_cap_changes(tmp_path, images, events, monkeypatch):
    """调大 `VISION_MAX_TOKENS` 后必须重算，否则"修截断"的补救措施是空的。

    缓存键是 `(task_id, stage)`，只比对模型名时：把上限从 8192 提到 32768 再点重试，
    仍会命中那份**被截断**的转录（计划书 §5 把"调大 max_tokens"列为截断的补救措施，
    而它必须真的能生效）。指纹因此带上了输出上限与协议版本。
    """
    _stub_solver(monkeypatch)
    vision_calls = _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["旧上限下的转录"]},
        vision_mode="combined",
    )
    cache = _DictCache()
    pipeline = core_pipeline.SolutionPipeline(
        solution_dir=tmp_path / "solutions",
        on_event=collected_append(events),
        stage_cache=cache,
        write_failure_log=False,
    )

    pipeline.run("t20", images)
    assert vision_calls["combined"] == 1
    assert cache.store[("t20", "vision")]["_meta"]["max_tokens"] == config.VISION_MAX_TOKENS

    # 同参数再跑 → 命中缓存（缓存本身是有效的）
    second = pipeline.run("t20", images)
    assert vision_calls["combined"] == 1
    assert "vision" in second["timings"]["cached"]

    # 调大输出上限 → 旧转录作废（它可能就是被截断的那份）
    monkeypatch.setattr(core_pipeline.config, "VISION_MAX_TOKENS", config.VISION_MAX_TOKENS * 2)
    third = pipeline.run("t20", images)

    assert vision_calls["combined"] == 2, "输出上限变了，旧缓存必须失效"
    assert "vision" not in third["timings"]["cached"]


def test_cache_fingerprint_invalidates_when_protocol_changes(tmp_path, images, events, monkeypatch):
    """协议版本变了（PAGE 协议改形态）也必须重算 —— 旧转录可能是另一种协议的产物。"""
    _stub_solver(monkeypatch)
    vision_calls = _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["旧协议转录"]},
        vision_mode="combined",
    )
    cache = _DictCache()
    pipeline = core_pipeline.SolutionPipeline(
        solution_dir=tmp_path / "solutions",
        on_event=collected_append(events),
        stage_cache=cache,
        write_failure_log=False,
    )

    pipeline.run("t21", images)
    assert vision_calls["combined"] == 1

    monkeypatch.setattr(core_pipeline.SolutionPipeline, "_CACHE_PROTOCOL", "page-v99")
    pipeline.run("t21", images)

    assert vision_calls["combined"] == 2


def test_json_protocol_keeps_the_polish_call(tmp_path, images, events, monkeypatch):
    """JSON 回退协议**不**内联：它没有 NEW/CONT，拿它内联等于把重复内容整段拼进去。

    JSON 的 `continuations` 恒为 False（每页都是"完整重述"），内联的结果既不去重、
    也没有 `---[NEXT]---` 边界。那正是润色 prompt"场景判断"要处理的场景，必须保留润色。
    """
    solver_calls = _stub_solver(monkeypatch, analysis="润色后的题目文本")
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["内容" * 2000, "重复的续页"]},
        vision_mode="json",
    )

    result = _pipeline(tmp_path, events).run("t22", images)

    assert _polish_calls(solver_calls) == 1
    assert result["problem_text"] == "润色后的题目文本"
    assert result["vision_mode"] == "json"


def test_batched_transcript_inlines_and_skips_polish(tmp_path, images, events, monkeypatch):
    """分批合并（`vision_mode="batched"` + `seams=True`）同样本地内联、跳过润色。

    批内接缝来自模型自己的 NEW/CONT（批首页按 NEW），因此分批只是把一次大请求拆成
    几个并发的小请求 —— T3 的收益（省掉那次重写整篇文本的润色调用）必须保留。
    """
    solver_calls = _stub_solver(monkeypatch)
    long_page = "内容" * 2000
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": [long_page, "续页内容"], "seams": True},
        vision_mode="batched",
        continuations=[False, True],
    )

    result = _pipeline(tmp_path, events).run("t26", images)

    assert result["timings"]["polish"] == 0
    assert _polish_calls(solver_calls) == 0
    assert result["problem_text"] == f"{long_page}\n续页内容"
    assert result["vision_mode"] == "batched"


def test_batched_without_seams_keeps_the_polish_call(tmp_path, images, events, monkeypatch):
    """有批走了 JSON 回退（`seams=False`）时，即使整体是 batched 也必须保留润色。"""
    solver_calls = _stub_solver(monkeypatch, analysis="润色后的题目文本")
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["内容" * 2000, "续页"], "seams": False},
        vision_mode="batched",
    )

    result = _pipeline(tmp_path, events).run("t27", images)

    assert _polish_calls(solver_calls) == 1
    assert result["problem_text"] == "润色后的题目文本"


def test_ended_flag_is_returned_and_missing_end_logs_a_warning(tmp_path, images, events, monkeypatch):
    """协议级截断信号要透出到结果里；缺 `<<<END>>>` 时还要留下告警。

    流式下拿不到 `finish_reason`，`ended` 是唯一能判断"转录被 max_tokens 截断"的信号，
    webapp/CLI 的告警靠它。
    """
    _stub_solver(monkeypatch)
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["甲", "乙"], "ended": False},
        vision_mode="combined",
    )
    records, handler = _capture_agent_log()
    try:
        result = _pipeline(tmp_path, events).run("t23", images)
    finally:
        logging.getLogger("CorePipeline").removeHandler(handler)

    assert result["ended"] is False
    assert any("<<<END>>>" in record.getMessage() for record in records)


def test_ended_defaults_to_true_for_the_parallel_path(tmp_path, images, events, monkeypatch):
    """并行路径没有协议级信号，`ended` 按 True 处理（缺页由 failed_pages 表达）。"""
    _stub_solver(monkeypatch)
    _stub_vision(monkeypatch, combined=None, pages=["甲", "乙"], failed=[])

    result = _pipeline(tmp_path, events).run("t24", images)

    assert result["vision_mode"] == "parallel"
    assert result["ended"] is True


def test_v130_cancel_before_vision_does_not_claim_a_transcribe_mode(tmp_path, images, events, monkeypatch):
    """视觉阶段没跑到的任务，`vision_mode` 必须是空串而不是 "parallel"。

    那一列是用来统计两条转录路径耗时的；把"第一步就被取消"记成并行路径会污染统计。
    """
    _stub_vision(monkeypatch, combined={"problem_type": "GENERAL", "pages": ["a", "b"]})
    _stub_solver(monkeypatch)
    token = CancelToken()
    token.cancel()

    result = _pipeline(tmp_path, events).run("t25", images, cancel=token)

    assert result["status"] == "cancelled"
    assert result["vision_mode"] == ""


# ---------------------------------------------------------------------------
# 组 I：OCR 双层落盘（S11）—— 页数必须等于图片数，且求解失败也要留下归档
# ---------------------------------------------------------------------------


def test_ocr_archive_is_written_page_per_image(tmp_path, images, events, monkeypatch):
    monkeypatch.setattr(core_pipeline.config, "OCR_DIR", tmp_path / "ocr")
    _stub_solver(monkeypatch)
    # 第 2 页为空内容：归档仍要为它写一节，标成"识别失败"而不是少写
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["第一页内容", ""]},
        vision_mode="combined",
        continuations=[False, False],
    )

    result = _pipeline(tmp_path, events).run("t20", images)

    archives = list((tmp_path / "ocr").glob("*/*.md"))
    assert len(archives) == 1
    assert archives[0].name == "t20.md"
    text = archives[0].read_text(encoding="utf-8")
    assert "task_id: t20" in text
    assert "vision_mode: combined" in text
    assert "pages: 2" in text
    assert "failed_pages: []" in text
    assert text.count("## 第 ") == 2, "分页数必须等于图片数（S11）"
    assert "识别失败" in text
    assert "第一页内容" in text
    # 解答文件必须指回这份归档（解答文件 ↔ 归档 ↔ uploads 目录可互相对照）
    solution = Path(result["path"]).read_text(encoding="utf-8")
    assert f"ocr_archive: {archives[0]}" in solution
    assert "task_id: t20" in solution


def test_ocr_archive_survives_solve_failure(tmp_path, images, events, monkeypatch):
    """归档是在视觉阶段结束后**立刻**写的：求解失败/取消也要保住 OCR。"""
    monkeypatch.setattr(core_pipeline.config, "OCR_DIR", tmp_path / "ocr")
    _stub_vision(monkeypatch, combined=None, pages=["只有第一页"], failed=[1])
    # 部分页失败 → 回退为原图直读，这里直接让这条链路报错
    monkeypatch.setattr(
        core_pipeline.vision_client,
        "solve_visual_reasoning_problem",
        lambda image_paths: iter([{"type": "error", "content": "模型挂了"}]),
    )

    with pytest.raises(RuntimeError, match="模型挂了"):
        _pipeline(tmp_path, events).run("t21", images)

    archives = list((tmp_path / "ocr").glob("*/*.md"))
    assert len(archives) == 1
    text = archives[0].read_text(encoding="utf-8")
    assert "vision_mode: parallel" in text
    assert "failed_pages: [1]" in text
    assert text.count("## 第 ") == 2
    assert "只有第一页" in text and "识别失败" in text


# ---------------------------------------------------------------------------
# T4：求解首行 FILE: 建议的剥离 + 三档文件名生成
# ---------------------------------------------------------------------------


def test_file_suggestion_is_stripped_and_used_as_filename(tmp_path, images, events, monkeypatch):
    """首行 FILE: 建议：不写进解答文件、不进 chunks/答案卡，只用于命名。

    分片刻意切在 FILE 行中间 —— 首行判定必须能跨分片。
    """
    solver_calls = _stub_solver(monkeypatch)
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "MULTIPLE_CHOICE", "pages": ["1. 题干", "2. 题干"]},
        vision_mode="combined",
    )

    def fake_stream_solve(prompt, provider, model, enable_thinking=True, **kwargs):
        yield {"type": "content", "content": "FI"}
        yield {"type": "content", "content": "LE: 16-20_多领域选择题"}
        yield {"type": "content", "content": "综合解答\n## 最终答案\n选 B。"}

    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", fake_stream_solve)

    result = _pipeline(tmp_path, events).run("t22", images)

    assert "FILE:" not in result["text"]
    assert result["text"].startswith("## 最终答案")
    # 答案卡抽取只看 chunks，FILE 行不该干扰它
    assert result["answer_card"]["text"].strip() == "选 B。"
    # 文件名采用首行建议 → 不必再调文件名模型
    assert Path(result["path"]).name == "16-20_多领域选择题综合解答.md"
    assert solver_calls["analysis"] == 0
    assert "FILE:" not in Path(result["path"]).read_text(encoding="utf-8")


def test_file_suggestion_line_does_not_affect_escalation(
    tmp_path, images, events, monkeypatch, _deterministic_thinking
):
    """`_should_escalate` 只看正文：FILE 行再长也不能冒充"答案够长"。

    这里 FILE 行的名字刻意长过 `SOLVER_ESCALATE_MIN_CHARS`，正文只有两个字 ——
    那一行若漏进 chunks，长度判定就会误判成"答案合格"，直接放弃升级重跑。
    """
    _stub_vision(monkeypatch, combined={"problem_type": "LEETCODE", "pages": ["题目"]})
    long_name = "长" * (core_pipeline.config.SOLVER_ESCALATE_MIN_CHARS + 200)
    calls = _stub_escalating_solver(monkeypatch, first_text=f"FILE: {long_name}\n短")

    result = _pipeline(tmp_path, events).run("t29", images)

    assert [call["thinking"] for call in calls] == [False, True], "正文过短必须触发升级"
    assert "FILE:" not in result["text"]


def test_first_line_that_is_not_a_filename_is_kept(tmp_path, images, events, monkeypatch):
    """首行不是 FILE: 行时，必须原样写出去（不能因为"在等判定"就把内容吞掉）。"""
    solver_calls = _stub_solver(monkeypatch)
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["题干甲", "题干乙"]},
        vision_mode="combined",
    )

    def fake_stream_solve(prompt, provider, model, enable_thinking=True, **kwargs):
        # 首行很晚才拿到换行，且带前导空行 —— 两种"不能丢内容"的情形
        yield {"type": "content", "content": "\n第 1 行是正文，不是文件名"}
        yield {"type": "content", "content": "\n第 2 行"}
        yield {"type": "meta", "thinking": False, "finish_reason": "stop", "truncated": False}

    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", fake_stream_solve)

    result = _pipeline(tmp_path, events).run("t23", images)

    assert "第 1 行是正文，不是文件名" in result["text"]
    assert "第 2 行" in result["text"]
    assert result["text"].startswith("\n第 1 行")


def test_long_first_line_is_flushed_without_waiting_for_newline(tmp_path, images, events, monkeypatch):
    """首行超过探测上限（约 120 字符）时立刻当正文写出去，不再缓冲。"""
    _stub_solver(monkeypatch)
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["题干甲", "题干乙"]},
        vision_mode="combined",
    )
    long_line = "答" * 200

    def fake_stream_solve(prompt, provider, model, enable_thinking=True, **kwargs):
        # 第一个分片就没有换行且远超上限 → 应当立即出现在 chunk 事件里
        yield {"type": "content", "content": long_line}

    monkeypatch.setattr(core_pipeline.solver_client, "stream_solve", fake_stream_solve)

    result = _pipeline(tmp_path, events).run("t24", images)

    chunk_events = [e["content"] for e in events if e["type"] == "chunk"]
    assert chunk_events and chunk_events[0] == long_line
    assert result["text"] == long_line


def test_local_filename_from_question_numbers_skips_the_model(tmp_path, images, events, monkeypatch):
    """T4：题号能本地解析出来时不调模型（auto 档的默认路径）。"""
    monkeypatch.setattr(core_pipeline.config, "FILENAME_MODE", "auto")
    solver_calls = _stub_solver(monkeypatch)
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["1. 第一题", "2. 第二题"]},
        vision_mode="combined",
    )

    result = _pipeline(tmp_path, events).run("t25", images)

    assert solver_calls["analysis"] == 0, "润色被内联跳过 + 文件名本地生成，一次模型都不该调"
    assert Path(result["path"]).name == "1-2_GENERAL_Solution.md"


def test_filename_mode_local_never_calls_the_model(tmp_path, images, events, monkeypatch):
    """`FILENAME_MODE=local`：解析不到题号也不许调模型，退到题型名。"""
    monkeypatch.setattr(core_pipeline.config, "FILENAME_MODE", "local")
    solver_calls = _stub_solver(monkeypatch)
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["没有题号的题干", "续页"]},
        vision_mode="combined",
    )

    result = _pipeline(tmp_path, events).run("t26", images)

    assert solver_calls["analysis"] == 0
    assert Path(result["path"]).name == "GENERAL_Solution.md"


def test_filename_model_call_is_timed_and_reported(tmp_path, images, events, monkeypatch):
    """T4：真的调了模型时，耗时进 timings.filename、用量发一条 stage="filename"。"""
    solver_calls = _stub_solver(monkeypatch)
    _stub_vision(
        monkeypatch,
        combined={"problem_type": "GENERAL", "pages": ["题干甲", "题干乙"]},
        vision_mode="combined",
    )

    result = _pipeline(tmp_path, events).run("t27", images)

    assert solver_calls["analysis"] == 1
    assert isinstance(result["timings"].get("filename"), int)
    filename_usage = [
        event for event in events
        if event["type"] == "usage" and event["stage"] == "filename"
    ]
    assert len(filename_usage) == 1
    assert filename_usage[0]["model"] == core_pipeline.config.AUX_MODEL_NAME


# ---------------------------------------------------------------------------
# 换路重解：跳过视觉阶段，但契约里的三个转录键仍要带回（webapp 不该 KeyError）
# ---------------------------------------------------------------------------


def test_resolve_returns_transcript_keys_and_reuses_archive(tmp_path, events, monkeypatch):
    _stub_solver(monkeypatch)
    # 第一次完整处理时留下的归档：重解不该再写一份，但 frontmatter 要指回去
    archive = core_pipeline.config.OCR_DIR / "2026-01-01" / "t28.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("旧归档", encoding="utf-8")

    result = _pipeline(tmp_path, events).resolve("t28", "GENERAL", "已有的题目文本")

    assert result["status"] == "completed"
    assert result["problem_text"] == "已有的题目文本"
    assert result["ocr_raw_text"] == "已有的题目文本"
    assert result["vision_mode"] == ""
    solution = Path(result["path"]).read_text(encoding="utf-8")
    assert "task_id: t28" in solution
    assert f"ocr_archive: {archive}" in solution
