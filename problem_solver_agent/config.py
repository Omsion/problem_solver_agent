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

# --- 2. 视觉模型配置（provider 化）---
# 视觉层用哪家：deepseek（迁移目标）/ zhipu（当前安全基线）。
# 迁移背景与代价量化见 docs/plans/deepseek_vision_migration.md。
#
# 为什么**未显式配置时**默认仍是 zhipu：按计划书 §6 第 5 步，只有双 provider 的
# A/B（`python -m tools.vision_ab check -i <图> --reference <provider>`）跑出
# 数字、S1（OCR 不倒退）/ S2（分类一致率 ≥90%）判定通过之后，才把默认值切到
# deepseek。在那之前，新 provider 必须由 .env 里的 `VISION_PROVIDER=deepseek`
# **显式**启用（本机 .env 就是这么写的，因此这里的默认值不影响已配置的部署）。
# 这样"切换"永远是一次显式动作，而"回退"是从未离开过的状态。
DEFAULT_VISION_PROVIDER = "zhipu"
VISION_PROVIDER = os.getenv("VISION_PROVIDER", DEFAULT_VISION_PROVIDER).strip().lower()

# 每个 provider 的密钥环境变量、端点、模型名与**输出上限**。新增一家只需加一条。
#
# `max_tokens` 必须按 provider 给：DeepSeek 上限 384K（这里取 32768 覆盖 8 页转录），
# 而 GLM-4.6V 系列的输出上限是 **8192** —— 迁移前视觉调用写死的正是 8192。
# 用一个全局值会让 `VISION_PROVIDER=zhipu` 的回退路径发出 32768（要么被 GLM 拒绝、
# 要么被静默截断），S5「回退后与迁移前一致」就不成立了。
VISION_PROVIDER_CONFIG: dict[str, dict[str, str]] = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",   # 官方 OpenAI 兼容端点
        "classify_model": "deepseek-flash",       # 分类 + OCR
        "reasoning_model": "deepseek-flash",      # 视觉推理 + 核对
        # 特殊能力开关：DeepSeek 思考模式**默认开启**，必须显式下发
        # thinking.type=disabled 才会关掉（见下方 VISION_DISABLE_THINKING）。
        "supports_thinking_control": "1",
        "max_tokens": "32768",
    },
    "zhipu": {
        "api_key_env": "ZHIPU_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "classify_model": "GLM-4.6V-FlashX",
        "reasoning_model": "GLM-4.6V",
        "supports_thinking_control": "0",
        # GLM 系列输出上限 8192（迁移前的实际值），不能跟着 DeepSeek 一起提到 32768
        "max_tokens": "8192",
    },
}

# 未知取值一律回落 DEFAULT_VISION_PROVIDER，不抛异常（配置写错不该让服务起不来）
if VISION_PROVIDER not in VISION_PROVIDER_CONFIG:
    VISION_PROVIDER = DEFAULT_VISION_PROVIDER

_VISION_CFG = VISION_PROVIDER_CONFIG[VISION_PROVIDER]

VISION_BASE_URL = _VISION_CFG["base_url"]
VISION_CLASSIFY_MODEL = _VISION_CFG["classify_model"]
# 专用于视觉推理的、更强大的模型
VISION_REASONING_MODEL = _VISION_CFG["reasoning_model"]
# 视觉模型 provider 名称（用于解答文件元数据）
VISION_PROVIDER_NAME = VISION_PROVIDER


def _vision_api_key(provider: str | None = None) -> str | None:
    """取视觉层密钥；provider 为 None 时用当前 VISION_PROVIDER。

    所有校验点（main / pipeline / app / routes / diag）都改用它，这样切换
    provider 时校验逻辑不用跟着改，错误信息也能指出**该配哪个环境变量**。
    """
    cfg = VISION_PROVIDER_CONFIG.get((provider or VISION_PROVIDER).strip().lower())
    if not cfg:
        return None
    return os.getenv(cfg["api_key_env"])


def provider_supports_thinking_control(provider: str | None = None) -> bool:
    """该 provider 是否支持"显式关闭思考模式"的能力位（目前只有 DeepSeek）。

    求解层（`solver_client.ask_for_analysis`）与视觉层共用这一份判定：
    两处各写一遍 `provider == "deepseek"` 的话，将来接入另一家支持该开关的
    provider 时，会有一层静默地继续空转思考（思考 token 照样计费，延迟白等）。
    """
    cfg = VISION_PROVIDER_CONFIG.get((provider or VISION_PROVIDER).strip().lower())
    return bool(cfg and cfg.get("supports_thinking_control") == "1")


