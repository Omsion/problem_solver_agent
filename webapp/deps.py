"""
deps.py - FastAPI 依赖注入：当前用户、管理员校验

支持三种身份来源，按优先级：
1. `Authorization: Bearer <jwt>`  —— 网页登录后的令牌
2. `X-API-Key: <key>`             —— 开放 API 调用（等价于 LiteLLM 虚拟密钥）
3. 内置本地用户                     —— 仅当 AUTH_ENABLED=false

`AUTH_ENABLED=false` 时所有请求都视为同一个本地用户且跳过额度校验，
行为与改造前完全一致，便于单人自用与回归测试。
"""

from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, Request, status

from . import config
from .accounts import AccountManager, User
from .auth import TokenError, decode_access_token, extract_bearer_token

logger = logging.getLogger("Deps")


def get_accounts(request: Request) -> AccountManager:
    """从应用状态取账户管理器（由 create_app 注入）。"""
    manager = getattr(request.app.state, "accounts", None)
    if manager is None:  # pragma: no cover - 说明装配有问题
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="账户服务未初始化",
        )
    return manager


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    accounts: AccountManager = Depends(get_accounts),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    token: str | None = None,
    api_key: str | None = None,
) -> User:
    """解析当前用户，兼容四种来源：

    1. `Authorization: Bearer <jwt>`
    2. `X-API-Key: <key>`
    3. `?token=<jwt>` 查询参数
    4. `?api_key=<key>` 查询参数

    为什么需要查询参数：浏览器的 `EventSource` **无法自定义请求头**，而任务进度
    依赖 SSE 流式推送。若只支持请求头，开启登录后 `AUTH_ENABLED=true` 时
    `/api/tasks/{id}/stream` 会直接 401，核心功能失效。这是 SSE 生态里的事实标准做法。

    安全代价（已知并接受）：查询参数会进入服务器访问日志与浏览器历史，
    因此**仅在无法使用请求头时才用**，且只建议在局域网/自用场景这么做。
    生产环境若暴露公网，应改用 Cookie 会话或把令牌放进 URL fragment。

    `AUTH_ENABLED=false` 时直接返回内置本地用户，不要求任何凭证。
    """
    if not config.AUTH_ENABLED:
        return accounts.ensure_local_user(config.LOCAL_USER_ID, config.LOCAL_TENANT_ID)

    # 1) Bearer JWT
    bearer = extract_bearer_token(authorization)
    if bearer:
        try:
            payload = decode_access_token(bearer)
        except TokenError as exc:
            raise _unauthorized(f"登录已失效：{exc}") from exc
        user = accounts.get_user(str(payload.get("sub")))
        if user is None:
            raise _unauthorized("账号不存在或已被删除")
        return user

    # 3) 查询参数里的 token（SSE 场景）
    if token:
        try:
            payload = decode_access_token(token)
        except TokenError as exc:
            raise _unauthorized(f"登录已失效：{exc}") from exc
        user = accounts.get_user(str(payload.get("sub")))
        if user is None:
            raise _unauthorized("账号不存在或已被删除")
        return user

    # 2) API Key（请求头优先）
    if x_api_key:
        user = accounts.get_user_by_api_key(x_api_key.strip())
        if user is None:
            raise _unauthorized("API Key 无效")
        return user

    # 4) 查询参数里的 API Key
    if api_key:
        user = accounts.get_user_by_api_key(api_key.strip())
        if user is None:
            raise _unauthorized("API Key 无效")
        return user

    raise _unauthorized("请在请求头中提供 Bearer 令牌或 X-API-Key")


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """要求管理员角色。"""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "需要管理员权限"},
        )
    return user


def get_optional_user(
    request: Request,
    accounts: AccountManager = Depends(get_accounts),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    token: str | None = None,
    api_key: str | None = None,
) -> User | None:
    """可选身份：未登录返回 None 而不是 401（用于探活等公开端点）。"""
    if not config.AUTH_ENABLED:
        return accounts.ensure_local_user(config.LOCAL_USER_ID, config.LOCAL_TENANT_ID)
    try:
        return get_current_user(request, accounts, authorization, x_api_key, token, api_key)
    except HTTPException:
        return None
