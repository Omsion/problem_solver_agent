# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 配置文件

此文件集中管理项目的所有可配置参数，包括API密钥、文件路径、以及Agent的行为设置。
修改此文件可以方便地调整程序的运行方式，而无需改动核心逻辑代码。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# --- 安全设置：从 .env 文件加载环境变量 ---
load_dotenv()


# --- 1. DeepSeek API 配置 ---

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# 使用基础URL，而不是完整的端点URL
API_BASE_URL = "https://api.deepseek.com"

# ### NEW ### - 添加模型模式选择
# -------------------------------------------------------------------------
# 在这里选择您想使用的DeepSeek模型模式。
# - "reasoner": 思考模式。适合需要深度推理的复杂任务（如算法题），效果更好但响应稍慢。
# - "chat":     非思考模式。适合快速响应的常规任务。
DEEPSEEK_MODEL_MODE = "reasoner"  # <-- 在这里修改 "reasoner" 或 "chat"

# 根据上面的选择，自动生成模型名称
MODEL_NAME = "deepseek-reasoner" if DEEPSEEK_MODEL_MODE == "reasoner" else "deepseek-chat"
# -------------------------------------------------------------------------


# --- 2. 核心文件路径配置 ---
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "problem_solver_agent" / "solutions"


# --- 3. Agent 行为配置 ---
GROUP_TIMEOUT = 5.0
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')


# --- 4. 提示词工程 (Prompt Engineering) ---
PROMPT_TEMPLATE = """
请综合分析以下所有图片，它们共同构成一个完整的题目。请遵循以下要求：

1.  **详细解答**：请给出详细的思考过程、解题步骤和最终答案。
2.  **格式清晰**：使用Markdown格式化您的回答，使其易于阅读。

---
任务结束后，请在回答的最后另起一行，并严格按照'TITLE: [5到10个字的题目核心内容]'的格式，为这道题生成一个简短的、适合作为文件名的标题。
"""


# --- 启动时检查：确保必要的文件夹存在 ---
def initialize_directories():
    """检查并创建项目所需的文件夹。"""
    print("正在初始化目录结构...")
    for dir_path in [PROCESSED_DIR, SOLUTION_DIR]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"  - 目录 '{dir_path}' 已确认存在。")
        except OSError as e:
            print(f"创建目录 '{dir_path}' 时发生错误: {e}")
            exit(1)