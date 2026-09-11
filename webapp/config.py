"""webapp 配置模块"""

import os
from pathlib import Path

# --- 路径配置 ---
WEBAPP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = WEBAPP_DIR / "uploads"
SOLUTION_DIR = WEBAPP_DIR / "solutions"
DATA_DIR = WEBAPP_DIR / "data"
TEMPLATES_DIR = WEBAPP_DIR / "templates"
STATIC_DIR = WEBAPP_DIR / "static"
DB_PATH = DATA_DIR / "tasks.db"

# --- 服务器配置 ---
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8000"))

# --- 业务配置 ---
MAX_HISTORY = 100          # 最多保留的历史任务数
MAX_UPLOAD_SIZE = 50       # 单次上传总大小限制 (MB)
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# 认证与商业化配置
# ---------------------------------------------------------------------------
# AUTH_ENABLED=false 时退化为"单用户本地模式"：
#   - 所有请求视为同一个内置本地用户，不做登录
#   - 不校验额度，行为与改造前一致
# 这样单人自用时不会被登录流程打扰，也便于回归测试。
AUTH_ENABLED = _env_bool("AUTH_ENABLED", False)

# JWT 签名密钥。生产环境必须显式设置，否则启动时会给出明确警告。
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

# 内置本地用户（AUTH_ENABLED=false 时使用）
LOCAL_USER_ID = os.getenv("LOCAL_USER_ID", "local")
LOCAL_TENANT_ID = os.getenv("LOCAL_TENANT_ID", "default")

# 新用户注册赠送的额度（元）
DEFAULT_USER_BUDGET = float(os.getenv("DEFAULT_USER_BUDGET", "10.0"))
# 单次任务预留额度：低于该值直接拒绝提交，避免"余额不足却跑完一次调用"
MIN_TASK_BUDGET = float(os.getenv("MIN_TASK_BUDGET", "0.05"))

# 短信验证码
# SMS_PROVIDER=console 时验证码直接回显在接口响应里（仅用于本地开发/自测）。
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "console").strip().lower()
SMS_CODE_TTL_SECONDS = int(os.getenv("SMS_CODE_TTL_SECONDS", "300"))
SMS_CODE_LENGTH = int(os.getenv("SMS_CODE_LENGTH", "6"))

# 管理员账号：注册时手机号命中该列表即获得 admin 角色
ADMIN_PHONES = {
    phone.strip()
    for phone in os.getenv("ADMIN_PHONES", "").split(",")
    if phone.strip()
}
