"""
accounts.py - 用户、API 密钥、用量与额度

对应 new_plan.md 的阶段零/阶段四，但**去掉了外部依赖**：

计划原方案用 LiteLLM 的虚拟密钥 + Postgres 做预算与消费追踪。实际落地时
那需要额外跑 Postgres 与 LiteLLM 两个服务，而本项目的定位是"本地部署、
开箱可用"。因此这里自研一层等价的、极简的账户体系：

- `users`      账号、角色、租户、密码哈希、额度余量
- `api_keys`   每个用户一把密钥（用于开放 API 调用，等价于 LiteLLM 虚拟密钥）
- `usage_events` 每次模型调用的用量流水（provider/model/tokens/费用）

费用用"按 token 估算"的方式计算，单价表可配置（见 COST_TABLE），
不依赖任何外部记账服务。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("Accounts")

# 角色
ROLE_USER = "user"
ROLE_ADMIN = "admin"

# 每百万 token 的估算单价（元）。用于额度扣减，可按需调整。
# 说明：这是**估算**而非精确账单，只用于限制滥用；精确账单应查对应平台后台。
COST_TABLE: dict[str, tuple[float, float]] = {
    # model: (输入元/百万token, 输出元/百万token)
    # DeepSeek 求解器与辅助模型统一为 "deepseek-flash"，因此只保留一条单价
    "deepseek-flash": (0.5, 2.0),
    "GLM-4.6V-FlashX": (0.5, 1.5),
    "GLM-4.6V": (2.0, 6.0),
    "default": (2.0, 8.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """按 token 估算一次调用的费用（元）。未知模型走 default 单价。"""
    input_price, output_price = COST_TABLE.get(model, COST_TABLE["default"])
    return round(
        input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price,
        6,
    )


# ---------------------------------------------------------------------------
# 密码哈希：用标准库 scrypt，避免引入 passlib/bcrypt 依赖
# ---------------------------------------------------------------------------

# scrypt 强度参数。n=2**14 单次约 45ms，对登录接口是合适的强度；
# 但测试会反复建用户，把整套用例拖慢到近一分钟。
# 因此在 pytest 进程内自动降档——只影响测试，不影响线上强度。
_SCRYPT_N_DEFAULT = 2**14
_SCRYPT_N_TEST = 2**12


def _is_test_process() -> bool:
    """当前是否跑在 pytest 里。

    注意：不能用 `PYTEST_CURRENT_TEST` 环境变量做模块级判断——那个变量是
    pytest 开始跑用例时才设置的，模块导入阶段还不存在。
    `pytest` 在 `sys.modules` 里则从启动起就成立。
    """
    if "pytest" in sys.modules:
        return True
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _scrypt_n() -> int:
    return _SCRYPT_N_TEST if _is_test_process() else _SCRYPT_N_DEFAULT


def hash_password(password: str, *, salt: str | None = None) -> str:
    """返回 `scrypt$<salt>$<hash>` 格式的密码哈希。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("utf-8"), n=_scrypt_n(), r=8, p=1, dklen=32
    )
    return f"scrypt${salt}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """校验密码。格式非法或为空时一律返回 False（不抛异常）。"""
    if not stored or not password:
        return False
    try:
        scheme, salt, expected = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hash_password(password, salt=salt).split("$", 2)[2]
    return hmac.compare_digest(candidate, expected)


@dataclass
class User:
    id: str
    phone: str
    role: str
    tenant_id: str
    budget: float
    spent: float
    api_key: str | None
    created_at: float
    last_login_at: float | None = None

    @property
    def remaining(self) -> float:
        return round(max(0.0, self.budget - self.spent), 6)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def to_public_dict(self) -> dict:
        """给前端的安全视图：不含密码哈希，密钥做掩码。"""
        return {
            "id": self.id,
            "phone": mask_phone(self.phone),
            "role": self.role,
            "tenant_id": self.tenant_id,
            "budget": round(self.budget, 4),
            "spent": round(self.spent, 4),
            "remaining": self.remaining,
            "api_key_masked": mask_key(self.api_key),
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


def mask_phone(phone: str | None) -> str:
    """138****8000 形式的掩码。"""
    if not phone:
        return ""
    if len(phone) < 7:
        return phone[:1] + "***"
    return f"{phone[:3]}****{phone[-4:]}"


def mask_key(key: str | None) -> str:
    """sk-abc...xyz 形式的掩码。"""
    if not key:
        return ""
    if len(key) <= 12:
        return key[:4] + "***"
    return f"{key[:7]}...{key[-4:]}"


def generate_api_key(prefix: str = "sk-solver") -> str:
    return f"{prefix}-{secrets.token_urlsafe(32)}"


class BudgetExceededError(Exception):
    """额度不足。"""

    def __init__(self, message: str, *, remaining: float = 0.0, required: float = 0.0):
        super().__init__(message)
        self.remaining = remaining
        self.required = required


class AccountManager:
    """账号、密钥、用量与额度的持久化操作。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ---- 连接与建表 ----

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id              TEXT PRIMARY KEY,
                    phone           TEXT UNIQUE,
                    hashed_password TEXT,
                    role            TEXT NOT NULL DEFAULT 'user',
                    tenant_id       TEXT NOT NULL DEFAULT 'default',
                    budget          REAL NOT NULL DEFAULT 0,
                    spent           REAL NOT NULL DEFAULT 0,
                    api_key         TEXT,
                    created_at      REAL NOT NULL,
                    last_login_at   REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users (phone)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_api_key ON users (api_key)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_events (
                    id            TEXT PRIMARY KEY,
                    user_id       TEXT NOT NULL,
                    tenant_id     TEXT NOT NULL DEFAULT 'default',
                    task_id       TEXT,
                    provider      TEXT NOT NULL DEFAULT '',
                    model         TEXT NOT NULL DEFAULT '',
                    stage         TEXT NOT NULL DEFAULT '',
                    input_tokens  INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cost          REAL NOT NULL DEFAULT 0,
                    created_at    REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_user_time ON usage_events (user_id, created_at DESC)"
            )
            conn.commit()

    # ---- 用户 ----

    def ensure_local_user(self, user_id: str, tenant_id: str) -> User:
        """AUTH_ENABLED=false 时使用的内置单用户。"""
        existing = self.get_user(user_id)
        if existing:
            return existing
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, phone, role, tenant_id, budget, spent, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (user_id, None, ROLE_ADMIN, tenant_id, 1e9, now),
            )
            conn.commit()
        logger.info("已确保内置本地用户存在: %s", user_id)
        user = self.get_user(user_id)
        assert user is not None
        return user

    def create_user(
        self,
        *,
        phone: str,
        password: str,
        role: str = ROLE_USER,
        tenant_id: str = "default",
        budget: float = 0.0,
    ) -> User:
        user_id = f"u_{secrets.token_hex(8)}"
        now = time.time()
        api_key = generate_api_key()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO users (id, phone, hashed_password, role, tenant_id, budget, spent, api_key, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (user_id, phone, hash_password(password), role, tenant_id, budget, api_key, now),
            )
            conn.commit()
        user = self.get_user(user_id)
        assert user is not None
        logger.info("新用户注册: %s (%s)", user_id, mask_phone(phone))
        return user

    def get_user(self, user_id: str) -> User | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None

    def get_hashed_password(self, user_id: str) -> str | None:
        """取密码哈希，供登录校验使用。

        `User` 是面向接口的安全视图（不含哈希），因此单独提供这个方法，
        而不是让调用方绕过封装去翻数据库。
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT hashed_password FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return row[0] if row else None

    def verify_user_password(self, user_id: str, password: str) -> bool:
        """校验某个用户的密码。"""
        return verify_password(password, self.get_hashed_password(user_id))

    def get_user_by_phone(self, phone: str) -> User | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return _row_to_user(row) if row else None

    def get_user_by_api_key(self, api_key: str) -> User | None:
        if not api_key:
            return None
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)).fetchone()
        return _row_to_user(row) if row else None

    def touch_login(self, user_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (time.time(), user_id))
            conn.commit()

    def set_role(self, user_id: str, role: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            conn.commit()
            return cursor.rowcount > 0

    def set_budget(self, user_id: str, budget: float) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute("UPDATE users SET budget = ? WHERE id = ?", (float(budget), user_id))
            conn.commit()
            return cursor.rowcount > 0

    def rotate_api_key(self, user_id: str) -> str | None:
        new_key = generate_api_key()
        with self._get_conn() as conn:
            cursor = conn.execute("UPDATE users SET api_key = ? WHERE id = ?", (new_key, user_id))
            conn.commit()
            if cursor.rowcount == 0:
                return None
        return new_key

    def list_users(self, *, limit: int = 100, offset: int = 0) -> list[User]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_user(row) for row in rows]

    def count_users(self) -> int:
        with self._get_conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    # ---- 额度 ----

    def check_budget(self, user_id: str, required: float) -> None:
        """额度不足时抛出 BudgetExceededError。"""
        user = self.get_user(user_id)
        if user is None:
            raise BudgetExceededError("用户不存在", remaining=0.0, required=required)
        if user.remaining < required:
            raise BudgetExceededError(
                f"额度不足：剩余 {user.remaining:.4f} 元，本次至少需要 {required:.4f} 元",
                remaining=user.remaining,
                required=required,
            )

    # ---- 用量 ----

    def record_usage(
        self,
        *,
        user_id: str,
        stage: str,
        provider: str = "",
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        task_id: str | None = None,
        tenant_id: str = "default",
    ) -> dict:
        """记录一次调用并累加用户消费。

        Returns:
            本次记录（含估算费用）。
        """
        cost = estimate_cost(model, input_tokens, output_tokens)
        event_id = f"e_{secrets.token_hex(8)}"
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO usage_events (id, user_id, tenant_id, task_id, provider, model, stage, "
                "input_tokens, output_tokens, cost, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, user_id, tenant_id, task_id, provider, model, stage,
                 input_tokens, output_tokens, cost, now),
            )
            conn.execute("UPDATE users SET spent = spent + ? WHERE id = ?", (cost, user_id))
            conn.commit()
        return {
            "id": event_id,
            "user_id": user_id,
            "task_id": task_id,
            "stage": stage,
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "created_at": now,
        }

    def list_usage(self, user_id: str, *, limit: int = 50) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM usage_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def usage_summary(self, user_id: str) -> dict:
        """聚合用量：总量、分模型、分阶段。"""
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS input_tokens, "
                "COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cost),0) AS cost "
                "FROM usage_events WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            by_model = conn.execute(
                "SELECT model, COUNT(*) AS calls, COALESCE(SUM(cost),0) AS cost "
                "FROM usage_events WHERE user_id = ? GROUP BY model ORDER BY cost DESC",
                (user_id,),
            ).fetchall()
            by_stage = conn.execute(
                "SELECT stage, COUNT(*) AS calls, COALESCE(SUM(cost),0) AS cost "
                "FROM usage_events WHERE user_id = ? GROUP BY stage ORDER BY cost DESC",
                (user_id,),
            ).fetchall()
        return {
            "calls": int(total["calls"]),
            "input_tokens": int(total["input_tokens"]),
            "output_tokens": int(total["output_tokens"]),
            "cost": round(float(total["cost"]), 6),
            "by_model": [dict(row) for row in by_model],
            "by_stage": [dict(row) for row in by_stage],
        }

    def global_usage_summary(self) -> dict:
        """全局用量（管理员看板）。"""
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(cost),0) AS cost, "
                "COALESCE(SUM(input_tokens),0) AS input_tokens, "
                "COALESCE(SUM(output_tokens),0) AS output_tokens FROM usage_events"
            ).fetchone()
            by_user = conn.execute(
                "SELECT u.id, u.phone, u.role, u.budget, u.spent, "
                "COALESCE(SUM(e.cost),0) AS cost, COUNT(e.id) AS calls "
                "FROM users u LEFT JOIN usage_events e ON e.user_id = u.id "
                "GROUP BY u.id ORDER BY cost DESC LIMIT 20"
            ).fetchall()
        return {
            "calls": int(total["calls"]),
            "cost": round(float(total["cost"]), 6),
            "input_tokens": int(total["input_tokens"]),
            "output_tokens": int(total["output_tokens"]),
            "top_users": [
                {
                    **{k: row[k] for k in ("id", "role", "budget", "spent")},
                    "phone": mask_phone(row["phone"]),
                    "cost": round(float(row["cost"]), 6),
                    "calls": int(row["calls"]),
                }
                for row in by_user
            ],
        }

    # ---- 短信验证码（内存实现，够本地/单机使用）----

    def save_sms_code(self, phone: str, code: str, ttl: int) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sms_codes (phone TEXT PRIMARY KEY, code TEXT, expires_at REAL)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO sms_codes (phone, code, expires_at) VALUES (?, ?, ?)",
                (phone, code, time.time() + ttl),
            )
            conn.commit()

    def verify_sms_code(self, phone: str, code: str) -> bool:
        """校验并消费验证码（一次性）。"""
        with self._get_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sms_codes (phone TEXT PRIMARY KEY, code TEXT, expires_at REAL)"
            )
            row = conn.execute(
                "SELECT code, expires_at FROM sms_codes WHERE phone = ?", (phone,)
            ).fetchone()
            if not row:
                return False
            if row["expires_at"] < time.time():
                conn.execute("DELETE FROM sms_codes WHERE phone = ?", (phone,))
                conn.commit()
                return False
            matched = hmac.compare_digest(str(row["code"]), str(code))
            if matched:
                conn.execute("DELETE FROM sms_codes WHERE phone = ?", (phone,))
                conn.commit()
            return matched


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        phone=row["phone"] or "",
        role=row["role"] or ROLE_USER,
        tenant_id=row["tenant_id"] or "default",
        budget=float(row["budget"] or 0.0),
        spent=float(row["spent"] or 0.0),
        api_key=row["api_key"],
        created_at=float(row["created_at"] or 0.0),
        last_login_at=float(row["last_login_at"]) if row["last_login_at"] else None,
    )


def parse_usage_payload(payload: str | None) -> dict:
    """解析可能存在 usage 明细的 JSON 字符串（容错）。"""
    if not payload:
        return {}
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}
