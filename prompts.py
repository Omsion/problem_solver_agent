# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 提示词模块

本文件集中管理了所有与大型语言模型交互时使用的提示词（Prompts）。
将其从主配置文件中分离出来，旨在提高代码的可读性和可维护性。
"""

# --- 提示词工程 (Prompt Engineering) for Qwen-VL ---

# 提示词 1: 问题分类 (Classification Prompt)
CLASSIFICATION_PROMPT = """
Analyze the content of the image(s). Determine the type of problem presented.
Your response MUST be ONLY ONE of the following keywords:
- 'CODING': If the problem is a programming/coding challenge requiring a code solution.
- 'VISUAL_REASONING': If the problem requires finding a pattern in a sequence of shapes, figures, or matrices.
- 'QUESTION_ANSWERING': If the problem is a standard question-answering task based on provided text or data.
- 'GENERAL': For any other text-based problem.
Respond with only the single, most appropriate keyword and nothing else.
"""

# <<< 支持结构化内容（表格、公式）的文档重构提示词 >>>
TRANSCRIPTION_PROMPT = """
你是一个世界顶级的、专门用于文档数字化的多模态识别引擎。你的任务是精确地识别单张图片中的所有内容，并将其转化为结构化的文本。

**核心要求：**
- **精确识别**: 识别图片中的所有文字、段落、列表、表格和数学公式。
- **结构保持**: 必须最大程度地保留原始文档的布局和格式。
- **纯净输出**: 你的输出只能包含识别出的内容，严禁添加任何前缀、后缀、解释或评论。

**格式化规则：**
1.  **表格 (Tables)**: 必须使用 **Markdown** 格式进行输出 (例如: `| Header 1 | Header 2 |\n|---|---|\n| Cell 1 | Cell 2 |`)。
2.  **数学公式 (Math Formulas)**: 必须使用 **LaTeX** 格式进行输出，行内公式使用 `$...$` 包裹，块级公式使用 `$$...$$` 包裹。
3.  **其他所有内容**: 严格按照原始的换行、缩进和排版进行输出。

现在，请处理你收到的**单张图片**，并严格按照上述规则，输出其包含的结构化文本。
"""

# <<< 用于最终文本润色的提示词 >>>
TEXT_POLISHING_PROMPT = """
你是一位专业的文档校对员。以下是一段通过OCR和程序化拼接生成的文本，可能包含一些微小的识别错误或不自然的断行。

你的任务是：
1.  **修正明显的OCR错误** (例如，将 "hell0" 修正为 "hello")。
2.  **修复不自然的断行**，使段落更加通顺。
3.  **保持所有代码、Markdown表格、LaTeX公式的原始格式不变。**
4.  **绝对不要添加、删除或改写任何非错误内容。**

请对以下文本进行最小化的、必要的校对，并返回最终的、干净的文本。

**待校对文本:**
---
{merged_text}
---
"""

# --- 提示词工程 for Solvers (已包含容错指令) ---

PROMPT_TEMPLATES = {

    # 策略一: 针对视觉推理题 (由Qwen-VL执行)
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

    # 策略二: 针对直接问答题 (由Solvers，如DeepSeek执行)
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

    # 策略三: 针对通用文字题 (由Solvers，如DeepSeek执行)
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

    # 策略四: 针对LeetCode编程题 (由Solvers，如DeepSeek执行, 包含“思维链”和两种风格)
    "LEETCODE": {
        "OPTIMAL": """
你是一位顶级的算法专家和软件架构师。请为下面的LeetCode编程题提供一份高质量的教学式题解。

**问题文本:**
---
{transcribed_text}
---

### 1. 题目分析与核心思路
*   准确概括核心要求，阐述最优算法思路、原因及时间空间复杂度。

### 2. 代码实现 (包含可执行部分)
*   **a. 核心解法:** 在 `Solution` 类中提供完整、注释清晰的最优解Python代码。
*   **b. 可执行入口:** 在代码末尾，**必须**提供一个 `if __name__ == '__main__':` 代码块，用于模拟处理输入、调用你的 `Solution` 类方法，并打印结果。
*   **c. I/O (输入/输出) 核心要求:**
    *   **目标:** 为确保代码能通过在线评测系统(OJ)，必须使用高效的、逐行处理的I/O方式，以防止时间超限(TLE)或内存超限(MLE)。
    *   **首选模式 (多组未知数量的测试用例):**
        ```python
        # import sys
        # for line in sys.stdin:
        #     # 解析 line 并处理
        ```
    *   **备用模式 (已知行数或复杂格式):**
        ```python
        # import sys
        # T = int(sys.stdin.readline())
        # for _ in range(T):
        #     # 使用 sys.stdin.readline() 读取后续行
        ```
    *   **禁止模式:** **严禁**使用 `data = sys.stdin.read()` 或 `data = open(0).read()` 等一次性读入全部输入的低效方法。
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

