"""
自动化多图解题Agent - 配置文件 (V3.0 - 纯常量版)

本模块只包含配置常量，不含任何函数或业务逻辑。
- 共享流水线函数请见 pipeline.py
- Prompt 模板请见 prompts.py
"""

from dotenv import load_dotenv

import os
from pathlib import Path

# --- 0. 基础设置 ---
load_dotenv()

# --- 1. API 密钥与通用设置 ---
# 从环境变量读取 API 密钥（推荐方式：使用 .env 文件）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")

# API 超时时间（秒）
# 说明：单次 API 调用的最大等待时间
# 建议：30-1200 (0.5分钟 - 20分钟)
# - 简单任务：60-120秒
# - 复杂推理：300-600秒
# - 注意：超时时间过长可能导致用户长时间等待无响应
API_TIMEOUT = 600.0

# 最大重试次数
# 说明：网络错误时的自动重试次数
# 建议：2-5次（避免无限重试，同时提高成功率）
# - 0次：不重试（快速失败）
# - 3次：总共尝试4次（平衡性能和可靠性）
# - 过多：可能导致总等待时间过长
MAX_RETRIES = 3

# 重试延迟时间（秒）
# 说明：每次重试前等待的时间
# 建议：5-15秒（给API服务恢复时间）
# - 过短：可能重复触发限流
# - 过长：用户等待时间增加
RETRY_DELAY = 10

# --- 2. 视觉模型配置 (GLM-4.6V) ---
VISION_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
VISION_CLASSIFY_MODEL = "GLM-4.6V-FlashX"
# 专用于视觉推理的、更强大的模型
VISION_REASONING_MODEL = "GLM-4.6V"
# 视觉模型 provider 名称（用于解答文件元数据）
VISION_PROVIDER_NAME = "zhipu"

# --- 3. 辅助模型配置 (Auxiliary Model Configuration) ---
AUX_PROVIDER = "deepseek"
AUX_MODEL_NAME = "deepseek-v4-flash"

# --- 4. 核心求解器配置 (Solver Configuration) ---
# 配置字典，用于定义问题类型到求解器的映射规则。
SOLVER_ROUTING_CONFIG = {
    # 为编程类问题指定使用 'deepseek' 供应商
    "CODING_SOLVER": "deepseek",

    # 为所有其他问题指定一个默认的求解器
    "DEFAULT_SOLVER": "deepseek"
}

SOLVER_CONFIG = {
    "deepseek": {
        "model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com/v1"},
}

# --- 5. 求解风格配置 ---
# 支持通过 .env 覆盖（SOLVER_STYLE=OPTIMAL / EXPLORATORY）。
# 此前 .env 里的 SOLVER_STYLE 会被这里写死的常量静默忽略。
SOLUTION_STYLE = os.getenv("SOLVER_STYLE", "OPTIMAL").strip().upper()
if SOLUTION_STYLE not in ("OPTIMAL", "EXPLORATORY"):
    SOLUTION_STYLE = "OPTIMAL"

# --- 5.1 图片预处理配置（影响发送给视觉模型的请求体积）---
# 实测：2288×1764 的截图 2.96 MB → 最长边 1600 + JPEG q80 后 187 KB
# （base64 约 243 KB，缩小约 16 倍）。1600px 对文字 OCR 完全够用。
# 若发现小字识别变差，把 IMAGE_MAX_EDGE 调大或把 IMAGE_JPEG_QUALITY 提到 90。
IMAGE_MAX_EDGE = int(os.getenv("IMAGE_MAX_EDGE", "1600"))
IMAGE_JPEG_QUALITY = int(os.getenv("IMAGE_JPEG_QUALITY", "80"))
IMAGE_CACHE_ENABLED = os.getenv("IMAGE_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
# 图片缓存总上限（MB），超出后按最旧优先清理
IMAGE_CACHE_MAX_MB = int(os.getenv("IMAGE_CACHE_MAX_MB", "512"))

# --- 5.2 任务保留策略（防止 uploads/processed 无限增长）---
# 最多保留的任务数，以及任务的最长保留天数（<=0 表示不按天数清理）
TASK_RETENTION_COUNT = int(os.getenv("TASK_RETENTION_COUNT", "100"))
TASK_RETENTION_DAYS = int(os.getenv("TASK_RETENTION_DAYS", "30"))
# 同时处理的任务上限（自动导入 + 手动上传共享），避免高峰期把 API 配额打满
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "2"))

