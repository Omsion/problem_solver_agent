# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 配置文件 (智能分流版)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- 1. Qwen-VL (DashScope) API - for Classification and Transcription ---
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME = "qwen-vl-plus"

# ### NEW ### - Prompt for Step 1: Problem Classification
# This prompt instructs the model to act as a classifier.
CLASSIFICATION_PROMPT = """
Analyze the content of the image(s). Determine the type of problem presented.
Your response MUST be ONLY ONE of the following keywords:

- 'LEETCODE': If the problem requires writing code within a class structure, like a typical LeetCode problem.
- 'ACM': If the problem requires a complete, runnable script that handles its own input and output (e.g., from stdin/stdout), typical of ACM/ICPC contests.
- 'GENERAL': If the problem is a multiple-choice question, a fill-in-the-blank question, a logic puzzle, or any other non-coding question.

Respond with only the single, most appropriate keyword and nothing else.
"""

# Prompt for Step 3: Transcription (Unchanged)
TRANSCRIPTION_PROMPT = """
Your task is to act as a highly accurate OCR engine. Analyze the following image(s) and transcribe all text content you see.
- Transcribe the text in the order it appears across the images.
- Preserve all original formatting, including mathematical formulas, symbols, code indentation, and line breaks.
- Combine the text from all images into a single, seamless block of text.
- Do not add any commentary, explanations, or introductory phrases. Output only the transcribed text.
"""

# --- 2. DeepSeek API - for Problem Solving ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_MODE = "reasoner"
MODEL_NAME = "deepseek-reasoner" if DEEPSEEK_MODEL_MODE == "reasoner" else "deepseek-chat"

# ### NEW ### - A dictionary of specialized prompts for DeepSeek
PROMPT_TEMPLATES = {
    "GENERAL": """
Based on the following problem text, please provide a comprehensive solution.

**Problem:**
---
{transcribed_text}
---

**Your Task:**
1.  **Detailed Analysis:** Provide a detailed thought process and step-by-step reasoning.
2.  **Final Answer:** Clearly state the final answer to the question.
3.  **Generate a Title:** After your solution, start a new line and strictly follow the format 'TITLE: [A 5-10 word summary]' to create a filename title.
""",

    "LEETCODE": """
Based on the following LeetCode-style programming problem, please provide a complete solution.

**Problem:**
---
{transcribed_text}
---

**Your Task:**
1.  **Problem Analysis:** Briefly explain the core logic and chosen algorithm (e.g., dynamic programming, two-pointers). Mention time and space complexity.
2.  **Python Code (LeetCode Mode):** Provide a complete Python solution inside a `Solution` class, with a method signature matching the problem's requirements. The code should be well-commented.
3.  **Generate a Title:** After your solution, start a new line and strictly follow the format 'TITLE: [A 5-10 word summary]' to create a filename title.
""",

    "ACM": """
Based on the following ACM/ICPC-style programming problem, please provide a complete solution.

**Problem:**
---
{transcribed_text}
---

**Your Task:**
1.  **Problem Analysis:** Briefly explain the core logic and chosen algorithm. Mention time and space complexity.
2.  **Python Code (ACM Mode):** Provide a complete, runnable Python script that correctly handles standard input (e.g., using `sys.stdin` or `input()`) and prints the result to standard output as required by the problem. The code should be well-commented.
3.  **Generate a Title:** After your solution, start a new line and strictly follow the format 'TITLE: [A 5-10 word summary]' to create a filename title.
"""
}

# --- 3. Core File Paths ---
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "problem_solver_agent" / "solutions"

# --- 4. Agent Behavior ---
GROUP_TIMEOUT = 10.0
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')

# --- Initialization Function ---
def initialize_directories():
    print("Initializing directory structure...")
    for dir_path in [PROCESSED_DIR, SOLUTION_DIR]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"  - Directory '{dir_path}' confirmed.")
        except OSError as e:
            print(f"Error creating directory '{dir_path}': {e}")
            exit(1)