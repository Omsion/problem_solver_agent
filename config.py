# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 配置文件 (智能分流版)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- 1. Qwen-VL (DashScope) API 配置 ---
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME = "qwen-vl-plus"

# --- 提示词工程 (Prompt Engineering) for Qwen-VL ---

# ### UPDATED ### - 提示词 1: 问题“粗分类” (Classification Prompt)
# -------------------------------------------------------------------------------------
# 将模型的任务简化为二元分类，这比三元分类更稳定、更不容易出错。
# 模型现在只负责判断是否为编程题。
CLASSIFICATION_PROMPT = """
Analyze the content of the image(s). Determine if the problem is a programming/coding challenge or a general knowledge question.
Your response MUST be ONLY ONE of the following keywords:

- 'CODING': If the problem requires writing an algorithm or a code solution.
- 'GENERAL': If the problem is a multiple-choice, fill-in-the-blank, or any other non-coding question.

Respond with only the single, most appropriate keyword and nothing else.
"""
# -------------------------------------------------------------------------------------


# 提示词 2: 图片转录 (Transcription Prompt)
TRANSCRIPTION_PROMPT = """
Your task is to act as a highly accurate OCR engine. Analyze the following image(s) and transcribe all text content you see.
- Transcribe the text in the order it appears across the images.
- Preserve all original formatting, including mathematical formulas, symbols, code indentation, and line breaks.
- Combine the text from all images into a single, seamless block of text.
- Do not add any commentary, explanations, or introductory phrases. Output only the transcribed text.
"""

# --- 2. DeepSeek API 配置 ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_MODE = "reasoner"
MODEL_NAME = "deepseek-reasoner" if DEEPSEEK_MODEL_MODE == "reasoner" else "deepseek-chat"

# 提示词工程 (Prompt Engineering) for DeepSeek
# 策略字典保持不变，因为我们最终还是会细分到这三种策略上。
PROMPT_TEMPLATES = {
    "GENERAL": """
你是一位逻辑严谨、善于分析问题的专家。请根据以下问题文本，提供一份详尽的解决方案。
你必须严格遵循下面指定的“三段式”结构进行回答，并使用提供的Markdown标题。

**问题文本:**
---
{transcribed_text}
---

### 1. 题目分析
*   **核心思路:** 清晰地阐述解决这个问题的核心逻辑和思考过程。
*   **步骤推理:** 一步步地详细拆解你的推理过程。

### 2. 最终答案
明确地给出问题的最终答案。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文问题总结]' 的格式，为该问题生成一个适合用作文件名的标题。
""",

    "LEETCODE": """
你是一位顶级的算法工程师和技术面试官。请为下面的LeetCode风格编程题目提供一份专业、完整且结构清晰的题解。
你必须严格遵循下面指定的“三段式”结构进行回答，并使用提供的Markdown标题。

**问题文本:**
---
{transcribed_text}
---

### 1. 解题思路分析
*   **核心算法:** 简要说明解决此问题的核心算法思想（例如：动态规划、双指针、原地哈希等）。
*   **复杂度分析:** 明确指出最终解法的时间复杂度和空间复杂度。

### 2. Python代码实现 (LeetCode模式)
在 `Solution` 类中提供一个完整、注释清晰的Python代码实现。

### 3. 代码逻辑讲解
逐段或按关键步骤解释你的代码实现，清晰说明每一部分的作用，使其与“解题思路分析”中的逻辑相对应。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
""",

    "ACM": """
你是一位经验丰富的ACM竞赛金牌选手。请为下面的ACM/ICPC风格编程题目提供一份专业、完整且可以直接提交的题解。
你必须严格遵循下面指定的“三段式”结构进行回答，并使用提供的Markdown标题。

**问题文本:**
---
{transcribed_text}
---

### 1. 解题思路分析
*   **核心算法:** 简要说明解决此问题的核心算法思想以及如何处理输入输出。
*   **复杂度分析:** 明确指出最终解法的时间复杂度和空间复杂度。

### 2. Python代码实现 (ACM模式)
提供一份完整的、可独立运行的Python脚本。代码必须能正确处理标准输入（如 `sys.stdin` 或 `input()`）并将结果打印到标准输出。请为代码添加必要的注释。

### 3. 代码逻辑讲解
解释你的代码实现，特别是输入输出的处理方式以及核心算法的逻辑。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
"""
}

# --- 3. 核心文件路径配置 ---
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "solutions"

# --- 4. Agent 行为配置 ---
GROUP_TIMEOUT = 15.0
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')

# --- 5. 初始化功能 ---
def initialize_directories():
    print("正在初始化目录结构...")
    for dir_path in [PROCESSED_DIR, SOLUTION_DIR]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"  - 目录 '{dir_path}' 已确认存在。")
        except OSError as e:
            print(f"创建目录 '{dir_path}' 时发生错误: {e}")
            exit(1)