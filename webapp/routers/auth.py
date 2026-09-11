"""
routers/auth.py - 注册、登录、当前用户信息

对应 new_plan.md 阶段四。与原计划的差异：
- 短信验证码默认用 `SMS_PROVIDER=console`，验证码直接回显在响应里，
  便于本地/自测；生产接入真实短信服务时改成对应 provider 即可。
- 不再调用 LiteLLM 创建虚拟密钥，改为在本地 users 表生成 API Key（见 accounts.py）。
"""

from __future__ import annotations

import logging
import random
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .. import config
from ..accounts import AccountManager, User, hash_password, mask_key
from ..auth import create_access_token
from ..deps import get_accounts, get_current_user

logger = logging.getLogger("AuthRouter")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# 中国大陆手机号
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


class PhoneRequest(BaseModel):
    phone: str = Field(..., description="手机号")


class RegisterRequest(BaseModel):
    phone: str
    code: str
    password: str | None = Field(default=None, description="留空则使用验证码作为初始密码")


class LoginRequest(BaseModel):
    phone: str
    password: str


def _validate_phone(phone: str) -> str:
    phone = (phone or "").strip()
    if not PHONE_PATTERN.match(phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_phone", "message": "手机号格式不正确"},
        )
    return phone


def _issue_token(user: User, accounts: AccountManager) -> dict:
    accounts.touch_login(user.id)
    token = create_access_token(user.id, role=user.role, tenant_id=user.tenant_id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_minutes": config.ACCESS_TOKEN_EXPIRE_MINUTES,
        "user": user.to_public_dict(),
    }


@router.post("/send-code")
async def send_code(payload: PhoneRequest, accounts: AccountManager = Depends(get_accounts)):
    """发送短信验证码。

    `SMS_PROVIDER=console`（默认）时不真正发短信，而是把验证码放在响应的
    `debug_code` 字段里返回——仅供本地开发与自测，生产请改成真实短信服务。
    """
    phone = _validate_phone(payload.phone)

    code = "".join(str(random.randint(0, 9)) for _ in range(config.SMS_CODE_LENGTH))
    accounts.save_sms_code(phone, code, config.SMS_CODE_TTL_SECONDS)

    response: dict = {
        "ok": True,
        "provider": config.SMS_PROVIDER,
        "expires_in": config.SMS_CODE_TTL_SECONDS,
    }
    if config.SMS_PROVIDER == "console":
        response["debug_code"] = code
        logger.info("开发模式验证码 %s -> %s", phone, code)
    else:
        # 其它 provider 尚未接入，明确告知而不是静默失败
        response["ok"] = False
        response["message"] = (
            f"SMS_PROVIDER={config.SMS_PROVIDER} 尚未接入，请使用 console 或实现对应发送逻辑"
        )
    return response


@router.post("/register")
async def register(payload: RegisterRequest, accounts: AccountManager = Depends(get_accounts)):
    """注册并直接返回登录令牌。

    注册成功即赠送 `DEFAULT_USER_BUDGET` 额度；手机号命中 `ADMIN_PHONES` 时授予管理员角色。
    """
    phone = _validate_phone(payload.phone)

    if accounts.get_user_by_phone(phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "phone_taken", "message": "手机号已注册"},
        )

    if not accounts.verify_sms_code(phone, payload.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "bad_code", "message": "验证码不正确或已过期"},
        )

    role = "admin" if phone in config.ADMIN_PHONES else "user"
    user = accounts.create_user(
        phone=phone,
        password=payload.password or phone[-6:],
        role=role,
        tenant_id=config.LOCAL_TENANT_ID,
        budget=config.DEFAULT_USER_BUDGET,
    )
    return _issue_token(user, accounts)


@router.post("/login")
async def login(payload: LoginRequest, accounts: AccountManager = Depends(get_accounts)):
    """手机号 + 密码登录。"""
    phone = (payload.phone or "").strip()
    user = accounts.get_user_by_phone(phone)
    if user is None or not accounts.verify_user_password(user.id, payload.password):
        # 不区分"用户不存在"与"密码错误"，避免枚举手机号
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "bad_credentials", "message": "手机号或密码错误"},
        )
    return _issue_token(user, accounts)


@router.get("/me")
async def me(
    user: User = Depends(get_current_user),
    accounts: AccountManager = Depends(get_accounts),
):
    """当前用户信息：额度、已用、用量汇总与密钥掩码。"""
    return {
        "user": user.to_public_dict(),
        "usage": accounts.usage_summary(user.id),
        "auth_enabled": config.AUTH_ENABLED,
    }


@router.post("/api-key/rotate")
async def rotate_api_key(
    user: User = Depends(get_current_user),
    accounts: AccountManager = Depends(get_accounts),
):
    """重新生成 API Key（旧 Key 立即失效）。返回完整新 Key，仅此一次可见。"""
    new_key = accounts.rotate_api_key(user.id)
    if new_key is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "用户不存在"})
    logger.info("用户 %s 轮换了 API Key", user.id)
    return {"api_key": new_key, "api_key_masked": mask_key(new_key)}


__all__ = ["router", "hash_password"]
