# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 配置文件 (V2.2 - 多模型/多端点版)

配置管理最佳实践：
1. 避免使用 import *，显式导入需要的变量
2. 所有配置项使用大写命名（常量约定）
3. 敏感信息（API密钥）从环境变量读取
4. 路径配置使用 pathlib.Path
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from .prompts import (
    CLASSIFICATION_PROMPT,
    TRANSCRIPTION_PROMPT,
    TEXT_MERGE_AND_POLISH_PROMPT,
    FILENAME_GENERATION_PROMPT,
    PROMPT_TEMPLATES,
)

# --- 0. 基础设置 ---
load_dotenv()

# --- 1. API 密钥与通用设置 ---
# 从环境变量读取 API 密钥（推荐方式：使用 .env 文件）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
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

# --- 2. 视觉模型配置 (Qwen-VL) ---
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME = "qwen3-vl-flash"
# 专用于视觉推理的、更强大的思考模型
QWEN_VL_THINKING_MODEL_NAME = "qwen3-vl-plus"

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

    "dashscope": {
        "model": "qwen3-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    },
    "zhipu": {
        "model": "glm-4.5",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
    }
}

# --- 5. 求解风格配置 ---
SOLUTION_STYLE = "OPTIMAL"# 'EXPLORATORY' or 'OPTIMAL'

# --- 6. 核心文件路径配置 ---
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
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

# 允许的图片文件扩展名
# 说明：文件监控器只处理这些扩展名的文件
# 注意：新增图片格式需要在此处添加
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')

# 8. 全局热键配置 (用于 silent_screencapper.py)
HOTKEY_CONFIG = {
    # 修饰键虚拟键码列表（例如：Alt, Ctrl, Shift）
    # VK_MENU (0x12) = Alt 键
    # VK_CONTROL (0x11) = Ctrl 键
    # VK_SHIFT (0x10) = Shift 键
    "MODIFIERS_VK": [0x12],  # 0x12 is VK_MENU, which represents the Alt key

    # 主键虚拟键码（ASCII码）
    # 使用 ord() 函数获取字符的虚拟键码
    "KEY_VK": ord('X'),

    # 用于日志输出的字符串表示
    "STRING": "Alt + X"
}

# 远程触发服务端口
# 说明：用于 remote_trigger.py 的 Web 服务器监听端口
# 建议：1024-65535（避免使用系统保留端口 0-1023）
# - 5555：默认端口（易于记忆）
# - 注意：确保防火墙允许此端口
REMOTE_TRIGGER_PORT = 5555


# --- 9. 初始化功能 ---
def initialize_directories():
    """初始化项目所需的目录结构。"""
    print("正在初始化目录结构...")
    for dir_path in [PROCESSED_DIR, SOLUTION_DIR]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"  - 目录 '{dir_path}' 已确认存在。")
        except OSError as e:
            print(f"创建目录 '{dir_path}' 时发生错误: {e}")
            exit(1)


def validate_config() -> None:
    """
    验证所有必需的配置项是否有效。

    检查项目：
    1. API 密钥是否已设置
    2. 关键路径是否存在或可创建
    3. 配置值是否在合理范围内

    Raises:
        ValueError: 当配置验证失败时抛出，包含详细的错误信息
        OSError: 当路径操作失败时抛出

    Example:
        >>> try:
        ...     validate_config()
        ... except ValueError as e:
        ...     print(f"配置错误: {e}")
    """
    errors = []
    warnings = []

    # --- 验证 API 密钥 ---
    if not DASHSCOPE_API_KEY:
        errors.append("DASHSCOPE_API_KEY 未设置，请在 .env 文件中配置")
    if not DEEPSEEK_API_KEY:
        errors.append("DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")
    if not ZHIPU_API_KEY:
        errors.append("ZHIPU_API_KEY 未设置，请在 .env 文件中配置")

    # --- 验证 API 超时设置 ---
    if API_TIMEOUT < 10:
        warnings.append(f"API_TIMEOUT ({API_TIMEOUT}s) 设置过小，可能导致大模型调用超时")
    if API_TIMEOUT > 3600:
        warnings.append(f"API_TIMEOUT ({API_TIMEOUT}s) 设置过大，建议在合理范围内")

    # --- 验证重试设置 ---
    if MAX_RETRIES < 0:
        errors.append(f"MAX_RETRIES ({MAX_RETRIES}) 不能为负数")
    if MAX_RETRIES > 10:
        warnings.append(f"MAX_RETRIES ({MAX_RETRIES}) 设置过大，可能导致长时间等待")
    if RETRY_DELAY < 0:
        errors.append(f"RETRY_DELAY ({RETRY_DELAY}s) 不能为负数")

    # --- 验证路径配置 ---
    if not ROOT_DIR.exists():
        try:
            ROOT_DIR.mkdir(parents=True, exist_ok=True)
            warnings.append(f"ROOT_DIR 不存在，已自动创建: {ROOT_DIR}")
        except OSError as e:
            errors.append(f"无法创建 ROOT_DIR: {ROOT_DIR}, 错误: {e}")

    if not MONITOR_DIR.exists():
        try:
            MONITOR_DIR.mkdir(parents=True, exist_ok=True)
            warnings.append(f"MONITOR_DIR 不存在，已自动创建: {MONITOR_DIR}")
        except OSError as e:
            errors.append(f"无法创建 MONITOR_DIR: {MONITOR_DIR}, 错误: {e}")

    # --- 验证求解器配置 ---
    for provider, config in SOLVER_CONFIG.items():
        if "model" not in config or not config["model"]:
            errors.append(f"求解器 '{provider}' 缺少 model 配置")
        if "base_url" not in config or not config["base_url"]:
            errors.append(f"求解器 '{provider}' 缺少 base_url 配置")

    # --- 验证路由配置 ---
    if "CODING_SOLVER" not in SOLVER_ROUTING_CONFIG:
        errors.append("SOLVER_ROUTING_CONFIG 中缺少 'CODING_SOLVER' 配置")
    if "DEFAULT_SOLVER" not in SOLVER_ROUTING_CONFIG:
        errors.append("SOLVER_ROUTING_CONFIG 中缺少 'DEFAULT_SOLVER' 配置")

    # --- 输出结果 ---
    if warnings:
        print("\n配置警告：")
        for warning in warnings:
            print(f"  ⚠️  {warning}")
        print()

    if errors:
        print("\n配置错误：")
        for error in errors:
            print(f"  ❌ {error}")
        raise ValueError("\n配置验证失败，请修复上述错误后重试。")

    print("✅ 配置验证通过。")

