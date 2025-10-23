# -*- coding: utf-8 -*-
"""
aggregate_for_gemini.py - 项目代码聚合脚本 (V2.0 - 结构化项目版)

此脚本用于将 "自动化多图解题Agent" 项目的所有核心Python源代码文件
合并到一个单一的 .txt 文件中，以适配新的分层项目结构。

该输出文件包含一个为 Gemini Pro 定制的详细引导提示（Prompt），
旨在为其提供完整的项目背景和上下文，以便进行高效的代码分析、
调试或功能扩展。

请将此脚本放置在项目根目录下的 'scripts/' 文件夹中运行。
"""
import os
import sys
from pathlib import Path

# --- 配置 ---

# 最终输出的txt文件名
OUTPUT_FILENAME = "problem-solver-agent-for-gemini.txt"

# 需要从聚合中排除的目录名。
EXCLUDED_DIRS = {
    '__pycache__',
    '.git',
    '.idea',
    '.vscode',
    'venv',
    '.venv',
    'solutions',
    'processed',
    'scripts'  # 排除脚本目录自身
}

# 引导语 (Prompt)，为AI提供精准的项目背景和任务指令。
# 【V2.0 更新】: 在文件路径中加入了源码包和工具集的目录名，为AI提供更精确的上下文。
GEMINI_PROMPT = """
Hello Gemini. I need your expert help with my Python project, an automated agent that solves problems from screenshots.

Your Role:
Act as an expert Python programmer with deep specialization in concurrent programming (threading), filesystem monitoring, and designing robust, event-driven applications that interface with multimodal AI APIs.

Project Context:
The code I'm providing is a complete "Automated Multi-Image Problem Solver Agent". The primary goal is to solve a common user problem: a single math or logic problem is often too long for one screenshot. This agent intelligently groups multiple, consecutively taken screenshots into a single request for a multimodal AI.

The project is structured into a core application package (`problem_solver_agent`) and a set of standalone utilities (`tools`).

The project handles the entire workflow:
1.  **Configuration (`problem_solver_agent/config.py`):** A centralized file for all settings.
2.  **Filesystem Monitoring (`problem_solver_agent/file_monitor.py`):** Uses `watchdog` to monitor for new images.
3.  **Intelligent Grouping (`problem_solver_agent/image_grouper.py`):** The core logic using a `threading.Timer` to group consecutively taken screenshots.
4.  **API Interaction (`problem_solver_agent/qwen_client.py`, `problem_solver_agent/solver_client.py`):** Handles communication with various AI models.
5.  **Utilities (`problem_solver_agent/utils.py`):** Contains helper functions for logging, encoding, etc.
6.  **Main Entrypoint (`problem_solver_agent/main.py`):** Initializes all components and starts the agent.

Your Task:

1.  **Analyze and Understand:** Carefully read and fully comprehend the entire Python codebase provided below. The code is split into multiple files, and you must understand how they interact.

2.  **Wait for Instructions:** After you have fully processed all the code, simply respond with: "I have analyzed the complete Automated Problem Solver Agent and understand its event-driven, time-based grouping architecture. I am ready to assist. What is your question?"

3.  **Assist Me:** Once you've given the confirmation message, I will ask you questions about debugging, code improvements, or new features.

Code Structure:
The complete source code is provided below. Each file is clearly delimited by --- START OF FILE: [filepath] --- and --- END OF FILE: [filepath] --- markers.
"""


def aggregate_scripts():
    """
    递归地查找项目目录下的所有.py文件，并将它们的内容
    合并到一个带有引导性Prompt的txt文件中。
    """
    try:
        # 【关键修改点】
        # 假设此脚本位于 'scripts/' 目录下，项目根目录是其父目录的父目录。
        # Path(__file__).resolve() -> .../scripts/aggregate_for_gemini.py
        # .parents[0] -> .../scripts
        # .parents[1] -> .../ (项目根目录)
        project_root = Path(__file__).resolve().parents[1]
        this_script_name = Path(__file__).name

        print(f"项目根目录已确定为: {project_root}")

        py_files_to_aggregate = []
        for root, dirs, files in os.walk(project_root, topdown=True):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

            for file in files:
                # 只聚合 'problem_solver_agent' 和 'tools' 目录下的.py文件
                current_dir = Path(root)
                if file.endswith('.py'):
                    if 'problem_solver_agent' in current_dir.parts or 'tools' in current_dir.parts:
                        absolute_path = current_dir / file
                        relative_path = absolute_path.relative_to(project_root)
                        py_files_to_aggregate.append(relative_path)

        py_files_to_aggregate.sort()

        if not py_files_to_aggregate:
            print("错误: 未找到任何可供聚合的.py文件。请确保 'problem_solver_agent' 和 'tools' 目录存在且包含代码。")
            return

        print(f"\n即将聚合以下 {len(py_files_to_aggregate)} 个文件:")
        # 使用 as_posix() 确保在所有操作系统上都显示正斜杠
        for rel_path in py_files_to_aggregate:
            print(f"- {rel_path.as_posix()}")

        output_filepath = project_root / OUTPUT_FILENAME
        with open(output_filepath, 'w', encoding='utf-8') as outfile:
            outfile.write(GEMINI_PROMPT)
            outfile.write("\n\n**Aggregated Files:**\n")
            for rel_path in py_files_to_aggregate:
                outfile.write(f"- `{rel_path.as_posix()}`\n")
            outfile.write("\n--- START OF CODE ---\n\n")

            for rel_path in py_files_to_aggregate:
                absolute_path = project_root / rel_path
                try:
                    with open(absolute_path, 'r', encoding='utf-8') as infile:
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