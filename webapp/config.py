"""webapp 配置模块"""

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
PORT = 8000

# --- 业务配置 ---
MAX_HISTORY = 100          # 最多保留的历史任务数
MAX_UPLOAD_SIZE = 50       # 单次上传总大小限制 (MB)
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
