# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 配置文件 (V2.0 - 多求解器版)

本文件是整个Agent的“战略中心”，负责管理所有可配置参数。
与V1版本相比，V2将所有大型提示词移至 `prompts.py`，并增加了灵活的求解器切换机制。
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# --- 0. 基础设置 ---
# 加载 .env 文件中的环境变量，这是安全管理API密钥的最佳实践。
load_dotenv()
# 导入所有提示词定义
from prompts import *

# --- 1. API 密钥与通用设置 ---
# 从 .env 文件中读取各个平台的API密钥
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
# DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"

# API调用优化配置
API_TIMEOUT = 600.0  # API调用超时时间（秒）
MAX_RETRIES = 2     # 最大重试次数
RETRY_DELAY = 10     # 每次重试延迟

# --- 2. 视觉模型配置 (Qwen-VL) ---
# 负责问题分类和文字转录
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME = "qwen-vl-max"

# --- 3. 文本润色模型配置 ---
# 用于对OCR结果进行初步校对，建议使用速度较快的模型
POLISHING_MODEL_PROVIDER = "deepseek"
POLISHING_MODEL_NAME = "deepseek-chat"

# --- 4. 核心求解器配置 (Solver Configuration) ---
# 这是整个重构的核心。在这里选择你希望使用的最终求解模型。
# 可选的提供商: "deepseek", "dashscope", "zhipu"
SOLVER_PROVIDER = "deepseek"  # <-- 在这里切换模型提供商

# --- 为每个提供商定义具体的模型和API端点 ---
SOLVER_CONFIG = {
    "deepseek": {
        "model": "deepseek-coder", # 或者 "deepseek-reasoner"
        "base_url": "https://api.deepseek.com/v1"
    },
    "dashscope": {
        # 使用通义千问的 Qwen3-Coder-Plus 模型
        "model": "qwen2-72b-instruct",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    },
    "zhipu": {
        # 使用智谱的 GLM-4.5-Pro 模型
        "model": "glm-4.5-pro",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/"
    }
}
# 根据选择的提供商，动态获取当前要使用的模型名称
SOLVER_MODEL_NAME = SOLVER_CONFIG[SOLVER_PROVIDER]["model"]


# --- 5. 求解风格配置 ---
# - 'OPTIMAL': AI将尽力提供时间/空间复杂度最优的标准解法。
# - 'EXPLORATORY': AI将被引导提供一个更易于理解的“次优解”，并附带优化思路。
SOLUTION_STYLE = "OPTIMAL"

# --- 6. 核心文件路径配置 ---
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "solutions"

# --- 7. Agent 行为配置 ---
GROUP_TIMEOUT = 8.0
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')

# --- 8. 初始化功能 ---
def initialize_directories():
    """
    一个辅助函数，在程序启动时自动检查并创建必要的文件夹。
    """
    print("正在初始化目录结构...")
    for dir_path in [PROCESSED_DIR, SOLUTION_DIR]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"  - 目录 '{dir_path}' 已确认存在。")
        except OSError as e:
            print(f"创建目录 '{dir_path}' 时发生错误: {e}")
            exit(1)

