"""
test_routes_upload.py - 上传安全与任务接口门禁测试

覆盖：
- 目录穿越文件名被拒（旧实现会写到 uploads 之外）
- 非图片内容被拒
- 超过体积限制被拒
- 取消 / 重试 的状态门禁
- 终态任务的 SSE 响应

运行：pytest tests/test_routes_upload.py -v
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from webapp.app import create_app


def _png_bytes(size=(64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """把上传/解答/数据库目录指向临时路径，避免污染真实数据。"""
    from webapp import config as web_config

    monkeypatch.setattr(web_config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)
    monkeypatch.setattr(web_config, "SOLUTION_DIR", tmp_path / "solutions", raising=False)
    monkeypatch.setattr(web_config, "DATA_DIR", tmp_path / "data", raising=False)
    monkeypatch.setattr(web_config, "DB_PATH", tmp_path / "data" / "tasks.db", raising=False)

    app = create_app()
    with TestClient(app) as c:
        yield c


def _upload(client: TestClient, name: str, payload: bytes, content_type: str = "image/png"):
    return client.post(
        "/api/tasks",
        files=[("files", (name, payload, content_type))],
    )


def test_normal_upload_creates_task(client: TestClient):
    resp = _upload(client, "shot.png", _png_bytes())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["num_images"] == 1

    detail = client.get(f"/api/tasks/{body['task_id']}")
    assert detail.status_code == 200
    assert len(detail.json()["image_urls"]) == 1


def test_path_traversal_filename_is_sanitized(client: TestClient, tmp_path):
    """核心回归：目录穿越文件名不能写到 uploads 之外。

    安全策略是"清理成安全 basename 后接受"，而不是报错拒绝：
    `../../../evil.png` → `evil.png`，并且只能落在 uploads/<task_id>/ 内。
    """
    resp = _upload(client, "../../../evil.png", _png_bytes())
    assert resp.status_code == 200, resp.text

    task_id = resp.json()["task_id"]
    upload_root = tmp_path / "uploads"
    stored = sorted((upload_root / task_id).iterdir())
    assert [p.name for p in stored] == ["evil.png"]

    # 项目目录与 uploads 的上级都不该出现 evil.png
    assert not (upload_root.parent / "evil.png").exists()
    assert not (tmp_path / "evil.png").exists()


def test_absolute_path_filename_is_sanitized(client: TestClient, tmp_path):
    resp = _upload(client, "C:\\Windows\\Temp\\evil.png", _png_bytes())
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    stored = sorted((tmp_path / "uploads" / task_id).iterdir())
    assert [p.name for p in stored] == ["evil.png"]


def test_dot_dot_filename_does_not_escape(client: TestClient, tmp_path):
    resp = _upload(client, "..\\..\\evil.png", _png_bytes())
    assert resp.status_code == 200, resp.text
    upload_root = tmp_path / "uploads"
    for path in upload_root.rglob("*"):
        if path.is_file():
            assert upload_root in path.parents
            assert path.name == "evil.png"


def test_non_image_content_is_rejected(client: TestClient):
    resp = _upload(client, "fake.png", b"this is not an image")
    assert resp.status_code == 400
    assert resp.json()["error"].startswith("文件不是有效图片")


def test_disallowed_extension_is_rejected(client: TestClient):
    resp = _upload(client, "script.sh", b"#!/bin/sh\necho hi", "text/x-sh")
    assert resp.status_code == 400
    assert "不支持的文件类型" in resp.json()["error"]


def test_oversized_upload_is_rejected(client: TestClient, monkeypatch):
    from webapp import config as web_config

    monkeypatch.setattr(web_config, "MAX_UPLOAD_SIZE", 0, raising=False)  # 0 MB → 任何内容都超限
    resp = _upload(client, "big.png", _png_bytes())
    assert resp.status_code == 413
    assert resp.json()["code"] == "too_large"


def test_duplicate_filenames_get_unique_names(client: TestClient, tmp_path):
    resp = client.post(
        "/api/tasks",
        files=[
            ("files", ("same.png", _png_bytes(), "image/png")),
            ("files", ("same.png", _png_bytes((32, 32)), "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    files = sorted((tmp_path / "uploads" / task_id).iterdir())
    assert len(files) == 2
    assert {f.name for f in files} == {"same.png", "same_2.png"}


def test_failed_upload_cleans_up_directory(client: TestClient, tmp_path):
    _upload(client, "bad.png", b"not an image")
    uploads = tmp_path / "uploads"
    # 不应留下空任务目录
    assert list(uploads.iterdir()) == []


# ---------------------------------------------------------------------------
# 状态门禁
# ---------------------------------------------------------------------------


def test_cancel_unknown_task_returns_404(client: TestClient):
    assert client.post("/api/tasks/nope/cancel").status_code == 404


def test_cancel_completed_task_is_rejected(client: TestClient):
    resp = _upload(client, "ok.png", _png_bytes())
    task_id = resp.json()["task_id"]

    from webapp import routes

    routes.task_manager.update_task(task_id, status="completed")
    cancel = client.post(f"/api/tasks/{task_id}/cancel")
    assert cancel.status_code == 400
    assert cancel.json()["code"] == "not_cancellable"


def test_cancel_marks_task_cancelled(client: TestClient):
    resp = _upload(client, "ok.png", _png_bytes())
    task_id = resp.json()["task_id"]

    cancel = client.post(f"/api/tasks/{task_id}/cancel")
    assert cancel.status_code == 200
    assert client.get(f"/api/tasks/{task_id}").json()["task"]["status"] == "cancelled"


def test_retry_requires_terminal_non_completed_status(client: TestClient):
    resp = _upload(client, "ok.png", _png_bytes())
    task_id = resp.json()["task_id"]

    # pending 状态不允许重试
    first = client.post(f"/api/tasks/{task_id}/retry")
    assert first.status_code == 400
    assert first.json()["code"] == "not_retryable"

    from webapp import routes

    routes.task_manager.update_task(task_id, status="failed", error_message="boom")
    second = client.post(f"/api/tasks/{task_id}/retry")
    assert second.status_code == 200
    assert second.json()["resumed"] is True
    # 重试后错误信息被清空，状态回到 pending
    task = client.get(f"/api/tasks/{task_id}").json()["task"]
    assert task["status"] == "pending"
    assert task["error_message"] == ""


def test_retry_without_images_returns_410(client: TestClient, tmp_path):
    resp = _upload(client, "ok.png", _png_bytes())
    task_id = resp.json()["task_id"]

    from webapp import routes

    routes.task_manager.update_task(task_id, status="failed", error_message="boom")
    # 模拟原图被清理
    for path in (tmp_path / "uploads" / task_id).iterdir():
        path.unlink()

    retry = client.post(f"/api/tasks/{task_id}/retry")
    assert retry.status_code == 410
    assert retry.json()["code"] == "upload_expired"


def test_completed_task_stream_returns_done_event(client: TestClient):
    resp = _upload(client, "ok.png", _png_bytes())
    task_id = resp.json()["task_id"]

    from webapp import routes

    routes.task_manager.update_task(task_id, status="completed", filename="x.md")

    with client.stream("GET", f"/api/tasks/{task_id}/stream") as stream:
        body = "".join(stream.iter_text())
    assert "event: done" in body
    assert "x.md" in body


# ---------------------------------------------------------------------------
# 系统状态端点
# ---------------------------------------------------------------------------


def test_status_endpoint(client: TestClient):
    body = client.get("/api/status").json()
    for key in ("auto_import_enabled", "running", "monitor_dir", "uploads_bytes", "remote_connected"):
        assert key in body
    assert body["remote_connected"] is False


def test_health_endpoint_does_not_require_api_calls(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "vision_configured" in body
    assert "solver_providers" in body


def test_stats_endpoint(client: TestClient):
    body = client.get("/api/stats").json()
    assert "stages" in body
    assert "sample_size" in body