# --- 5.3 视觉模型调用参数 ---
# 分类 / OCR 等短任务用较短超时，避免网络异常时长时间挂起
VISION_TIMEOUT = float(os.getenv("VISION_TIMEOUT", "120"))
# 是否把"分类"与"OCR"合并成一次视觉调用（失败会自动回退到两步）
USE_COMBINED_VISION_CALL = os.getenv("USE_COMBINED_VISION_CALL", "true").lower() in ("true", "1", "yes")
# 多图 OCR 合并文本短于该长度时跳过"润色"调用
MERGE_SKIP_THRESHOLD = int(os.getenv("MERGE_SKIP_THRESHOLD", "1200"))


# --- 6. 核心文件路径配置 ---
# ROOT_DIR 的解析顺序：
#   1. 环境变量 SOLVER_ROOT_DIR（显式指定，推荐）
#   2. 项目根目录下的 workspace/（自包含，不污染用户目录）
#   3. 项目父目录（历史默认行为，保持兼容）
# 说明：历史默认值会把 Screenshots/processed/solutions 建在项目**父目录**，
# 也就是用户的 Pictures 目录里。为兼容既有数据，默认行为不变，但会在启动时
# 明确打印实际路径，便于发现"产物跑到别处去了"。
_PROJECT_DIR = Path(__file__).resolve().parent.parent
_EXPLICIT_ROOT = os.getenv("SOLVER_ROOT_DIR")


def _resolve_root_dir() -> Path:
    if _EXPLICIT_ROOT:
        return Path(_EXPLICIT_ROOT)
    return _PROJECT_DIR.parent


ROOT_DIR = _resolve_root_dir()
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "solutions"

# 图片预处理缓存目录（放在项目内，而不是用户目录）
IMAGE_CACHE_DIR = _PROJECT_DIR / "webapp" / "cache" / "images"

# --- 7. Agent 行为配置 ---
# 分组超时时间（秒）
# 说明：当用户在 GROUP_TIMEOUT 秒内没有新截图时，将当前收集的图片作为一个完整任务提交
# 建议：5-15秒（根据用户操作习惯调整）
# - 5秒：适合快速截图的用户（可能过早分组）
# - 8-10秒：平衡选择（大多数用户适用）
# - 15秒：适合操作较慢的用户（可能延迟提交）
GROUP_TIMEOUT = 8.0

# 后台工作线程数（ImageGrouper 消费者线程池大小）
# 说明：控制同时处理的任务数量。每个任务占一个线程，适合 I/O 密集型场景。
# 建议：1-8（根据 CPU 核心数和 API 并发限制调整）
# - 1：单任务串行（最稳定）
# - 4：四任务并发（适合大多数场景）
# OCR 并行线程数
# 说明：控制同时进行的 OCR 转录调用数。
# - 1：串行转录（最稳定）
# - 2：两张图并行（需确认 API 配额充足）
OCR_PARALLEL_WORKERS = 1

# 后台工作线程数（ImageGrouper 消费者线程池大小）
NUM_WORKERS = 4

# --- 重分类关键词（CLI 和 Web 流水线共享）---
ML_KEYWORDS = [
    "numpy", "torch", "tensorflow", "mlp", "transformer", "注意力",
    "normalization", "norm", "cnn", "rnn", "神经网络", "感知机",
    "反向传播", "前向传播", "mnist", "cifar",
]
CODING_KEYWORDS = ["手撕", "算法", "leetcode", "acm", "代码", "函数", "实现", "编程"]

# 允许的图片文件扩展名
# 说明：文件监控器只处理这些扩展名的文件
# 注意：新增图片格式需要在此处添加
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')


# ---------------------------------------------------------------------------
