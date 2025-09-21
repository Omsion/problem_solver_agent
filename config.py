# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 配置文件 (智能分流版)

本文件是整个自动化Agent的“大脑”和“战略中心”。它集中管理了所有可配置的参数，
使得调整Agent的行为（如更换模型、修改指令、调整路径）无需触及核心业务逻辑代码，
极大地提高了项目的可维护性和可扩展性。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 通过调用 load_dotenv()，程序会自动加载项目根目录下 .env 文件中定义的环境变量。
# 这是安全管理API密钥等敏感信息的最佳实践，避免了将其硬编码在代码中。
load_dotenv()

# --- 1. Qwen-VL (DashScope) API 配置 ---
# 负责所有与“视觉”相关的任务，包括初步的问题分类和后续的图片文字转录。
# -------------------------------------------------------------------------------------
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME = "qwen-vl-plus"  # 使用通义千问的视觉语言模型

# --- 提示词工程 (Prompt Engineering) for Qwen-VL ---

# 提示词 1: 问题“粗分类” (Classification Prompt)
# 这个提示词的目标是让模型充当一个精准的二元分类器，判断问题是否需要编程。
# - 指令非常严格 ("MUST be ONLY ONE")，确保输出干净，便于程序解析。
# - 使用英文关键词 ('CODING', 'GENERAL') 是一个刻意的设计选择，
#   因为它们是明确的、无歧义的编程术语，可以避免因中文近义词带来的解析困难，
#   让后续的逻辑判断更加稳定可靠。
CLASSIFICATION_PROMPT = """
Analyze the content of the image(s). Determine if the problem is a programming/coding challenge or a general knowledge question.
Your response MUST be ONLY ONE of the following keywords:

- 'CODING': If the problem requires writing an algorithm or a code solution.
- 'GENERAL': If the problem is a multiple-choice, fill-in-the-blank, or any other non-coding question.

Respond with only the single, most appropriate keyword and nothing else.
"""

# 提示词 2: 图片转录 (Transcription Prompt)
# 这个提示词的目标是让模型扮演一个高精度的OCR（光学字符识别）引擎。
# 明确指示它“不要添加任何评论、解释或介绍性短语”，以获取最纯净的原始问题文本。
TRANSCRIPTION_PROMPT = """
Your task is to act as a highly accurate OCR engine. Analyze the following image(s) and transcribe all text content you see.
- Transcribe the text in the order it appears across the images.
- Preserve all original formatting, including mathematical formulas, symbols, code indentation, and line breaks.
- Combine the text from all images into a single, seamless block of text.
- Do not add any commentary, explanations, or introductory phrases. Output only the transcribed text.
"""

# --- 2. DeepSeek API 配置 ---
# 负责所有与“思考”和“推理”相关的任务，即根据转录后的文本进行问题求解。
# -------------------------------------------------------------------------------------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_MODE = "reasoner"  # 可选 "reasoner" (思考模式) 或 "chat" (快速模式)
MODEL_NAME = "deepseek-reasoner" if DEEPSEEK_MODEL_MODE == "reasoner" else "deepseek-chat"

# --- 提示词工程 (Prompt Engineering) for DeepSeek ---

# 这是一个“策略字典”，它将最终确定的问题类型映射到最优的、高度专业化的提示词模板。
# 这是实现Agent能够根据不同问题类型给出不同格式答案的核心。
PROMPT_TEMPLATES = {
    # 策略一: 针对通用问题（选择、填空、逻辑题等）
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

    # 策略二: 针对LeetCode编程题
    # - 通过“角色扮演”（顶级的算法工程师）引导模型产出更专业的内容。
    # - 使用“强制性指令” (必须严格遵循) 和 “Markdown标题模板” (###) 来“硬化”格式约束，
    #   确保模型始终按我们期望的结构输出，解决了之前输出格式不稳定的问题。
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

    # 策略三: 针对ACM及其他非LeetCode编程题
    # - 这个提示词经过“泛化”和“强化”，使其具有高度适应性。
    # - 它指示模型“智能判断”输入形式：如果是标准IO，就写真正的ACM模式；
    #   如果是函数调用型（如此前遇到的Attention数学题），就在主程序块中演示，
    #   从而完美解决了因指令与问题性质冲突而导致模型返回空内容的“静默失败”问题。
    "ACM": """
你是一位经验丰富的ACM竞赛金牌选手和算法问题解决专家。请为下面的编程题目提供一份专业、完整且可以直接运行的题解。
你必须严格遵循下面指定的“三段式”结构进行回答，并使用提供的Markdown标题。

**问题文本:**
---
{transcribed_text}
---

### 1. 解题思路分析
*   **核心算法:** 简要说明解决此问题的核心算法思想以及如何处理输入输出。
*   **复杂度分析:** 明确指出最终解法的时间复杂度和空间复杂度。

### 2. Python代码实现 (可执行脚本模式)
提供一份完整的、可独立运行的Python脚本。
*   **核心逻辑**: 必须将核心解题算法封装在一个或多个函数中。
*   **主程序入口**: 在 `if __name__ == "__main__":` 块中编写主程序逻辑。
*   **输入处理**: 根据题目描述，智能判断并实现输入处理。如果题目是典型的标准输入输出（需要循环读取多组数据），请使用 `sys.stdin` 或 `input()`；如果题目是函数调用式的，请在主程序块中直接定义示例输入并调用核心函数来演示。
*   **输出**: 必须将最终结果打印到标准输出。
请为代码添加必要的注释。

### 3. 代码逻辑讲解
解释你的代码实现，特别是主程序入口的逻辑、输入输出的处理方式以及核心算法的实现。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
"""
}

# --- 3. 核心文件路径配置 ---
# -------------------------------------------------------------------------------------
# 定义程序所需的所有关键目录。
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
# 监控截图的源文件夹
MONITOR_DIR = ROOT_DIR / "Screenshots"
# 存放已处理截图的归档文件夹
PROCESSED_DIR = ROOT_DIR / "processed"
# 存放最终解答文件的输出文件夹。
# 关键设计：将其设置在项目文件夹之外，是为了彻底解决因IDE“热重载”功能
# 监测到输出文件创建而自动重启主程序，进而导致的无限重复处理循环问题。
SOLUTION_DIR = ROOT_DIR / "solutions"

# --- 4. Agent 行为配置 ---
# -------------------------------------------------------------------------------------
# 图片分组的时间窗口（单位：秒）。
# 用户截图习惯的直接体现。例如，设置为15秒意味着，在前一张截图到达后的15秒内
# 出现的任何新截图，都会被认为是同一道题的一部分。可根据个人习惯调整。
GROUP_TIMEOUT = 15.0
# Agent能够识别并处理的图片文件扩展名元组。
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