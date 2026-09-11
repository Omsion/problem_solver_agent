"""
test_auth.py - JWT 令牌、Bearer 解析与依赖注入测试

覆盖：
- 令牌签发/解析：sub/role/tenant_id/iat/exp 正确，expires_minutes 生效
- 篡改 payload 或签名 → TokenError，且能区分"签名校验失败"
- 已过期令牌 → TokenError
- 算法混淆防护：alg=none / 其它算法一律拒绝（安全回归）
- 格式错误（空串、段数不对）→ TokenError；extract_bearer_token 大小写与非法输入
- 依赖注入：AUTH_ENABLED 开关、Bearer/API-Key 认证、401 错误码 unauthorized、
  非管理员 403 错误码 forbidden

运行：pytest tests/test_auth.py -v
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from webapp import config as webapp_config
from webapp.accounts import ROLE_ADMIN, ROLE_USER, AccountManager, User
from webapp.auth import (
    TokenError,
    create_access_token,
    decode_access_token,
    extract_bearer_token,
    get_secret_key,
)
from webapp.deps import get_admin_user, get_current_user


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    """固定签名密钥，避免依赖进程内临时密钥导致断言不稳定。"""
    monkeypatch.setattr(webapp_config, "AUTH_SECRET_KEY", "unit-test-secret")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


# ---------------------------------------------------------------------------
# JWT 签发与解析
# ---------------------------------------------------------------------------


def test_create_and_decode_access_token_roundtrip():
    token = create_access_token("u_1001", role="admin", tenant_id="tenant-a", expires_minutes=5)
    payload = decode_access_token(token)

    assert payload["sub"] == "u_1001"
    assert payload["role"] == "admin"
    assert payload["tenant_id"] == "tenant-a"
    assert abs(payload["iat"] - int(time.time())) <= 5
    assert payload["exp"] == payload["iat"] + 5 * 60


def test_token_header_is_hs256():
    token = create_access_token("u_1001")
    header = json.loads(_b64url_decode(token.split(".")[0]))
    assert header == {"alg": "HS256", "typ": "JWT"}


def test_extra_claims_are_merged():
    token = create_access_token("u_1001", extra={"scope": "read"})
    assert decode_access_token(token)["scope"] == "read"


def test_expires_minutes_controls_exp():
    """ttl 参数生效：不同 ttl 得到不同的 exp。"""
    short = decode_access_token(create_access_token("u_1001", expires_minutes=1))
    long = decode_access_token(create_access_token("u_1001", expires_minutes=60))

    assert short["exp"] - short["iat"] == 60
    assert long["exp"] - long["iat"] == 3600
    assert long["exp"] > short["exp"]


def test_default_expiry_uses_config(monkeypatch):
    monkeypatch.setattr(webapp_config, "ACCESS_TOKEN_EXPIRE_MINUTES", 7)
    payload = decode_access_token(create_access_token("u_1001"))
    assert payload["exp"] - payload["iat"] == 7 * 60


def test_tampered_payload_is_rejected():
    """篡改 payload（例如把自己提权为 admin）后签名必然不匹配。"""
    token = create_access_token("u_1002", role="user")
    header_segment, payload_segment, signature_segment = token.split(".")

    payload = json.loads(_b64url_decode(payload_segment))
    payload["role"] = "admin"
    forged_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    with pytest.raises(TokenError) as excinfo:
        decode_access_token(f"{header_segment}.{forged_payload}.{signature_segment}")
    # 必须报"签名失败"，不能被解析成一份合法令牌
    assert "签名" in str(excinfo.value)


def test_tampered_signature_is_rejected():
    token = create_access_token("u_1003")
    header_segment, payload_segment, signature_segment = token.split(".")

    flipped = ("A" if signature_segment[0] != "A" else "B") + signature_segment[1:]
    with pytest.raises(TokenError) as excinfo:
        decode_access_token(f"{header_segment}.{payload_segment}.{flipped}")
    assert "令牌签名校验失败" in str(excinfo.value)


def test_unparsable_signature_is_rejected():
    """签名段不是合法 base64（这里长度非法）时必须报错，而不是静默通过。"""
    token = create_access_token("u_1004")
    header_segment, payload_segment, _ = token.split(".")
    with pytest.raises(TokenError) as excinfo:
        decode_access_token(f"{header_segment}.{payload_segment}.A")
    assert "无法解析" in str(excinfo.value)


def test_short_but_decodable_signature_is_rejected():
    """签名能解码但内容不对 → 报签名校验失败。"""
    token = create_access_token("u_1004")
    header_segment, payload_segment, _ = token.split(".")
    with pytest.raises(TokenError) as excinfo:
        decode_access_token(f"{header_segment}.{payload_segment}.AAAA")
    assert "签名校验失败" in str(excinfo.value)


def test_expired_token_is_rejected():
    """expires_minutes 为负数 → exp 落在过去。"""
    token = create_access_token("u_1005", expires_minutes=-1)
    assert json.loads(_b64url_decode(token.split(".")[1]))["exp"] < time.time()

    with pytest.raises(TokenError) as excinfo:
        decode_access_token(token)
    assert "过期" in str(excinfo.value)


@pytest.mark.parametrize("alg", ["none", "None", "RS256"])
def test_alg_none_and_other_algorithms_are_rejected(alg: str):
    """安全回归：算法混淆攻击必须被拒。

    这里连签名都用真实密钥算对了，唯一的问题就是 header 里的 alg 不是 HS256，
    仍然必须拒绝（不能因为签名"看起来合法"就放行）。
    """
    header_segment = _b64url(
        json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    )
    payload_segment = _b64url(
        json.dumps({"sub": "u_attacker", "exp": int(time.time()) + 600}, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _b64url(
        hmac.new(get_secret_key().encode("utf-8"), signing_input, hashlib.sha256).digest()
    )

    with pytest.raises(TokenError) as excinfo:
        decode_access_token(f"{header_segment}.{payload_segment}.{signature}")
    assert alg in str(excinfo.value)


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c.d", None])
def test_malformed_tokens_raise_token_error(token):
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_payload_must_be_a_json_object():
    """负载签名合法但不是一个对象时也必须拒绝。"""
    header_segment = _b64url(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    )
    payload_segment = _b64url(json.dumps(["not", "an", "object"], separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _b64url(
        hmac.new(get_secret_key().encode("utf-8"), signing_input, hashlib.sha256).digest()
    )

    with pytest.raises(TokenError) as excinfo:
        decode_access_token(f"{header_segment}.{payload_segment}.{signature}")
    assert "负载" in str(excinfo.value)


def test_token_without_subject_is_rejected():
    token = create_access_token("u_1", extra={"sub": ""})
    with pytest.raises(TokenError) as excinfo:
        decode_access_token(token)
    assert "subject" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 密钥
# ---------------------------------------------------------------------------


def test_get_secret_key_prefers_configured_key(monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_SECRET_KEY", "configured-secret")
    assert get_secret_key() == "configured-secret"


def test_get_secret_key_falls_back_to_stable_ephemeral(monkeypatch):
    """未配置密钥时进程内生成一把，且同一进程内保持稳定（否则令牌会随机失效）。"""
    monkeypatch.setattr(webapp_config, "AUTH_SECRET_KEY", "")
    first = get_secret_key()
    assert first
    assert get_secret_key() == first


# ---------------------------------------------------------------------------
# Bearer 解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),  # 大小写不敏感
        ("BEARER abc", "abc"),
        ("Bearer   abc  ", "abc"),  # 多余空白被裁掉
        ("Basic abc", None),
        ("abc", None),
        ("Bearer", None),
        ("Bearer   ", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_bearer_token(header, expected):
    assert extract_bearer_token(header) == expected


# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------


def _build_app(accounts: AccountManager) -> FastAPI:
    """最小应用：只挂两条受保护路由，用于验证依赖注入行为。"""
    app = FastAPI()
    app.state.accounts = accounts

    @app.get("/me")
    def me(user: User = Depends(get_current_user)):
        return {"id": user.id, "role": user.role}

    @app.get("/admin")
    def admin(user: User = Depends(get_admin_user)):
        return {"id": user.id, "role": user.role}

    return app


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """临时账号库 + 默认关闭认证；需要开认证的用例自行 monkeypatch 打开。"""
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", False)
    accounts = AccountManager(tmp_path / "accounts.db")
    with TestClient(_build_app(accounts)) as client:
        yield client


def _accounts(client: TestClient) -> AccountManager:
    return client.app.state.accounts


def _make_user(accounts: AccountManager, user_id: str, *, role: str = ROLE_USER) -> User:
    """直接造一个指定角色的用户，并签发一把 API Key。

    刻意不走 create_user：密码哈希（scrypt）已在 test_accounts.py 覆盖，
    这里只关心依赖注入，没必要为每条用例多付一次哈希开销。
    """
    accounts.ensure_local_user(user_id, "default")
    accounts.set_role(user_id, role)
    accounts.rotate_api_key(user_id)
    user = accounts.get_user(user_id)
    assert user is not None
    return user


def test_auth_disabled_returns_builtin_local_user(api: TestClient):
    """AUTH_ENABLED=false 时不需要任何请求头，直接是内置本地用户。"""
    resp = api.get("/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == webapp_config.LOCAL_USER_ID
    assert resp.json()["role"] == ROLE_ADMIN  # 内置本地用户具备管理员权限
    assert _accounts(api).count_users() == 1


def test_auth_disabled_admin_route_also_passes(api: TestClient):
    resp = api.get("/admin")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == webapp_config.LOCAL_USER_ID


def test_missing_credentials_returns_401(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)

    resp = api.get("/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "unauthorized"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_invalid_jwt_returns_401(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)

    resp = api.get("/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "unauthorized"


def test_valid_jwt_returns_matching_user(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)
    user = _make_user(_accounts(api), "u_auth_jwt")
    token = create_access_token(user.id, role=user.role)

    resp = api.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == user.id


def test_jwt_for_deleted_user_returns_401(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)
    token = create_access_token("u_deleted")

    resp = api.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "unauthorized"


def test_valid_api_key_returns_matching_user(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)
    user = _make_user(_accounts(api), "u_auth_key")

    resp = api.get("/me", headers={"X-API-Key": user.api_key})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == user.id


def test_wrong_api_key_returns_401(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)

    resp = api.get("/me", headers={"X-API-Key": "sk-solver-wrong-key"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "unauthorized"


def test_bearer_token_takes_priority_over_api_key(api: TestClient, monkeypatch):
    """同时给出合法 JWT 与错误 API Key 时，按 deps 的优先级仍以 Bearer 认证。"""
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)
    user = _make_user(_accounts(api), "u_auth_priority")
    token = create_access_token(user.id)

    resp = api.get("/me", headers={"Authorization": f"Bearer {token}", "X-API-Key": "sk-wrong"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == user.id


def test_normal_user_gets_403_on_admin_route(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)
    user = _make_user(_accounts(api), "u_auth_plain", role=ROLE_USER)
    token = create_access_token(user.id, role=ROLE_USER)

    resp = api.get("/admin", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "forbidden"


def test_admin_user_passes_admin_route(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)
    user = _make_user(_accounts(api), "u_auth_admin", role=ROLE_ADMIN)
    token = create_access_token(user.id, role=ROLE_ADMIN)

    resp = api.get("/admin", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == user.id


def test_admin_route_without_credentials_returns_401(api: TestClient, monkeypatch):
    monkeypatch.setattr(webapp_config, "AUTH_ENABLED", True)

    resp = api.get("/admin")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "unauthorized"


def test_missing_accounts_service_returns_500():
    """app.state.accounts 未装配时 get_accounts 给出明确的 500，而不是静默放行。"""
    app = FastAPI()

    @app.get("/me")
    def me(user: User = Depends(get_current_user)):
        return {"id": user.id}

    with TestClient(app) as client:
        resp = client.get("/me")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "账户服务未初始化"