# 视觉层密钥统一出口（默认 provider 的）
VISION_API_KEY = _vision_api_key()

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
# 是否关闭视觉调用（分类 / OCR / 视觉推理 / 核对）的思考模式。
# DeepSeek「思考模式默认打开，且 effort 默认 high」——不显式关闭的话，思考过程会
# 把 max_tokens 吃光、正文为空（与 solver 踩过的是同一个坑，见 _build_payload）。
VISION_DISABLE_THINKING = os.getenv("VISION_DISABLE_THINKING", "true").lower() in ("true", "1", "yes")
# 视觉调用的输出上限（**当前 provider** 的值）。
# 取自 provider 表：DeepSeek 默认 32768（旧实现写死 8192，会把多图合并转录截断），
# GLM 系列 8192（迁移前的实际值）。`VISION_MAX_TOKENS` 环境变量可覆盖**当前**
# provider 的值（用于现场调试）；显式指定其它 provider 时走
# `_vision_max_tokens(provider)` 取那一家的表值，不再跟着这里跑。
_ENV_VISION_MAX_TOKENS = os.getenv("VISION_MAX_TOKENS")


def _vision_max_tokens(provider: str | None = None) -> int:
    """某个 provider 的视觉输出上限（token）。

    当前 provider 用模块级 `VISION_MAX_TOKENS`（因此 `.env` 的覆盖与运行时修改都生效）；
    显式指定**其它** provider 时一律用 provider 表里的值 —— 否则 A/B 工具按
    `provider="zhipu"` 跑时会带上 DeepSeek 的 32768，而 GLM 的上限只有 8192。
    """
    name = (provider or VISION_PROVIDER).strip().lower()
    if name == VISION_PROVIDER:
        current = globals().get("VISION_MAX_TOKENS")
        if current:
            return int(current)
    cfg = VISION_PROVIDER_CONFIG.get(name) or _VISION_CFG
    try:
        return int(cfg.get("max_tokens") or 32768)
    except (TypeError, ValueError):
        return 32768


if _ENV_VISION_MAX_TOKENS:
    try:
        VISION_MAX_TOKENS = int(_ENV_VISION_MAX_TOKENS)
    except ValueError:
        VISION_MAX_TOKENS = _vision_max_tokens()
else:
    VISION_MAX_TOKENS = _vision_max_tokens()
# 合并调用（一次带 N 张图）的独立超时。它与逐页 OCR 的输出量差一个数量级，
# 共用 120 s 几乎必然超时，因此单独给 300 s。
VISION_COMBINED_TIMEOUT = float(os.getenv("VISION_COMBINED_TIMEOUT", "300"))
# 合并调用成功后是否**内联**拼接（跳过润色调用）。
# CONT 页的接缝判断来自模型本身，本地 join 即可；发现模型在 CONT 页多删了内容时
# 置 false 即可回到「合并调用 + 独立润色」的老路径。
VISION_INLINE_MERGE = os.getenv("VISION_INLINE_MERGE", "true").lower() in ("true", "1", "yes")
# 辅助调用（润色 / 文件名生成）的超时。润色要重写整篇合并文本（输出 6–10K token），
# 旧的硬编码 120 s 会超时并按 MAX_RETRIES 指数退避重试，一次润色最坏耗掉几分钟。
AUX_TIMEOUT = float(os.getenv("AUX_TIMEOUT", "300"))

