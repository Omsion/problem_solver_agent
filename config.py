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
# 这个提示词的目标是让模型充当一个精准的分类器。
# - 指令非常严格 ("MUST be ONLY ONE")，确保输出干净，便于程序解析。
# - 使用英文关键词 ('CODING', 'VISUAL_REASONING', 'GENERAL', 'QUESTION_ANSWERING') 是一个刻意的设计选择，
#   因为它们是明确的、无歧义的编程术语，可以避免因中文近义词带来的解析困难，
#   让后续的逻辑判断更加稳定可靠。
CLASSIFICATION_PROMPT = """
Analyze the content of the image(s). Determine the type of problem presented.
Your response MUST be ONLY ONE of the following keywords:
- 'CODING': If the problem is a programming/coding challenge requiring a code solution.
- 'VISUAL_REASONING': If the problem requires finding a pattern in a sequence of shapes, figures, or matrices.
- 'QUESTION_ANSWERING': If the problem is a standard question-answering task based on provided text or data.
- 'GENERAL': For any other text-based problem.
Respond with only the single, most appropriate keyword and nothing else.
"""

# <<< 为最大化转录准确率，采用Few-Shot Learning（少样本学习）提示词 >>>
# 这个提示词通过提供一个完美的输入输出范例，直接向模型展示了期望的行为，
# 能够极大地提升其作为纯粹OCR引擎的性能，并抑制其“创造”或“解读”内容的倾向。
TRANSCRIPTION_PROMPT = """
You are a world-class Optical Character Recognition (OCR) engine. Your ONLY function is to transcribe text from images with extreme precision. You MUST follow these rules:
- Transcribe text EXACTLY as it appears.
- DO NOT interpret, solve, rephrase, or add any commentary.
- Your output must contain ONLY the transcribed text.

Here is an example of a perfect transcription:

--- EXAMPLE START ---
[Image Content]:
'''
Problem 1. Two Sum

Given an array of integers `nums` and an integer `target`, return indices of the two numbers such that they add up to `target`.

Input: nums = [2, 7, 11, 15], target = 9
Output: [0, 1]
'''

[Your Perfect Output]:
Problem 1. Two Sum

Given an array of integers `nums` and an integer `target`, return indices of the two numbers such that they add up to `target`.

Input: nums = [2, 7, 11, 15], target = 9
Output: [0, 1]
--- EXAMPLE END ---

Now, apply this level of precision to the following image(s). Transcribe everything you see.
"""

# --- 2. DeepSeek API 配置 ---
# 负责所有基于文本的“思考”和“推理”任务，即对转录后的文本进行问题求解。
# -------------------------------------------------------------------------------------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_MODE = "reasoner"  # 可选 "reasoner" (思考模式) 或 "chat" (快速模式)
MODEL_NAME = "deepseek-reasoner" if DEEPSEEK_MODEL_MODE == "reasoner" else "deepseek-chat"
# 增加API调用优化配置
API_TIMEOUT = 600.0  # API调用超时时间（秒）
MAX_RETRIES = 3     # 最大重试次数
RETRY_DELAY = 10     # 每次重试延迟

# --- 3. 求解风格配置 ---
# -------------------------------------------------------------------------------------
# 在这里定义您期望的编程题解答风格，这是解决“代码雷同”问题的核心开关。
# - 'OPTIMAL': AI将尽力提供时间/空间复杂度最优的标准解法。
# - 'EXPLORATORY': AI将被引导提供一个非最优但逻辑正确、更易于理解的“次优解”，并附带优化思路，以增加答案的多样性和教学价值。
SOLUTION_STYLE = "OPTIMAL"  # <-- 在这里修改 'OPTIMAL' 或 'EXPLORATORY'

