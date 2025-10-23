# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 配置文件 (V2.2 - 多模型/多端点版)
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from prompts import *

# --- 0. 基础设置 ---
load_dotenv()

# --- 1. API 密钥与通用设置 ---
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")

API_TIMEOUT = 600.0
MAX_RETRIES = 2
RETRY_DELAY = 10

# --- 2. 视觉模型配置 (Qwen-VL) ---
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME = "qwen-vl-max"
# 专用于视觉推理的、更强大的思考模型
QWEN_VL_THINKING_MODEL_NAME = "qwen3-vl-235b-a22b-thinking"

# --- 3. 辅助模型配置 (Auxiliary Model Configuration) ---
AUX_PROVIDER = "deepseek"
AUX_MODEL_NAME = "deepseek-chat"

# --- 4. 核心求解器配置 (Solver Configuration) ---
# 配置字典，用于定义问题类型到求解器的映射规则。
SOLVER_ROUTING_CONFIG = {
    # 为编程类问题指定使用 'dashscope' 供应商
    "CODING_SOLVER": "dashscope",

    # 为所有其他问题指定一个默认的求解器
    "DEFAULT_SOLVER": "zhipu"
}

SOLVER_CONFIG = {
    "deepseek": {
        "model": "deepseek-reasoner",
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
SOLUTION_STYLE = "OPTIMAL"

# --- 6. 核心文件路径配置 ---
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "solutions"

# --- 7. Agent 行为配置 ---
GROUP_TIMEOUT = 8.0
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')

# 8. 全局热键配置 (用于 silent_screencapper.py)
HOTKEY_CONFIG = {
    # 定义一个或多个修饰键的虚拟键码列表
    "MODIFIERS_VK": [0x12],  # 0x12 is VK_MENU, which represents the Alt key
    # 定义主键的虚拟键码
    "KEY_VK": ord('X'),
    # 用于日志输出的字符串
    "STRING": "Alt + X"
}
REMOTE_TRIGGER_PORT = 5555

# --- 9. 初始化功能 ---
def initialize_directories():
    print("正在初始化目录结构...")
    for dir_path in [PROCESSED_DIR, SOLUTION_DIR]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"  - 目录 '{dir_path}' 已确认存在。")
        except OSError as e:
            print(f"创建目录 '{dir_path}' 时发生错误: {e}")
            exit(1)
