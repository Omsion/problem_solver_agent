"""
routers/admin.py - 管理员接口：用户管理与用量看板

对应 new_plan.md 阶段五。原计划使用 `@authhero/admin`（react-admin 封装），
核实后发现它是 AuthHero 这个**认证产品**的专用管理 UI，并非通用的 react-admin
发行版，且为 AGPL-3.0；接入它需要把认证也换成 AuthHero，与项目既有 JWT 体系冲突。
因此改为：后端提供与 react-admin 约定兼容的 REST 接口，前端用现有技术栈自建看板。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..accounts import AccountManager, User
from ..deps import get_accounts, get_admin_user

logger = logging.getLogger("AdminRouter")

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class BudgetUpdate(BaseModel):
    budget: float


class RoleUpdate(BaseModel):
    role: str


@router.get("/dashboard")
async def dashboard(
    _: User = Depends(get_admin_user),
    accounts: AccountManager = Depends(get_accounts),
):
    """总览：用户数、总调用、总花费、Top 用户。"""
    global_usage = accounts.global_usage_summary()
    return {
        "total_users": accounts.count_users(),
        **global_usage,
    }


@router.get("/users")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(get_admin_user),
    accounts: AccountManager = Depends(get_accounts),
):
    """用户列表。

    返回 `{"data": [...], "total": n}` 结构，与 react-admin 的
    `simpleRestProvider` 约定一致，便于将来直接接入任意管理前端。
    """
    users = accounts.list_users(limit=limit, offset=skip)
    return {
        "data": [user.to_public_dict() for user in users],
        "total": accounts.count_users(),
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: str,
    _: User = Depends(get_admin_user),
    accounts: AccountManager = Depends(get_accounts),
):
    """单个用户详情 + 用量汇总 + 最近流水。"""
    user = accounts.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "用户不存在"})
    return {
        "user": user.to_public_dict(),
        "usage": accounts.usage_summary(user_id),
        "events": accounts.list_usage(user_id, limit=50),
    }


@router.patch("/users/{user_id}/budget")
async def update_budget(
    user_id: str,
    payload: BudgetUpdate,
    _: User = Depends(get_admin_user),
    accounts: AccountManager = Depends(get_accounts),
):
    """调整用户额度（元）。"""
    if payload.budget < 0:
        raise HTTPException(status_code=400, detail={"code": "bad_budget", "message": "额度不能为负"})
    if not accounts.set_budget(user_id, payload.budget):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "用户不存在"})
    user = accounts.get_user(user_id)
    assert user is not None
    logger.info("管理员将用户 %s 额度调整为 %.4f", user_id, payload.budget)
    return {"user": user.to_public_dict()}


@router.patch("/users/{user_id}/role")
async def update_role(
    user_id: str,
    payload: RoleUpdate,
    _: User = Depends(get_admin_user),
    accounts: AccountManager = Depends(get_accounts),
):
    """调整用户角色（user / admin）。"""
    role = payload.role.strip().lower()
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail={"code": "bad_role", "message": "角色只能是 user 或 admin"})
    if not accounts.set_role(user_id, role):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "用户不存在"})
    user = accounts.get_user(user_id)
    assert user is not None
    logger.info("管理员将用户 %s 角色调整为 %s", user_id, role)
    return {"user": user.to_public_dict()}


@router.get("/usage")
async def usage_events(
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(get_admin_user),
    accounts: AccountManager = Depends(get_accounts),
):
    """全局用量流水（概览）。"""
    summary = accounts.global_usage_summary()
    return {"summary": summary, "limit": limit}
