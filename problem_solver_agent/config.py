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
# GLM-OCR 专用模型（layout_parsing API，高精度文档解析）
GLM_OCR_MODEL = "glm-ocr"
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
SOLUTION_STYLE = "OPTIMAL"# 'EXPLORATORY' or 'OPTIMAL'

# --- 6. 核心文件路径配置 ---
# ROOT_DIR 自动检测为项目父目录，可通过环境变量 SOLVER_ROOT_DIR 覆盖
# 例: 项目位于 D:\Pictures\OnlineTest\ → ROOT_DIR = D:\Pictures
_PROJECT_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = Path(os.getenv("SOLVER_ROOT_DIR", str(_PROJECT_DIR.parent)))
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "solutions"

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
# - 8：高并发（需确保 API 有足够并发配额）
NUM_WORKERS = 4

# OCR 并行线程数（GLM-OCR layout_parsing API）
# 说明：GLM-OCR 仅 0.9B 参数，推理延迟极低，支持高并发调用。
#       每张图片独立调用 layout_parsing，线程池并行执行。
# 建议：1-8（根据 API 并发配额和图片数量调整）
# - 1：串行（保守）
# - 4：四图并行（推荐，GLM-OCR 轻量级模型可轻松支撑）
# - 8：八图并行（大批量场景，需确认 API 配额充足）
OCR_PARALLEL_WORKERS = 4

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

