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
# 注意：项目内 DeepSeek 模型已统一为 "deepseek-flash"（求解器与辅助模型同款）。
AUX_PROVIDER = "deepseek"
AUX_MODEL_NAME = "deepseek-flash"

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
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com/v1"},
}

# 求解调用的输出上限（token）——**首选档**（不开思考时就是全部给正文）。
# 注意：思考模式下思考过程与正文共享这一配额，被思考吃满时正文会是空的
# （solver_client 会自动关闭思考模式重试 / 由流水线升级到思考档重跑）。
SOLVER_MAX_TOKENS = int(os.getenv("SOLVER_MAX_TOKENS", "16000"))

# --- 4.1 思考模式：首选与"按需升级" ---
# 实测（2026-09-13）：难题上思考过程会写掉 2.6 万字符，把 16000 配额吃光后
# finish_reason=length、正文 0 字符——等于白等约 70 秒还多花一份 token，
# 并没有换来正确率。因此改成两段式：
#   1) 先按"不开思考"快跑一次（简单题十几秒出答案）；
#   2) 只有答案不合格（空 / 被截断 / 过短）时，才升级到"开思考 + 大配额"重跑。
# 显式指定（网页上手动开思考、resolve 传参）永远优先，不受这里的默认值影响。
SOLVER_THINKING_DEFAULT = os.getenv("SOLVER_THINKING_DEFAULT", "false").lower() in ("true", "1", "yes")
SOLVER_ESCALATE_TO_THINKING = os.getenv("SOLVER_ESCALATE_TO_THINKING", "true").lower() in ("true", "1", "yes")
# 升级档的输出上限。实测该 API 接受 32768 / 65536，给思考留出写完的余地。
SOLVER_ESCALATE_MAX_TOKENS = int(os.getenv("SOLVER_ESCALATE_MAX_TOKENS", "32000"))
# 首选档答案短于该字符数就视为"不合格"，触发升级（空答案 / 被截断同样触发）
SOLVER_ESCALATE_MIN_CHARS = int(os.getenv("SOLVER_ESCALATE_MIN_CHARS", "500"))
# 思考深度：low / medium / high
SOLVER_REASONING_EFFORT = os.getenv("SOLVER_REASONING_EFFORT", "medium").strip().lower()
if SOLVER_REASONING_EFFORT not in ("low", "medium", "high"):
    SOLVER_REASONING_EFFORT = "medium"
# 思考过程的字符上限：超过它且正文仍为 0 时提前放弃思考（0 = 关闭该保护）。
# 升级档配了大配额，默认不再提前放弃，避免把可能写完的思考掐掉。
SOLVER_REASONING_CHAR_LIMIT = int(os.getenv("SOLVER_REASONING_CHAR_LIMIT", "0"))

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
# 合并调用（一次拿到"题型 + 全部逐页转录"）的开关模式：
#   "auto"（默认）= 只有图片数 <= COMBINED_VISION_MAX_IMAGES 时才尝试
#   "true"        = 总是尝试（历史行为）
#   "false"       = 从不尝试，直接走"分类 + 逐页转录"两步
# 为什么默认不再"总是尝试"：实测（webapp 用量流水）12 次尝试只成功 1 次。
# 多图时一次 JSON 装不下全部转录，输出被 max_tokens=8192 截断 → 解析失败 →
# 白等约 50 秒且这次调用照样计费。单图输出短，收益仍在。
USE_COMBINED_VISION_CALL = os.getenv("USE_COMBINED_VISION_CALL", "auto").strip().lower()
COMBINED_VISION_MAX_IMAGES = int(os.getenv("COMBINED_VISION_MAX_IMAGES", "1"))
# 多图 OCR 合并文本短于该长度时跳过"润色"调用
MERGE_SKIP_THRESHOLD = int(os.getenv("MERGE_SKIP_THRESHOLD", "1200"))


# --- 6. 核心文件路径配置 ---
# ROOT_DIR（"工作根目录"）的解析顺序：
#   1. 环境变量 SOLVER_ROOT_DIR（显式指定，推荐）
#   2. 项目父目录（默认；例如项目在 D:\work\OnlineTest，根目录就是 D:\work）
# 工作根目录下会放 Screenshots/（监控目录）、processed/（原图归档）、solutions/（解答）。
# 启动时（CLI 与 Web 都会）打印这四个实际路径，便于发现"产物跑到别处去了"。
# 注意：`workspace/` 只是历史设想，当前实现**不会**自动用它。
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

# --- 7.1 监控目录的"补偿扫描"（防漏事件）---
# 背景（2026-09-13 事故）：手机照片经 Syncthing 同步进来时是"先写临时文件、
# 再改名就位"，watchdog 对改名只发 on_moved；旧实现只监听 on_created，
# 于是文件躺在监控目录里几个小时也没被处理，而且没有任何补救机制。
# 现在除了补上 on_moved，还会定期扫一遍目录做兜底：
MONITOR_RESCAN_INTERVAL = float(os.getenv("MONITOR_RESCAN_INTERVAL", "15"))
# 只补投 mtime 在这个分钟数以内的文件（0 = 不限年龄）
MONITOR_CATCHUP_MAX_AGE_MINUTES = int(os.getenv("MONITOR_CATCHUP_MAX_AGE_MINUTES", "120"))
# 启动时是否先扫一遍（补上进程停机期间到达的文件）
MONITOR_STARTUP_SCAN = os.getenv("MONITOR_STARTUP_SCAN", "true").lower() in ("true", "1", "yes")

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
