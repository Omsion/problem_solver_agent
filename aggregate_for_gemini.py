# -*- coding: utf-8 -*-
"""
aggregate_for_gemini.py - 项目代码聚合脚本

此脚本用于将 "自动化多图解题Agent" 项目的所有核心Python源代码文件
合并到一个单一的 .txt 文件中。

该输出文件包含一个为 Gemini Pro 定制的详细引导提示（Prompt），
旨在为其提供完整的项目背景和上下文，以便进行高效的代码分析、
调试或功能扩展。

请将此脚本放置在项目根目录 (problem_solver_agent/) 下运行。
"""
import os
import sys
from pathlib import Path

# --- 配置 ---

# 最终输出的txt文件名
OUTPUT_FILENAME = "problem-solver-agent-for-gemini.txt"

# 需要从聚合中排除的目录名。
# 这可以防止包含虚拟环境、数据、输出、Git历史等无关内容。
EXCLUDED_DIRS = {
    '__pycache__',
    '.git',
    '.idea',
    '.vscode',
    'venv',
    '.venv',
    'solutions',  # 包含生成的结果，不是源代码
}

# 引导语 (Prompt)，为AI提供精准的项目背景和任务指令。
GEMINI_PROMPT = """
Hello Gemini. I need your expert help with my Python project, an automated agent that solves problems from screenshots.

Your Role:
Act as an expert Python programmer with deep specialization in concurrent programming (threading), filesystem monitoring, and designing robust, event-driven applications that interface with multimodal AI APIs.

Project Context:
The code I'm providing is a complete "Automated Multi-Image Problem Solver Agent". The primary goal is to solve a common user problem: a single math or logic problem is often too long for one screenshot. This agent intelligently groups multiple, consecutively taken screenshots into a single request for a multimodal AI like DeepSeek.

The project handles the entire workflow:
1.  **Configuration (`config.py`):** A centralized file for all settings, including API keys, monitored folder paths, and the crucial 'grouping timeout' duration.
2.  **Filesystem Monitoring (`file_monitor.py`):** Uses the `watchdog` library to monitor a specific folder (e.g., the user's default screenshot directory) for new image files in real-time.
3.  **Intelligent Grouping (`image_grouper.py`):** This is the core logic. It uses a `threading.Timer`. When a new image is detected, it resets this timer. If the timer expires (meaning the user has stopped taking screenshots for a few seconds), it processes all images collected during that time window as a single "group". This is how it solves the multi-screenshot problem.
4.  **API Interaction (`deepseek_client.py`):** It takes a group of image paths, encodes them to Base64, and constructs a multi-image request payload for the DeepSeek Vision API. It also includes a specially crafted prompt instructing the model to generate a title for the problem.
5.  **Utilities (`utils.py`):** Contains helper functions for logging, Base64 encoding, parsing the title from the AI's response, and sanitizing the title to create a valid filename.
6.  **Main Entrypoint (`main.py`):** Initializes all components, starts the file monitor, and keeps the application running.

Your Task:

1.  **Analyze and Understand:** Carefully read and fully comprehend the entire Python codebase provided below. The code is split into multiple files, and you must understand how they interact, especially the event flow from `file_monitor.py` to `image_grouper.py` and the role of the `threading.Timer`.

2.  **Wait for Instructions:** After you have fully processed all the code, simply respond with: "I have analyzed the complete Automated Problem Solver Agent and understand its event-driven, time-based grouping architecture. I am ready to assist. What is your question?"

3.  **Assist Me:** Once you've given the confirmation message, I will ask you questions. You should then help me with tasks such as:
    *   Debugging specific errors in the file handling or API logic.
    *   Explaining complex parts of the code (e.g., the thread safety of the `ImageGrouper` class).
    *   Suggesting code improvements for robustness, performance, or adding features like a failure queue.
    *   Refactoring the code to be more asynchronous.
    *   Adding new features (e.g., a simple GUI with Tkinter).

Code Structure:
The complete source code is provided below. Each file is clearly delimited by --- START OF FILE: [filepath] --- and --- END OF FILE: [filepath] --- markers.
"""


def aggregate_scripts():
    """
    递归地查找项目目录下的所有.py文件，并将它们的内容
    合并到一个带有引导性Prompt的txt文件中。
    """
    try:
        # 假设此脚本位于项目根目录
        project_root = Path(__file__).resolve().parent
        this_script_name = Path(__file__).name

        print(f"项目根目录: {project_root}")
        print(f"本脚本文件名 (将被排除): {this_script_name}")

        py_files_to_aggregate = []
        for root, dirs, files in os.walk(project_root, topdown=True):
            # 修改dirs列表可以阻止os.walk深入到被排除的目录中
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

            for file in files:
                if file.endswith('.py') and file != this_script_name:
                    absolute_path = Path(root) / file
                    # 获取相对于项目根的路径用于在输出文件中标记，更清晰
                    relative_path = absolute_path.relative_to(project_root)
                    py_files_to_aggregate.append(relative_path)

        # 排序以确保每次运行生成的文件内容顺序一致
        py_files_to_aggregate.sort()

        if not py_files_to_aggregate:
            print("错误: 未找到任何可供聚合的.py文件。请确保此脚本位于项目根目录下。")
            return

        print(f"\n即将聚合以下 {len(py_files_to_aggregate)} 个文件:")
        for rel_path in py_files_to_aggregate:
            print(f"- {rel_path}")

        # 开始写入输出文件
        output_filepath = project_root / OUTPUT_FILENAME
        with open(output_filepath, 'w', encoding='utf-8') as outfile:
            # 1. 写入引导语
            outfile.write(GEMINI_PROMPT)

            # 2. 在引导语后附上被聚合的文件列表
            outfile.write("\n\n**Aggregated Files:**\n")
            for rel_path in py_files_to_aggregate:
                outfile.write(f"- `{rel_path.as_posix()}`\n")
            outfile.write("\n--- START OF CODE ---\n\n")

            # 3. 依次读取每个文件并写入
            for rel_path in py_files_to_aggregate:
                absolute_path = project_root / rel_path
                try:
                    with open(absolute_path, 'r', encoding='utf-8') as infile:
                        # 使用相对路径和正斜杠作为文件标记
                        clean_rel_path = rel_path.as_posix()
                        outfile.write(f"--- START OF FILE: {clean_rel_path} ---\n\n")
                        outfile.write(infile.read())
                        outfile.write(f"\n\n--- END OF FILE: {clean_rel_path} ---\n\n\n")
                except Exception as e:
                    error_message = f"--- ERROR READING FILE: {clean_rel_path} ---\n"
                    error_message += f"--- REASON: {str(e)} ---\n\n"
                    outfile.write(error_message)
                    print(f"\n警告: 读取文件 {rel_path} 时发生错误: {e}")

        print(f"\n✅ **成功!** 已将 {len(py_files_to_aggregate)} 个脚本合并到: {output_filepath}")

    except Exception as e:
        print(f"\n❌ **发生严重错误:** {e}")
        sys.exit(1)


if __name__ == "__main__":
    aggregate_scripts()