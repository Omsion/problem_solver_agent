"""
auth.py - JWT 令牌签发与校验

计划原方案用 `python-jose`，这里改用标准库 `hmac` + `base64` 自实现 HS256。
理由：JWT 的 HS256 就是"对 base64url(header).base64url(payload) 做 HMAC-SHA256"，
标准库十行即可，少一个依赖也少一处供应链风险。

格式与标准 JWT 完全一致，因此前端/第三方仍可用任意 JWT 库解析。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

from . import config

logger = logging.getLogger("Auth")

# 未配置密钥时自动生成一个进程内密钥：单机自用足够，
# 但重启会导致已签发的令牌失效，因此生产必须显式配置 AUTH_SECRET_KEY。
_EPHEMERAL_SECRET: str | None = None


def get_secret_key() -> str:
    global _EPHEMERAL_SECRET
    if config.AUTH_SECRET_KEY:
        return config.AUTH_SECRET_KEY
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_urlsafe(48)
        logger.warning(
            "AUTH_SECRET_KEY 未配置，已生成临时密钥。"
            "服务重启后已签发的登录令牌会全部失效；生产环境请在 .env 中显式设置。"
        )
    return _EPHEMERAL_SECRET


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_access_token(
    subject: str,
    *,
    role: str = "user",
    tenant_id: str = "default",
    expires_minutes: int | None = None,
    extra: dict | None = None,
) -> str:
    """签发访问令牌。"""
    issued_at = int(time.time())
    ttl = expires_minutes if expires_minutes is not None else config.ACCESS_TOKEN_EXPIRE_MINUTES
    payload: dict = {
        "sub": subject,
        "role": role,
        "tenant_id": tenant_id,
        "iat": issued_at,
        "exp": issued_at + ttl * 60,
    }
    if extra:
        payload.update(extra)

    header = {"alg": "HS256", "typ": "JWT"}
    segments = [
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = hmac.new(get_secret_key().encode("utf-8"), signing_input, hashlib.sha256).digest()
    segments.append(_b64url_encode(signature))
    return ".".join(segments)


class TokenError(Exception):
    """令牌非法或过期。"""


def decode_access_token(token: str) -> dict:
    """校验并解析令牌。

    Raises:
        TokenError: 格式错误、签名不匹配或已过期。
    """
    if not token:
        raise TokenError("缺少令牌")

    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("令牌格式不正确")

    header_segment, payload_segment, signature_segment = parts

    try:
        header = json.loads(_b64url_decode(header_segment))
    except (ValueError, TypeError) as exc:
        raise TokenError("令牌头部无法解析") from exc

    if header.get("alg") != "HS256":
        # 明确拒绝 none 算法，避免算法混淆攻击
        raise TokenError(f"不支持的签名算法: {header.get('alg')}")

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected = hmac.new(get_secret_key().encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual = _b64url_decode(signature_segment)
    except (ValueError, TypeError) as exc:
        raise TokenError("令牌签名无法解析") from exc

    if not hmac.compare_digest(expected, actual):
        raise TokenError("令牌签名校验失败")

    try:
        payload = json.loads(_b64url_decode(payload_segment))
    except (ValueError, TypeError) as exc:
        raise TokenError("令牌负载无法解析") from exc

    if not isinstance(payload, dict):
        raise TokenError("令牌负载格式不正确")

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        raise TokenError("令牌缺少过期时间")
    if time.time() > exp:
        raise TokenError("令牌已过期")

    if not payload.get("sub"):
        raise TokenError("令牌缺少 subject")

    return payload


def extract_bearer_token(authorization: str | None) -> str | None:
    """从 `Authorization: Bearer xxx` 中取出令牌。"""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None