# --- 4. 提示词工程 for Solvers (已包含容错指令) ---
PROMPT_TEMPLATES = {
    # 策略一: 针对视觉推理题 (由Qwen-VL执行)
    # 采用了顶尖研究 'VisuRiddles' 的核心思想 (ref:https://arxiv.org/abs/2506.02537)，
    # 强制模型遵循“精细化感知 -> 抽象推理”的两步框架，显著提升复杂图形题的正确率。
    "VISUAL_REASONING": """
你是一位顶级的逻辑推理专家。请严格遵循“精细化感知”和“抽象推理”两个步骤，来解决下面的图形推理问题。
### 1. 精细化感知 (Fine-Grained Perception)
*   **题干图形描述:** 逐一、详细地描述题干序列和选项中的每一个图形。
### 2. 抽象推理与结论 (Abstract Reasoning & Conclusion)
*   **规律寻找:** 分析你在“精细化感知”阶段描述的特征，找出题干图形序列中蕴含的核心规律。
*   **匹配与决策:** 将规律应用到选项中，明确指出哪个选项完全符合。
### 3. 最终答案
明确指出哪个选项是正确答案，并简要重申核心理由。
---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文问题总结]' 的格式，为该问题生成一个适合用作文件名的标题。
""",

    # 策略二: 针对直接问答题 (由DeepSeek执行)
    # 这个提示词高度聚焦和简化，避免了模型在简单计算题上“过度思考”或“角色扮演失控”的问题。
    "QUESTION_ANSWERING": """
你是一个精准、高效的“信息提取与计算”机器人。请根据提供的“问题文本”，直接、清晰地回答问题。
**问题文本:**
---
{transcribed_text}
---
### 1. 计算过程
*   清晰地列出解决问题所需的关键数据、公式和计算步骤。
### 2. 最终答案
*   明确地给出问题的最终答案。
---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文问题总结]' 的格式，为该问题生成一个适合用作文件名的标题。
""",

    # 策略三: 针对通用文字题 (由DeepSeek执行)
    "GENERAL": """
你是一位逻辑严谨、善于分析问题的专家。请根据以下问题文本，提供一份详尽的解决方案。
**问题文本:**
---
{transcribed_text}
---
### 1. 题目分析
*   阐述解决这个问题的核心逻辑和思考过程。
### 2. 最终答案
*   明确地给出问题的最终答案。
---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文问题总结]' 的格式，为该问题生成一个适合用作文件名的标题。
""",

    # 策略四: 针对LeetCode编程题 (由DeepSeek执行, 包含“思维链”和两种风格)
    "LEETCODE": {
        "OPTIMAL": """
你是一位顶级的算法专家和软件架构师。请为下面的LeetCode编程题提供一份高质量的教学式题解。

**问题文本:**
---
{transcribed_text}
---

为了保证题解的清晰性和完整性，我们推荐你的回答包含以下几个部分：

### 1. 题目分析与核心思路
*   首先，请准确概括这道题的核心要求。
*   然后，阐述解决这个问题的最优算法思路，并解释其为何最优。
*   最后，给出该算法的时间和空间复杂度。

### 2. 代码实现
*   在 `Solution` 类中，提供完整、注释清晰的最优解Python代码。请确保代码100%符合题目的所有要求，包括所有细节和边界条件。

### 3. 学习建议
*   提供1-2条关于掌握此题背后知识点的学习建议。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
""",
        "EXPLORATORY": """
你是一位乐于助人的资深软件工程师，擅长用最直观易懂的方式讲解问题。请为下面的LeetCode编程题提供一份侧重于“思路清晰”的教学式题解。

**重要约束：** 请优先使用循环、排序等基础技巧，避免使用过于复杂的算法。

**问题文本:**
---
{transcribed_text}
---

为了帮助初学者理解，我们推荐你的回答包含以下几个部分：

### 1. 题目分析与核心思路
*   请用最直白的方式解释这道题要求我们做什么。
*   然后，提出一个逻辑清晰、易于实现的解法思路。
*   最后，分析这个解法的时间和空间复杂度。

### 2. 代码实现
*   在 `Solution` 类中，提供完整、注释清晰的、基于你上述“直觉”思路的Python代码。

### 3. 进阶思考
*   简要提及可能存在的更优解法，为学习者指明优化方向。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文次优解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
"""
    },

    # 策略五: 针对ACM编程题 (由DeepSeek执行, 包含“思维链”和两种风格)
    "ACM": {
        "OPTIMAL": """
你是一位经验丰富的ACM竞赛金牌教练，以代码的绝对正确性和对题目细节的精确把握著称。请为下面的ACM风格编程题目提供一份竞赛级的完整题解。
**注意:** 以下问题文本由OCR工具从图片转录而来，可能存在少量识别错误。请运用你的专业知识和推理能力，理解其核心意图并解决问题。

**问题文本:**
---
{transcribed_text}
---
为了保证题解的专业性和实用性，我们推荐你的回答包含以下几个部分：
### 1. 题目分析与核心思路
*   **需求提炼:** 首先，请精确地提炼出本题的所有计算任务、输入输出格式、数值精度要求和所有边界条件。
*   **算法选型:** 接着，阐述解决此问题的最优算法，并解释它为什么能高效且正确地处理所有需求。
*   **复杂度分析:** 最后，给出该算法的时间和空间复杂度。
### 2. 最优解Python代码实现
*   提供一份完整的、可直接在OJ系统提交的Python脚本。代码必须包含高效的输入输出处理，并完全符合题目的所有格式要求。
### 3. 代码关键点讲解
*   对代码中的核心算法、数据结构或关键处理逻辑进行简要讲解。
---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
""",
        "EXPLORATORY": """
你是一位正在备战区域赛的ACM队员，擅长用稳健、不易出错的基础算法解决问题。请为下面的ACM风格编程题目提供一份侧重于“正确性优先”的题解。

**重要约束：** 请优先使用暴力搜索、排序等基础但可靠的方法。

**问题文本:**
---
{transcribed_text}
---

为了确保解法的可靠性，我们推荐你的回答包含以下几个部分：

### 1. 题目分析与核心思路
*   **需求提炼:** 首先，请精确地提炼出本题的所有要求。
*   **算法选型:** 提出一个虽然不一定最快，但逻辑清晰、确保能得到正确答案的算法思路。
*   **复杂度分析:** 分析该解法的时间和空间复杂度，并评估是否可能超时。

### 2. 次优解Python代码实现
*   提供一份完整的、可独立运行的、基于你上述“稳健”思路的Python脚本。

### 3. 优化方向
*   简要说明为了通过更严格的时间限制，可以从哪些方面进行优化。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文次优解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
"""
    }
}

# --- 5. 核心文件路径配置 ---
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "solutions"

# --- 6. Agent 行为配置 ---
GROUP_TIMEOUT = 15.0
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')

# --- 7. 初始化功能 ---
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