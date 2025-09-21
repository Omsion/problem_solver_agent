# -*- coding: utf-8 -*-
"""
自动化多图解题Agent - 配置文件 (双模型流水线版)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- 1. Qwen-VL (DashScope) API - for Image-to-Text ---
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME = "qwen-vl-plus"  # Vision model for transcription

# Prompt for Qwen-VL: Instructs it to accurately transcribe text from images.
QWEN_PROMPT = """
Your task is to act as a highly accurate OCR engine. Analyze the following image(s) and transcribe all text content you see.
- Transcribe the text in the order it appears across the images.
- Preserve all original formatting, including mathematical formulas, symbols, code indentation, and line breaks.
- Combine the text from all images into a single, seamless block of text.
- Do not add any commentary, explanations, or introductory phrases. Output only the transcribed text.
"""

# --- 2. DeepSeek API - for Problem Solving ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_MODE = "reasoner"  # "reasoner" or "chat"
MODEL_NAME = "deepseek-reasoner" if DEEPSEEK_MODEL_MODE == "reasoner" else "deepseek-chat"

# Prompt for DeepSeek: Instructs it to solve the problem based on the text provided by Qwen.
DEEPSEEK_PROMPT_TEMPLATE = """
Based on the following transcribed problem text, please provide a comprehensive solution.

**Transcribed Problem:**
---
{transcribed_text}
---

**Your Task:**
1.  **Detailed Analysis:** Provide a detailed thought process, step-by-step reasoning, and the final answer.
2.  **Clear Formatting:** Use Markdown for clarity.
3.  **Generate a Title:** After your solution, start a new line and strictly follow the format 'TITLE: [A 5-10 word summary of the problem]' to create a suitable filename title.
"""

# --- 3. Core File Paths ---
ROOT_DIR = Path(r"D:\Users\wzw\Pictures")
MONITOR_DIR = ROOT_DIR / "Screenshots"
PROCESSED_DIR = ROOT_DIR / "processed"
SOLUTION_DIR = ROOT_DIR / "problem_solver_agent" / "solutions"

# --- 4. Agent Behavior ---
GROUP_TIMEOUT = 5.0
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