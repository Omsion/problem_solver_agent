"""
test_multitenancy.py - 多用户隔离、额度校验与用量记账

这是商业化改造的核心安全回归：验证 A 用户看不到 B 用户的任务，
额度不足时提交被拒，以及用量确实落了库。

运行：pytest tests/test_multitenancy.py -v
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from webapp import config as web_config
from webapp.app import create_app


def _png_bytes(size=(48, 36)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (40, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """构建隔离的测试环境：独立的数据库与目录，并打开登录开关。"""
    monkeypatch.setattr(web_config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)
    monkeypatch.setattr(web_config, "SOLUTION_DIR", tmp_path / "solutions", raising=False)
    monkeypatch.setattr(web_config, "DATA_DIR", tmp_path / "data", raising=False)
    monkeypatch.setattr(web_config, "DB_PATH", tmp_path / "data" / "tasks.db", raising=False)
    monkeypatch.setattr(web_config, "AUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(web_config, "AUTH_SECRET_KEY", "test-secret-key-for-isolation", raising=False)
    monkeypatch.setattr(web_config, "DEFAULT_USER_BUDGET", 10.0, raising=False)
    monkeypatch.setattr(web_config, "MIN_TASK_BUDGET", 0.01, raising=False)
    monkeypatch.setattr(web_config, "ADMIN_PHONES", set(), raising=False)

    app = create_app()
    with TestClient(app) as client:
        yield client, app


def _register(client: TestClient, phone: str, password: str = "pw123456") -> dict:
    """走完整注册流程：发验证码（console 模式会回显）→ 注册。"""
    code_resp = client.post("/api/v1/auth/send-code", json={"phone": phone})
    assert code_resp.status_code == 200, code_resp.text
    code = code_resp.json().get("debug_code")
    assert code, "console 模式应回显验证码以便自测"

    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "code": code, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_task(client: TestClient, token: str) -> str:
    resp = client.post(
        "/api/tasks",
        files=[("files", ("a.png", _png_bytes(), "image/png"))],
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


# ---------------------------------------------------------------------------
# 认证与鉴权
# ---------------------------------------------------------------------------


def test_health_endpoint_is_public(app_env):
    """探活端点不需要登录，容器健康检查依赖它。"""
    client, _ = app_env
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_unauthenticated_request_is_rejected(app_env):
    client, _ = app_env
    resp = client.get("/api/tasks")
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert detail["code"] == "unauthorized"


def test_register_then_access_with_token(app_env):
    client, _ = app_env
    session = _register(client, "13800138001")
    assert session["access_token"]
    assert session["user"]["remaining"] == 10.0

    resp = client.get("/api/tasks", headers=_auth(session["access_token"]))
    assert resp.status_code == 200


def test_login_with_password(app_env):
    client, _ = app_env
    session = _register(client, "13800138002", password="mypassword")
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": "13800138002", "password": "mypassword"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["id"] == session["user"]["id"]


def test_login_with_wrong_password_is_rejected(app_env):
    client, _ = app_env
    _register(client, "13800138003", password="right")
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": "13800138003", "password": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "bad_credentials"


def test_api_key_authentication(app_env):
    """开放 API 场景：用 X-API-Key 代替 Bearer。"""
    client, app = app_env
    session = _register(client, "13800138004")
    user_id = session["user"]["id"]

    accounts = app.state.accounts
    user = accounts.get_user(user_id)
    assert user is not None
    assert user.api_key

    resp = client.get("/api/tasks", headers={"X-API-Key": user.api_key})
    assert resp.status_code == 200

    bad = client.get("/api/tasks", headers={"X-API-Key": "sk-solver-not-a-real-key"})
    assert bad.status_code == 401


def test_duplicate_phone_registration_is_rejected(app_env):
    client, _ = app_env
    _register(client, "13800138005")
    code = client.post("/api/v1/auth/send-code", json={"phone": "13800138005"}).json()["debug_code"]
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": "13800138005", "code": code},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "phone_taken"


def test_register_with_wrong_code_is_rejected(app_env):
    client, _ = app_env
    client.post("/api/v1/auth/send-code", json={"phone": "13800138006"})
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": "13800138006", "code": "000000"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "bad_code"


def test_invalid_phone_is_rejected(app_env):
    client, _ = app_env
    resp = client.post("/api/v1/auth/send-code", json={"phone": "12345"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_phone"


# ---------------------------------------------------------------------------
# 任务隔离（核心安全回归）
# ---------------------------------------------------------------------------


def test_users_cannot_see_each_others_tasks(app_env):
    client, _ = app_env
    token_a = _register(client, "13800138010")["access_token"]
    token_b = _register(client, "13800138011")["access_token"]

    task_a = _create_task(client, token_a)

    # A 能看到自己的任务
    listing_a = client.get("/api/tasks", headers=_auth(token_a)).json()
    assert [t["id"] for t in listing_a["tasks"]] == [task_a]

    # B 的列表里不应该有 A 的任务
    listing_b = client.get("/api/tasks", headers=_auth(token_b)).json()
    assert listing_b["tasks"] == []

    # B 直接猜 URL 也不能读到 A 的任务（返回 404 而不是 403，避免泄露存在性）
    detail = client.get(f"/api/tasks/{task_a}", headers=_auth(token_b))
    assert detail.status_code == 404

    # B 也不能删除 A 的任务
    delete = client.delete(f"/api/tasks/{task_a}", headers=_auth(token_b))
    assert delete.status_code == 404
    # A 的任务仍然在
    assert client.get(f"/api/tasks/{task_a}", headers=_auth(token_a)).status_code == 200


def test_users_cannot_cancel_or_retry_each_others_tasks(app_env):
    client, _ = app_env
    token_a = _register(client, "13800138012")["access_token"]
    token_b = _register(client, "13800138013")["access_token"]

    task_a = _create_task(client, token_a)

    assert client.post(f"/api/tasks/{task_a}/cancel", headers=_auth(token_b)).status_code == 404
    assert client.post(f"/api/tasks/{task_a}/retry", headers=_auth(token_b)).status_code == 404
    assert client.post(f"/api/tasks/{task_a}/resolve", headers=_auth(token_b)).status_code == 404
    assert client.post(f"/api/tasks/{task_a}/verify", headers=_auth(token_b)).status_code == 404


def test_stats_are_per_user(app_env):
    client, _ = app_env
    token_a = _register(client, "13800138014")["access_token"]
    token_b = _register(client, "13800138015")["access_token"]
    _create_task(client, token_a)

    stats_a = client.get("/api/stats", headers=_auth(token_a)).json()
    stats_b = client.get("/api/stats", headers=_auth(token_b)).json()
    assert stats_a["sample_size"] == 1
    assert stats_b["sample_size"] == 0


# ---------------------------------------------------------------------------
# 额度
# ---------------------------------------------------------------------------


def test_submit_is_rejected_when_budget_exhausted(app_env):
    """额度不足时必须拒绝，而不是先花钱再报错。"""
    client, app = app_env
    session = _register(client, "13800138020")
    user_id = session["user"]["id"]
    token = session["access_token"]

    # 把额度压到最低
    accounts = app.state.accounts
    assert accounts.set_budget(user_id, 0.0)

    resp = client.post(
        "/api/tasks",
        files=[("files", ("a.png", _png_bytes(), "image/png"))],
        headers=_auth(token),
    )
    assert resp.status_code == 402
    body = resp.json()
    assert body["error"]["code"] == "insufficient_budget"
    assert body["error"]["remaining"] == 0.0


def test_sufficient_budget_allows_submit(app_env):
    client, _ = app_env
    token = _register(client, "13800138021")["access_token"]
    task_id = _create_task(client, token)
    assert task_id


def test_me_endpoint_reports_usage_and_budget(app_env):
    client, app = app_env
    session = _register(client, "13800138022")
    user_id = session["user"]["id"]
    token = session["access_token"]

    app.state.accounts.record_usage(
        user_id=user_id, stage="solve", provider="deepseek",
        model="deepseek-v4-pro", input_tokens=1000, output_tokens=500,
    )

    body = client.get("/api/v1/auth/me", headers=_auth(token)).json()
    assert body["auth_enabled"] is True
    assert body["user"]["spent"] > 0
    assert body["user"]["remaining"] < body["user"]["budget"]
    assert body["usage"]["calls"] == 1
    assert body["usage"]["by_model"][0]["model"] == "deepseek-v4-pro"


def test_phone_is_masked_in_api_responses(app_env):
    """接口响应里不能出现完整手机号与完整密钥。"""
    client, app = app_env
    session = _register(client, "13800138023")
    token = session["access_token"]
    user_id = session["user"]["id"]

    me = client.get("/api/v1/auth/me", headers=_auth(token)).text
    assert "13800138023" not in me
    assert "138****8023" in me

    api_key = app.state.accounts.get_user(user_id).api_key
    assert api_key and api_key not in me


# ---------------------------------------------------------------------------
# 管理员
# ---------------------------------------------------------------------------


def test_normal_user_cannot_access_admin(app_env):
    client, _ = app_env
    token = _register(client, "13800138030")["access_token"]
    resp = client.get("/api/v1/admin/dashboard", headers=_auth(token))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "forbidden"


def test_admin_can_list_users_and_dashboard(app_env):
    client, app = app_env
    token = _register(client, "13800138031")["access_token"]
    user_id = app.state.accounts.get_user_by_phone("13800138031").id
    assert app.state.accounts.set_role(user_id, "admin")

    dashboard = client.get("/api/v1/admin/dashboard", headers=_auth(token))
    assert dashboard.status_code == 200
    assert dashboard.json()["total_users"] >= 1

    users = client.get("/api/v1/admin/users", headers=_auth(token))
    assert users.status_code == 200
    body = users.json()
    assert body["total"] >= 1
    assert {"phone", "role", "budget", "spent", "remaining"} <= set(body["data"][0].keys())


def test_admin_can_update_budget_and_role(app_env):
    client, app = app_env
    admin_token = _register(client, "13800138032")["access_token"]
    target = _register(client, "13800138033")
    target_id = target["user"]["id"]

    admin_id = app.state.accounts.get_user_by_phone("13800138032").id
    assert app.state.accounts.set_role(admin_id, "admin")

    update_budget = client.patch(
        f"/api/v1/admin/users/{target_id}/budget",
        json={"budget": 99.5},
        headers=_auth(admin_token),
    )
    assert update_budget.status_code == 200
    assert update_budget.json()["user"]["budget"] == 99.5

    update_role = client.patch(
        f"/api/v1/admin/users/{target_id}/role",
        json={"role": "admin"},
        headers=_auth(admin_token),
    )
    assert update_role.status_code == 200
    assert update_role.json()["user"]["role"] == "admin"


def test_admin_rejects_negative_budget(app_env):
    client, app = app_env
    token = _register(client, "13800138034")["access_token"]
    admin_id = app.state.accounts.get_user_by_phone("13800138034").id
    app.state.accounts.set_role(admin_id, "admin")

    resp = client.patch(
        f"/api/v1/admin/users/{admin_id}/budget",
        json={"budget": -1},
        headers=_auth(token),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "bad_budget"


# ---------------------------------------------------------------------------
# 用量记账（通过桩流水线，不产生真实 API 调用）
# ---------------------------------------------------------------------------


def test_usage_event_is_recorded(app_env, monkeypatch):
    """流水线上报 usage 事件后，数据库里应该有对应流水。

    注意：本用例耗时约 30 秒，这不是被测代码慢，而是 Starlette `TestClient`
    对 `StreamingResponse` 的已知行为——它会把流式响应缓冲到流结束才交给
    `iter_text()`，而本端点的保活心跳间隔是 30 秒。真实 uvicorn 下是逐帧
    立即推送的（已用独立脚本验证事件在流水线结束时就已发布到订阅者的队列）。
    """
    client, app = app_env
    session = _register(client, "13800138040")
    user_id = session["user"]["id"]
    token = session["access_token"]
    task_id = _create_task(client, token)

    from webapp import routes

    def fake_run(self, task_id_arg, image_paths, on_progress, **kwargs):
        # 模拟流水线上报一次用量后正常完成
        on_progress({
            "type": "usage",
            "stage": "solve",
            "model": "deepseek-v4-pro",
            "provider": "deepseek",
            "pages": 1,
            "output_chars": 2000,
            "calls": 1,
        })
        on_progress({"type": "done", "task_id": task_id_arg, "filename": "x.md"})
        routes.task_manager.update_task(task_id_arg, status="completed", filename="x.md")

    monkeypatch.setattr(routes.pipeline_service.__class__, "run", fake_run)

    with client.stream("GET", f"/api/tasks/{task_id}/stream", headers=_auth(token)) as stream:
        body = "".join(stream.iter_text())
    assert "event: done" in body
    # usage 事件不应下发到前端
    assert "usage" not in body

    usage = app.state.accounts.list_usage(user_id)
    assert len(usage) == 1
    assert usage[0]["stage"] == "solve"
    assert usage[0]["model"] == "deepseek-v4-pro"
    assert usage[0]["task_id"] == task_id
    assert usage[0]["cost"] > 0

    # 记账必须落在正确的人名下（隔离性）
    other = _register(client, "13800138041")["user"]["id"]
    assert app.state.accounts.list_usage(other) == []