### 1. 题目分析与核心思路
*   用最直白的方式解释题目要求，提出一个逻辑清晰、易于实现的解法思路，并分析其时间空间复杂度。

### 2. 代码实现 (包含可执行部分)
*   **a. 核心解法:** 在 `Solution` 类中，提供完整、注释清晰的、基于你上述“直觉”思路的Python代码。
*   **b. 可执行入口:** 在代码末尾，**必须**提供一个 `if __name__ == '__main__':` 代码块，用于模拟处理输入、调用你的 `Solution` 类方法，并打印结果。
*   **c. I/O (输入/输出) 核心要求:**
    *   **目标:** 为确保代码能通过在线评测系统(OJ)，必须使用高效的、逐行处理的I/O方式，以防止时间超限(TLE)或内存超限(MLE)。
    *   **首选模式 (多组未知数量的测试用例):**
        ```python
        # import sys
        # for line in sys.stdin:
        #     # 解析 line 并处理
        ```
    *   **备用模式 (已知行数或复杂格式):**
        ```python
        # import sys
        # T = int(sys.stdin.readline())
        # for _ in range(T):
        #     # 使用 sys.stdin.readline() 读取后续行
        ```
    *   **禁止模式:** **严禁**使用 `data = sys.stdin.read()` 或 `data = open(0).read()` 等一次性读入全部输入的低效方法。
---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文次优解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
"""
    },

    # 策略五: 针对ACM编程题 (由Solvers，如DeepSeek执行, 包含“思维链”和两种风格)
    "ACM": {
        "OPTIMAL": """
你是一位经验丰富的ACM竞赛金牌教练，以代码的绝对正确性和对题目细节的精确把握著称。请为下面的ACM风格编程题目提供一份竞赛级的完整题解。
**注意:** 以下问题文本由OCR工具从图片转录而来，可能存在少量识别错误。请运用你的专业知识和推理能力，理解其核心意图并解决问题。

**问题文本:**
---
{transcribed_text}
---

### 1. 题目分析与核心思路
*   **需求提炼:** 精确提炼本题的所有计算任务、输入输出格式、数值精度要求和所有边界条件。
*   **算法选型:** 阐述解决此问题的最优算法，并解释其高效性和正确性。
*   **复杂度分析:** 给出该算法的时间和空间复杂度。

### 2. 最优解Python代码实现
*   **I/O (输入/输出) 核心要求:**
    *   **目标:** 为确保代码能通过在线评测系统(OJ)，必须使用高效的、逐行处理的I/O方式，以防止时间超限(TLE)或内存超限(MLE)。
    *   **首选模式 (多组未知数量的测试用例):**
        ```python
        import sys
        for line in sys.stdin:
            # 解析 line 并处理
        ```
    *   **备用模式 (已知行数或复杂格式):**
        ```python
        import sys
        T = int(sys.stdin.readline())
        for _ in range(T):
            # 使用 sys.stdin.readline() 读取后续行
        ```
    *   **禁止模式:** **严禁**使用 `data = sys.stdin.read()` 或 `data = open(0).read()` 等一次性读入全部输入的低效方法。
*   **代码要求:** 基于上述I/O要求，提供一份完整的、可直接在OJ系统提交的Python脚本。

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

### 1. 题目分析与核心思路
*   **需求提炼:** 提炼本题的所有要求。
*   **算法选型:** 提出一个虽然不一定最快，但逻辑清晰、确保能得到正确答案的算法思路。
*   **复杂度分析:** 分析该解法的时间和空间复杂度，并评估是否可能超时。

### 2. 次优解Python代码实现
*   **I/O (输入/输出) 核心要求:**
    *   **目标:** 为确保代码能通过在线评测系统(OJ)，必须使用高效的、逐行处理的I/O方式，以防止时间超限(TLE)或内存超限(MLE)。
    *   **首选模式 (多组未知数量的测试用例):**
        ```python
        import sys
        for line in sys.stdin:
            # 解析 line 并处理
        ```
    *   **备用模式 (已知行数或复杂格式):**
        ```python
        import sys
        T = int(sys.stdin.readline())
        for _ in range(T):
            # 使用 sys.stdin.readline() 读取后续行
        ```
    *   **禁止模式:** **严禁**使用 `data = sys.stdin.read()` 或 `data = open(0).read()` 等一次性读入全部输入的低效方法。
*   **代码要求:** 基于上述I/O要求，提供一份完整的、可独立运行的Python脚本。

---
任务完成后，请另起一行，并严格遵循 'TITLE: [5-10个字的中文次优解法总结]' 的格式，为该题解生成一个适合用作文件名的标题。
"""
    }
}