"""
test_resolve_verify_sse.py - 换路重解 / 核对 / SSE 续传 测试

覆盖新增能力：
- 事件序号与 `id:` 帧格式
- `Last-Event-ID` 续传（只补发漏掉的事件）
- 历史环形上限（长任务不撑爆内存）
- `/resolve` 与 `/verify` 的状态门禁与错误分支

运行：pytest tests/test_resolve_verify_sse.py -v
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from problem_solver_agent.verify import VerificationResult
from webapp.app import create_app
from webapp.routes import TaskEventBus, _sse_frame


def _png_bytes(size=(64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (30, 90, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _vision_cache_payload(
    value: dict,
    *,
    model: str | None = None,
    provider: str | None = None,
    omit_provider: bool = False,
) -> dict:
    """构造迁移后（组 I）的阶段缓存包装 `{"_meta": …, "value": …}`。

    默认写入**当前**模型与 provider，因此是"可用"的缓存；要造失效缓存就显式传
    别的 model/provider。meta 里额外塞 max_tokens/protocol（按 core 当前值），
    用于验证读取端不依赖 `_meta` 的确切键集合（Lead 还在往里加键）。
    """
    from problem_solver_agent import config as core_config
    from problem_solver_agent.core_pipeline import SolutionPipeline

    meta = {
        "model": model or core_config.VISION_CLASSIFY_MODEL,
        "max_tokens": 4096,
        "protocol": getattr(SolutionPipeline, "_CACHE_PROTOCOL", "page-v1"),
    }
    if not omit_provider:
        meta["provider"] = provider or core_config.VISION_PROVIDER_NAME
    return {"_meta": meta, "value": value}


def _write_ocr_archive(task_id: str) -> Path:
    """按 core 的约定造一份 OCR 归档：`<OCR_DIR>/<YYYY-MM-DD>/<task_id>.md`。"""
    from problem_solver_agent import config as core_config
    from problem_solver_agent.utils import sanitize_filename

    archive = Path(core_config.OCR_DIR) / "2026-01-01" / f"{sanitize_filename(task_id)}.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("---\ntask_id: demo\n---\n\n第 1 页内容\n", encoding="utf-8")
    return archive


# ---------------------------------------------------------------------------
# 事件总线：序号与续传
# ---------------------------------------------------------------------------


def test_publish_assigns_monotonic_ids():
    bus = TaskEventBus()
    bus.publish("t1", {"type": "status", "message": "a"})
    bus.publish("t1", {"type": "chunk", "content": "b"})
    bus.publish("t1", {"type": "done"})

    assert bus.last_event_id("t1") == 3
    assert [TaskEventBus.event_id(e) for e in bus._history["t1"]] == [1, 2, 3]  # noqa: SLF001


def test_ids_are_per_task():
    bus = TaskEventBus()
    bus.publish("a", {"type": "chunk"})
    bus.publish("b", {"type": "chunk"})
    bus.publish("a", {"type": "chunk"})

    assert bus.last_event_id("a") == 2
    assert bus.last_event_id("b") == 1


def test_replay_since_returns_only_missed_events():
    bus = TaskEventBus()
    for text in ("one", "two", "three"):
        bus.publish("t1", {"type": "chunk", "content": text})

    missed = bus.replay_since("t1", 1)
    assert [e["content"] for e in missed] == ["two", "three"]


def test_replay_since_zero_returns_nothing():
    """last_event_id 为 0 表示全新连接，不走续传路径。"""
    bus = TaskEventBus()
    bus.publish("t1", {"type": "chunk", "content": "x"})
    assert bus.replay_since("t1", 0) == []


def test_replay_since_up_to_date_returns_empty():
    bus = TaskEventBus()
    bus.publish("t1", {"type": "chunk"})
    assert bus.replay_since("t1", 1) == []
    assert bus.replay_since("t1", 99) == []


def test_history_is_capped():
    """长任务不能无限累积事件，超出上限丢弃最旧的。"""
    bus = TaskEventBus()
    bus.REPLAY_LIMIT = 10  # type: ignore[misc]
    for index in range(25):
        bus.publish("t1", {"type": "chunk", "content": str(index)})

    history = bus._history["t1"]  # noqa: SLF001
    assert len(history) == 10
    # 保留的应是最后 10 条
    assert history[0]["content"] == "15"
    assert history[-1]["content"] == "24"
    # 序号仍然连续递增
    assert bus.last_event_id("t1") == 25


def test_cleanup_clears_counter_too():
    bus = TaskEventBus()
    bus.publish("t1", {"type": "chunk"})
    bus.cleanup("t1")
    assert bus.last_event_id("t1") == 0
    assert bus.replay_since("t1", 1) == []


def test_sse_frame_contains_id_event_and_data():
    frame = _sse_frame({"type": "chunk", "content": "hi", "_id": 4, "_task_id": "t1"})
    assert frame.startswith("id: 4\n")
    assert "event: chunk\n" in frame
    assert '"content": "hi"' in frame
    # 内部字段不下发
    assert "_task_id" not in frame
    assert '"_id"' not in frame
    assert frame.endswith("\n\n")


def test_sse_frame_without_id_omits_id_line():
    frame = _sse_frame({"type": "init", "task_id": "t1"})
    assert frame.startswith("event: init\n")
    assert "id:" not in frame


def test_publish_does_not_mutate_caller_dict():
    bus = TaskEventBus()
    original = {"type": "chunk", "content": "x"}
    bus.publish("t1", original)
    assert "_id" not in original
    assert "_task_id" not in original


# ---------------------------------------------------------------------------
# 路由：resolve / verify
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from webapp import config as web_config

    monkeypatch.setattr(web_config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)
    monkeypatch.setattr(web_config, "SOLUTION_DIR", tmp_path / "solutions", raising=False)
    monkeypatch.setattr(web_config, "DATA_DIR", tmp_path / "data", raising=False)
    monkeypatch.setattr(web_config, "DB_PATH", tmp_path / "data" / "tasks.db", raising=False)

    app = create_app()
    with TestClient(app) as c:
        yield c


def _create_task_with_images(client: TestClient, names=("a.png",)) -> str:
    files = [("files", (name, _png_bytes((40 + i, 40)), "image/png")) for i, name in enumerate(names)]
    resp = client.post("/api/tasks", files=files)
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


def test_resolve_unknown_task(client: TestClient):
    assert client.post("/api/tasks/nope/resolve").status_code == 404


def test_resolve_rejects_invalid_style(client: TestClient):
    task_id = _create_task_with_images(client)
    resp = client.post(f"/api/tasks/{task_id}/resolve?style=WRONG")
    assert resp.status_code == 400


def test_resolve_without_transcript_returns_409(client: TestClient):
    """新任务还没有任何解答，取不到题目文本，应提示先完整处理一次。"""
    task_id = _create_task_with_images(client)
    resp = client.post(f"/api/tasks/{task_id}/resolve")
    assert resp.status_code == 409
    assert resp.json()["code"] == "no_transcript"


def test_resolve_uses_cached_transcript(client: TestClient, monkeypatch):
    """阶段缓存里有识别结果时，resolve 应直接可用并跳过 OCR。

    缓存是迁移后的包装形状（`{"_meta": …, "value": …}`）。F1 之前这里按裸字段读
    `cached.get("pages")`，包装层让 `pages` 恒为 None，于是"已付费的转录明明在
    缓存里"却返回 409 —— 用户被迫为同一份 OCR 再付一次钱。
    """
    from webapp import routes

    # 只验证"能进入求解阶段"，用桩掐掉后台线程的真实模型调用（测试不联网）
    calls: list[dict] = []

    def fake_resolve(task_id, image_paths, on_progress, **kwargs):
        calls.append({"task_id": task_id, **kwargs})
        return {"status": "completed", "path": None}

    monkeypatch.setattr(routes.pipeline_service, "resolve", fake_resolve)

    task_id = _create_task_with_images(client)
    routes.task_manager.set_cached_stage(
        task_id,
        "vision",
        _vision_cache_payload(
            {
                "problem_type": "GENERAL",
                "pages": ["第一页题目", "第二页题目"],
                "failed_pages": [],
                "continuations": [False, True],
                "vision_mode": "combined",
            }
        ),
    )

    resp = client.post(f"/api/tasks/{task_id}/resolve?style=EXPLORATORY&thinking=1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reused_transcript"] is True
    assert body["style"] == "EXPLORATORY"
    assert body["thinking"] is True

    # 求解在后台线程里跑：轮询等它被调用，确认送进去的确实是缓存里的题面
    # （CONT 页用一个换行拼接），而不是空串或 "None"。
    deadline = time.time() + 5
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    assert calls, "resolve 后台线程没有调用流水线"
    assert calls[0]["transcribed_text"] == "第一页题目\n第二页题目"


def test_resolve_rejects_legacy_naked_cache(client: TestClient):
    """迁移前的裸 payload 不可信：宁可让用户重跑一次，也不按旧字段名猜着读。"""
    from webapp import routes

    task_id = _create_task_with_images(client)
    routes.task_manager.set_cached_stage(
        task_id, "vision", {"problem_type": "GENERAL", "pages": ["旧格式的题目文本"]}
    )

    resp = client.post(f"/api/tasks/{task_id}/resolve")
    assert resp.status_code == 409
    assert resp.json()["code"] == "no_transcript"


def test_resolve_rejects_cache_from_previous_vision_model(client: TestClient):
    """切了视觉模型/provider 后旧缓存必须失效（stage_cache 主键不含模型名）。

    缓存键是 `(task_id, stage)`，若只按 model 判"有缓存"，切 provider 后
    重解会静默拿上一个 provider 的转录去求解。
    """
    from webapp import routes

    task_id = _create_task_with_images(client)
    routes.task_manager.set_cached_stage(
        task_id,
        "vision",
        _vision_cache_payload(
            {"problem_type": "GENERAL", "pages": ["旧模型的题目文本"]},
            model="definitely-not-the-current-vision-model",
            provider="not-the-current-provider",
        ),
    )

    resp = client.post(f"/api/tasks/{task_id}/resolve")
    assert resp.status_code == 409
    assert resp.json()["code"] == "no_transcript"


class _FakeTaskManager:
    """只实现 `read_cached_transcript` 需要的取缓存接口。"""

    def __init__(self, payload) -> None:
        self.payload = payload

    def get_cached_stage(self, task_id: str, stage: str):
        assert stage == "vision"
        return self.payload


def test_read_cached_transcript_tolerates_missing_meta_keys():
    """`_meta` 只要求 model 对得上：provider/其它键缺失或新增都不该让缓存全失效。"""
    from problem_solver_agent import config as core_config
    from webapp.pipeline import read_cached_transcript, transcript_text_views

    value = {
        "problem_type": "GENERAL",
        "pages": ["一", "二"],
        "continuations": [False, True],
        "vision_mode": "combined",
    }
    manager = _FakeTaskManager(
        {"_meta": {"model": core_config.VISION_CLASSIFY_MODEL}, "value": value}
    )
    assert read_cached_transcript(manager, "t1") == value
    # CONT 页用一个换行拼接（join_by_continuation 的约定），不是 ---[NEXT]---
    assert transcript_text_views(value) == ("一\n二", "一\n二")


def test_read_cached_transcript_rejects_other_provider():
    """provider 写在 `_meta` 里且与当前不一致时必须判失效。"""
    from problem_solver_agent import config as core_config
    from webapp.pipeline import read_cached_transcript

    manager = _FakeTaskManager(
        {
            "_meta": {
                "model": core_config.VISION_CLASSIFY_MODEL,
                "provider": "some-other-provider",
            },
            "value": {"pages": ["x"]},
        }
    )
    assert read_cached_transcript(manager, "t1") is None


def test_transcript_text_views_has_no_problem_text_for_visual_reasoning():
    """视觉推理题没有文本输入：problem_text 写空串，原始 OCR 仍然保留。"""
    from webapp.pipeline import transcript_text_views

    problem_text, ocr_raw_text = transcript_text_views(
        {"problem_type": "VISUAL_REASONING", "pages": ["图形描述"], "continuations": []}
    )
    assert problem_text == ""
    assert ocr_raw_text == "图形描述"


def test_resolve_visual_reasoning_without_images_returns_410(client: TestClient, tmp_path):
    from webapp import routes

    task_id = _create_task_with_images(client)
    routes.task_manager.set_cached_stage(
        task_id,
        "vision",
        _vision_cache_payload({"problem_type": "VISUAL_REASONING", "pages": ["图形题"]}),
    )
    routes.task_manager.update_task(task_id, problem_type="VISUAL_REASONING")
    # 删掉原图，模拟被清理
    for path in (tmp_path / "uploads" / task_id).iterdir():
        path.unlink()

    resp = client.post(f"/api/tasks/{task_id}/resolve")
    assert resp.status_code == 410
    assert resp.json()["code"] == "upload_expired"


def test_verify_unknown_task(client: TestClient):
    assert client.post("/api/tasks/nope/verify").status_code == 404


def test_verify_requires_terminal_status(client: TestClient):
    task_id = _create_task_with_images(client)
    resp = client.post(f"/api/tasks/{task_id}/verify")
    assert resp.status_code == 400
    assert resp.json()["code"] == "not_verifiable"


def test_verify_requires_answer_content(client: TestClient):
    from webapp import routes

    task_id = _create_task_with_images(client)
    routes.task_manager.update_task(task_id, status="completed")  # 没有解答文件
    resp = client.post(f"/api/tasks/{task_id}/verify")
    assert resp.status_code == 409
    assert resp.json()["code"] == "empty_answer"


def test_verify_returns_structured_result(client: TestClient, tmp_path, monkeypatch):
    """核对成功时返回结构化结论并标记任务已核对。"""
    from webapp import routes

    task_id = _create_task_with_images(client)
    solution = tmp_path / "solutions" / "answer.md"
    solution.parent.mkdir(parents=True, exist_ok=True)
    solution.write_text("# 解答\n\n选 B。\n", encoding="utf-8")
    routes.task_manager.update_task(
        task_id, status="completed", solution_path=str(solution), filename="answer.md"
    )

    def fake_verify(task_id_arg, image_paths, answer_text):
        assert answer_text.startswith("# 解答")
        assert image_paths  # 原图存在
        return VerificationResult(verdict="disagree", issues=["漏答第二问"], corrections="补上", model="test-model")

    monkeypatch.setattr(routes.pipeline_service, "verify", fake_verify)

    resp = client.post(f"/api/tasks/{task_id}/verify")
    assert resp.status_code == 200, resp.text
    payload = resp.json()["verification"]
    assert payload["verdict"] == "disagree"
    assert payload["issues"] == ["漏答第二问"]
    assert routes.task_manager.get_task(task_id)["verified"] == 1


def test_verify_requires_images(client: TestClient, tmp_path):
    from webapp import routes

    task_id = _create_task_with_images(client)
    solution = tmp_path / "solutions" / "answer.md"
    solution.parent.mkdir(parents=True, exist_ok=True)
    solution.write_text("答案", encoding="utf-8")
    routes.task_manager.update_task(task_id, status="completed", solution_path=str(solution))
    for path in (tmp_path / "uploads" / task_id).iterdir():
        path.unlink()

    resp = client.post(f"/api/tasks/{task_id}/verify")
    assert resp.status_code == 410
    assert resp.json()["code"] == "upload_expired"


# ---------------------------------------------------------------------------
# 删除任务：OCR 归档也要一起删（F6）
# ---------------------------------------------------------------------------


def test_delete_task_removes_ocr_archive(client: TestClient):
    """删任务时 `<OCR_DIR>/<日期>/<task_id>.md` 必须一起删，否则永久残留。"""
    task_id = _create_task_with_images(client)
    archive = _write_ocr_archive(task_id)
    assert archive.exists()

    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    assert not archive.exists()


def test_delete_task_without_ocr_archive_does_not_raise(client: TestClient):
    """没有归档（例如视觉阶段就失败了）时删除接口必须照常返回 200。"""
    task_id = _create_task_with_images(client)
    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 200


def test_delete_ocr_archives_never_touches_files_outside_ocr_dir(tmp_path):
    """task_id 里的路径字符不能让删除越出 OCR_DIR（历史事故的护栏）。"""
    from problem_solver_agent import config as core_config
    from webapp.pipeline import delete_ocr_archives

    outside = Path(core_config.OCR_DIR).parent / "outside.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("不要删我", encoding="utf-8")

    # sanitize 之后 stem 退化成 "....outside"，glob 匹配不到任何归档
    assert delete_ocr_archives(["../../outside"]) == 0
    assert outside.exists()


def test_extract_problem_text_from_solution(tmp_path):
    """换路重解依赖从解答文件里取回题目文本。"""
    from webapp.pipeline import PipelineService

    solution = tmp_path / "s.md"
    solution.write_text(
        "---\nproblem_type: GENERAL\n---\n\n"
        "# 题目文本\n\n这是第一页的内容。\n\n这是第二页的内容。\n\n"
        "---\n\n# 解答\n\n选 B。\n",
        encoding="utf-8",
    )

    text = PipelineService.extract_problem_text(solution)
    assert "第一页的内容" in text
    assert "第二页的内容" in text
    # 不能把解答部分也带进来
    assert "选 B" not in text


def test_extract_problem_text_missing_file(tmp_path):
    from webapp.pipeline import PipelineService

    assert PipelineService.extract_problem_text(tmp_path / "nope.md") == ""


def test_extract_problem_text_without_section(tmp_path):
    from webapp.pipeline import PipelineService

    solution = tmp_path / "s.md"
    solution.write_text("# 解答\n\n只有答案。\n", encoding="utf-8")
    assert PipelineService.extract_problem_text(solution) == ""