# 合并调用（一次拿到"题型 + 全部逐页转录"）的开关模式：
#   "auto"（默认）= 只有图片数 <= COMBINED_VISION_MAX_IMAGES 时才尝试
#   "true"        = 总是尝试（历史行为）
#   "false"       = 从不尝试，直接走"分类 + 逐页转录"两步
# 历史教训（2026-09-13）：合并调用曾用 JSON 协议 + 8192 输出上限，多图必然被截断，
# 实测 12 次尝试只成功 1 次。现在协议换成 <<<PAGE n|NEW/CONT>>> 分隔符（正文逐字直出、
# 不经过转义层）、输出上限提到 VISION_MAX_TOKENS，多图合并才真正可用。
USE_COMBINED_VISION_CALL = os.getenv("USE_COMBINED_VISION_CALL", "auto").strip().lower()
COMBINED_VISION_MAX_IMAGES = int(os.getenv("COMBINED_VISION_MAX_IMAGES", "8"))
# 单次合并请求最多带几张图：超过就**分批 + 批间并行**（组 H2，见下）。
# 为什么是 4 而不是直接把 8 张塞一次请求：2026-09-21 的真实对照（8 张题图，各 2 轮）——
#   一次带 8 张：中位数 6.8 s（1 次请求，单序列串行生成）
#   分批 2×4 并发：≈3.5 s（2 次请求）      ← 本参数生效的路径
#   逐页并行 OCR：3.7 s（9 次请求，图片 token 付两次）
# 分批合并因此在**延迟上追平并行路径**，同时保住"图片只上传一次"与批内 NEW/CONT 去重。
# 详见 docs/plans/deepseek_vision_migration.md §8.2 / §8.5。
VISION_BATCH_SIZE = int(os.getenv("VISION_BATCH_SIZE", "4"))
# 批间并行度。批与批互相独立（每批自带图片与 prompt），因此可以并发；
# 约束同样不是 API 并发，而是本机 JPEG 解码/缩放与服务端首字延迟。
VISION_BATCH_WORKERS = int(os.getenv("VISION_BATCH_WORKERS", "4"))
# 多图 OCR 合并文本短于该长度时跳过"润色"调用
MERGE_SKIP_THRESHOLD = int(os.getenv("MERGE_SKIP_THRESHOLD", "1200"))

# 文件名生成模式：
#   "auto"（默认）= 优先用求解正文首行的 FILE: 建议，其次本地按题号生成，
#                   解析不到题号时才调模型（省掉一次隐形的 deepseek 调用）
#   "local"       = 完全本地生成，从不调模型
#   "model"       = 每次都调模型（旧行为）
FILENAME_MODE = os.getenv("FILENAME_MODE", "auto").strip().lower()
if FILENAME_MODE not in ("auto", "local", "model"):
    FILENAME_MODE = "auto"


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
# 原始逐页 OCR 归档目录（第 1 层落盘）。与 SOLUTION_DIR / PROCESSED_DIR 同级，
# 理由：① Web 与 CLI 自动共用同一份，不需要像解答文件那样再复制一遍；
# ② 手机端 Samba 能直接看到；③ **绝不能**把原始 OCR 塞进解答文件的
# `# 题目文本` 小节 —— extract_problem_text / stripPreamble 都是"从 # 题目文本
# 切到下一个 ---"，塞进去会被当成题目文本喂给换路重解，还会让答案卡抽取错位。
OCR_DIR = ROOT_DIR / "ocr"

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
# 手机遥控截图服务（tools/remote_trigger.py）监听端口；手机扫码后点按钮即可触发电脑截图
REMOTE_TRIGGER_PORT = int(os.getenv("REMOTE_TRIGGER_PORT", "5555"))

# 后台工作线程数（ImageGrouper 消费者线程池大小）
# 说明：控制同时处理的任务数量。每个任务占一个线程，适合 I/O 密集型场景。
# 建议：1-8（根据 CPU 核心数和 API 并发限制调整）
# - 1：单任务串行（最稳定）
# - 4：四任务并发（适合大多数场景）
# OCR 并行线程数
# 说明：控制同时进行的 OCR 转录调用数（只影响**回退路径**，合并调用成功时不走这里）。
# 约束不是 API 并发（DeepSeek 上限 2500，本项目远未触及），而是 image_prep 的
# JPEG 解码/缩放会抢本机 CPU；分类调用已预热内存缓存，OCR 阶段基本不再重编码。
# - 1：串行转录（最稳定，但 8 图就是 8 轮 TTFT 串起来）
# - 4：四张图并行
OCR_PARALLEL_WORKERS = int(os.getenv("OCR_PARALLEL_WORKERS", "4"))

# 后台工作线程数（ImageGrouper 消费者线程池大小）
NUM_WORKERS = 4

# 允许的图片文件扩展名
# 说明：文件监控器只处理这些扩展名的文件
# 注意：新增图片格式需要在此处添加
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')


# ---------------------------------------------------------------------------
