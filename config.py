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
#   因为它们是明确的、无歧义的编程术语，可以避免因中文近义词带来的解析困难，
#   让后续的逻辑判断更加稳定可靠。
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


# --- 3. 求解风格配置 ---
# -------------------------------------------------------------------------------------
# 在这里定义您期望的编程题解答风格，这是解决“代码雷同”问题的核心开关。
# - 'OPTIMAL': AI将尽力提供时间/空间复杂度最优的标准解法。
# - 'EXPLORATORY': AI将被引导提供一个非最优但逻辑正确、更易于理解的“次优解”，并附带优化思路，以增加答案的多样性和教学价值。
SOLUTION_STYLE = "EXPLORATORY"  # <-- 在这里修改 'OPTIMAL' 或 'EXPLORATORY'

# --- 4. 提示词工程 (Prompt Engineering) for All Solvers ---
# 这是一个“策略字典”，它将最终确定的问题类型映射到最优的、高度专业化的提示词模板。
# 这是实现Agent能够根据不同问题类型给出不同格式答案的核心。
PROMPT_TEMPLATES = {
    # 策略一: 针对视觉推理题 (由Qwen-VL执行)
    # 采用了顶尖研究 'VisuRiddles' 的核心思想 (ref:https://arxiv.org/abs/2506.02537)，
    # 强制模型遵循“精细化感知 -> 抽象推理”的两步框架，显著提升复杂图形题的正确率。
    "VISUAL_REASONING": """
你是一位顶级的逻辑推理专家，精通解决各类抽象视觉谜题。你的任务是严格遵循“精细化感知”和“抽象推理”两个步骤，来解决下面的图形推理问题。

**问题图片:**
(你正在观察图片)

### 1. 精细化感知 (Fine-Grained Perception)
在这一步，你只做观察和描述，不进行任何推理。
*   **题干图形描述:** 逐一、详细地描述题干序列中的每一个图形。对于每个图形，请描述其核心组成元素、几何属性（如对称性、曲直、开闭）、元素数量和相对位置。
    *   图形1: [详细描述]
    *   图形2: [详细描述]
    *   ...
*   **选项图形描述:** 逐一、详细地描述选项A, B, C, D中的每一个图形的核心特征。
    *   选项A: [详细描述]
    *   ...

### 2. 抽象推理与结论 (Abstract Reasoning & Conclusion)
在这一步，你将只基于上面你自己的文字描述进行逻辑推理。
*   **规律寻找:** 分析你在“精细化感知”阶段描述的特征，找出题干图形序列中蕴含的核心规律。请探索至少两种可能的规律（例如，规律A基于对称轴数量变化，规律B基于元素位置移动），然后评估并选择最连贯、最没有矛盾的一个作为最终规律。
*   **应用规律:** 将你选择的最佳规律应用到序列中，推导出问号处应该具备什么样的特征。
*   **匹配与决策:** 将你推导出的特征与“选项图形描述”进行匹配，明确指出哪个选项完全符合所有规律。

### 3. 最终答案
明确指出哪个选项是正确答案，并用你最终选择的最佳规律简要重申核心理由。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文问题总结]' 的格式，为该问题生成一个适合用作文件名的标题。
""",

    # 策略二: 针对通用问题（选择、填空、文字逻辑题等，由DeepSeek执行）
    "GENERAL": """
你是一位逻辑严谨、善于分析问题的专家...
""" # (为简洁省略，内容与之前版本相同)
,
    # 策略三: 针对LeetCode编程题 (由DeepSeek执行, 包含两种风格)
    "LEETCODE": {
        "OPTIMAL": """
你是一位融合了顶尖算法导师和资深软件架构师双重身份的AI专家。你的任务是为下面的LeetCode风格编程题目提供一份“超越标准答案”的深度教学式题解，聚焦于最高效的解决方案。
你必须严格遵循下面指定的五段式结构进行回答，并使用提供的Markdown标题。

**问题文本:**
---
{transcribed_text}
---

### 1. 核心思路与算法选择
*   **解题直觉:** 首先，用一两句话描述解决这个问题的直观想法或“第一感觉”。
*   **算法选型:** 基于直觉，分析并确定**时间与空间复杂度最优**的核心算法。解释为什么这是最优解。
*   **复杂度分析:** 明确指出最优解法的时间和空间复杂度。

### 2. 备选方案与权衡分析 (Trade-offs)
*   **备选方案:** 简要描述一到两种解决此问题的其他可行方法。
*   **优劣对比:** 对比最优解和备选方案的优劣。

### 3. 最优解Python代码实现 (LeetCode模式)
在 `Solution` 类中提供一个完整、注释清晰的最优解Python代码实现。

### 4. 代码逐行讲解
对最优解代码中的关键部分进行详细讲解。

### 5. 原创性与学习建议
*   **关于原创性:** 明确指出这份代码是基于公开知识和最佳实践生成的标准解法。
*   **学习建议:** 给出1-2条关于如何掌握此题背后知识点的学习建议。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
""",
        "EXPLORATORY": """
你是一位聪明但经验尚浅的软件工程师，倾向于使用最直观、最易于理解的方法来解决问题，而不是追求极致的性能。你的任务是为下面的LeetCode风格编程题目提供一份侧重于“思路清晰”的教学式题解。
你必须严格遵循下面指定的五段式结构进行回答。

**重要约束：** 在你的解决方案中，**请主动避免使用非常规或高度优化的数据结构**，例如 **堆（Heap）、单调栈/队列、字典树（Trie）** 等，除非题目明确要求。请优先使用列表、排序、循环等基础技巧。

**问题文本:**
---
{transcribed_text}
---

### 1. 核心思路与算法选择
*   **解题直觉:** 描述解决这个问题最直接、最符合直觉的暴力或次优想法。
*   **算法选型:** 基于上述直觉，设计一个**虽然可能不是最高效，但逻辑清晰、易于实现**的算法。
*   **复杂度分析:** 分析这个次优解法的时间和空间复杂度。

### 2. 更优方案探讨
*   **可能的优化:** 简要提及存在一个或多个更优的解法（例如，使用堆或动态规划），并指出它们能够优化哪个方面的性能（例如，将时间复杂度从O(n^2)降到O(n log n)）。

### 3. 次优解Python代码实现 (LeetCode模式)
在 `Solution` 类中提供一个完整、注释清晰的、基于你上述“直觉”思路的Python代码实现。

### 4. 代码逐行讲解
对你的次优解代码进行详细讲解，解释其逻辑流程。

### 5. 原创性与学习建议
*   **关于原创性:** 明确指出这份代码是一个为了教学和展示基础思路而构建的次优解。
*   **学习建议:** 建议学习者在理解此解法后，主动去探索和实现更优的解决方案。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文次优解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
"""
    },

    # 策略四: 针对ACM编程题 (由DeepSeek执行, 包含两种风格)
    "ACM": {
        "OPTIMAL": """
你是一位经验丰富的ACM竞赛金牌教练...
""" # (为简洁省略，内容与之前版本相同)
,
        "EXPLORATORY": """
你是一位正在备战区域赛的ACM队员，擅长用稳健、不易出错的基础算法解决问题。你的任务是为下面的ACM/ICPC风格编程题目提供一份侧重于“正确性优先”的题解。

**重要约束：** 在你的解决方案中，**请主动避免使用过于复杂或冷门的算法与数据结构**。请优先使用排序、暴力搜索（如果时间允许）、基础动态规划等方法。

**问题文本:**
---
{transcribed_text}
---

### 1. 核心思路与算法选择
*   **问题转化:** 分析题目，说明这个问题的本质。
*   **算法选型:** 设计一个**虽然可能不是最快，但正确性有保障、容易调试**的算法。
*   **复杂度分析:** 分析该解法的时间和空间复杂度，并简单评估它是否可能在竞赛中超时（TLE）。

### 2. 更优方案探讨
*   **可能的优化:** 简要提及为了在竞赛中稳定通过，可能需要采用的更高级的算法或数据结构。

### 3. 次优解Python代码实现 (可执行脚本模式)
提供一份完整的、可独立运行的、基于你上述“稳健”思路的Python脚本。

### 4. 代码逐行讲解
对你的次优解代码进行详细讲解。

### 5. 原创性与学习建议
*   **关于原创性:** 明确指出这是一份为了确保正确性而设计的、可能非最优的解法。
*   **学习建议:** 鼓励学习者思考如何对该解法进行优化以满足更严格的时间限制。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文次优解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
"""
    }
}

# --- 5. 核心文件路径配置 ---
# -------------------------------------------------------------------------------------
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "solutions"

# --- 6. Agent 行为配置 ---
# -------------------------------------------------------------------------------------
GROUP_TIMEOUT = 15.0
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')

# --- 7. 初始化功能 ---
# -------------------------------------------------------------------------------------
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