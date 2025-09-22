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
# 负责所有与“视觉”相关的任务，包括初步的问题分类和后续的图片文字转录/视觉推理。
# -------------------------------------------------------------------------------------
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME = "qwen-vl-plus"  # 使用通义千问的视觉语言模型

# --- 提示词工程 (Prompt Engineering) for Qwen-VL ---

# 提示词 1: 问题分类 (Classification Prompt)
# 这个提示词的目标是让模型充当一个精准的三元分类器。
# - 指令非常严格 ("MUST be ONLY ONE")，确保输出干净，便于程序解析。
# - 使用英文关键词 ('CODING', 'VISUAL_REASONING', 'GENERAL') 是一个刻意的设计选择，
#   因为它们是明确的、无歧义的编程术语，让后续的逻辑判断更加稳定可靠。
CLASSIFICATION_PROMPT = """
Analyze the content of the image(s). Determine the type of problem presented.
Your response MUST be ONLY ONE of the following keywords:

- 'CODING': If the problem is a programming/coding challenge requiring a code solution.
- 'VISUAL_REASONING': If the problem requires finding a pattern in a sequence of shapes, figures, or matrices, and choosing a correct option.
- 'GENERAL': If the problem is a text-based multiple-choice, fill-in-the-blank, or logic question not covered by the above.

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
# 负责所有基于文本的“思考”和“推理”任务，即对转录后的文本进行问题求解。
# -------------------------------------------------------------------------------------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_MODE = "reasoner"  # 可选 "reasoner" (思考模式) 或 "chat" (快速模式)
MODEL_NAME = "deepseek-reasoner" if DEEPSEEK_MODEL_MODE == "reasoner" else "deepseek-chat"

# --- 提示词工程 (Prompt Engineering) for All Solvers ---

# 这是一个“策略字典”，它将最终确定的问题类型映射到最优的、高度专业化的提示词模板。
# 这是实现Agent能够根据不同问题类型给出不同格式答案的核心。
PROMPT_TEMPLATES = {
    # 策略一: 针对视觉推理题 (由Qwen-VL执行)
    # 引入“思维链”方法，强制模型进行多角度、批判性思考，显著提高正确率。
    "VISUAL_REASONING": """
你是一位顶级的图形逻辑推理专家。你的任务是分析给出的图形序列，找出其中蕴含的规律，并从选项中选出正确答案。
你必须严格遵循下面指定的、包含“批判性思维”的四段式结构进行回答。

**问题图片:**
(你正在观察图片)

### 1. 探索多种可能的规律
*   **规律假设1 (例如，基于对称性):** 详细描述你发现的第一个可能的规律。
*   **规律假设2 (例如，基于元素数量/位置):** 详细描述你发现的第二个可能的规律。
*   **规律假设3 (例如，基于形状组合/叠加):** 详细描述你发现的第三个可能的规律。
(请尽可能多地探索不同的维度，如对称性、旋转、平移、数量、叠加、内外关系等。)

### 2. 评估并选择最佳规律
*   **规律评估:** 对比你提出的多个规律假设，评估哪个规律最连贯、最能解释整个图形序列，并且没有特例。
*   **最终选择:** 明确指出你选择的最佳规律是什么。

### 3. 应用规律并得出结论
*   **应用推导:** 将你选择的最佳规律应用到序列的最后一个图形，严谨地推导出问号处应该是什么样的图形。
*   **匹配选项:** 将你推导出的结果与选项A, B, C, D进行仔细比对。

### 4. 最终答案
明确指出哪个选项是正确答案，并用你选择的最佳规律简要重申理由。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文问题总结]' 的格式，为该问题生成一个适合用作文件名的标题。
""",

    # 策略二: 针对通用问题（选择、填空、文字逻辑题等，由DeepSeek执行）
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

    # 策略三: 针对LeetCode编程题 (由DeepSeek执行)
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

    # 策略四: 针对ACM及其他非LeetCode编程题 (由DeepSeek执行)
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
# -------------------------------------------------------------------------------------
def initialize_directories():
    """
    一个辅助函数，在程序启动时自动检查并创建必要的文件夹。
    这增强了程序的健壮性，使得用户在第一次运行时无需手动创建目录。
    """
    print("正在初始化目录结构...")
    for dir_path in [PROCESSED_DIR, SOLUTION_DIR]:
        try:
            # parents=True: 允许创建多层级的中间目录。
            # exist_ok=True: 如果目录已经存在，则不会引发错误。
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"  - 目录 '{dir_path}' 已确认存在。")
        except OSError as e:
            print(f"创建目录 '{dir_path}' 时发生错误: {e}")
            exit(1)