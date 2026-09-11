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


def test_resolve_uses_cached_transcript(client: TestClient):
    """阶段缓存里有识别结果时，resolve 应直接可用并跳过 OCR。"""
    from webapp import routes

    task_id = _create_task_with_images(client)
    routes.task_manager.set_cached_stage(
        task_id, "vision", {"problem_type": "GENERAL", "pages": ["第一页题目", "第二页题目"]}
    )

    resp = client.post(f"/api/tasks/{task_id}/resolve?style=EXPLORATORY&thinking=1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reused_transcript"] is True
    assert body["style"] == "EXPLORATORY"
    assert body["thinking"] is True


def test_resolve_visual_reasoning_without_images_returns_410(client: TestClient, tmp_path):
    from webapp import routes

    task_id = _create_task_with_images(client)
    routes.task_manager.set_cached_stage(
        task_id, "vision", {"problem_type": "VISUAL_REASONING", "pages": ["图形题"]}
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
