# -*- coding: utf-8 -*-

r"""
项目名称: 真实打字模拟器 (最终形态：智能注释剥离)
描述:       1. 新增“注释剥离”模式，利用`tokenize`模块精确移除所有Python注释。
            2. 采用“绝对空格注入”策略，彻底解决所有缩进问题。
            3. 引入“隐藏/恢复鼠标光标”功能，提供最干净、无干扰的视觉体验。
作者:       [Your Name/Agent]
依赖库:     keyboard, pyperclip, pyautogui
运行方式:   在管理员终端中执行 :
python human_typer.py
conda activate llm; cd "D:\Users\wzw\Pictures\OnlineTest"; python tools/human_typer.py

F4/insert键位不影响在网页IDE上进行模拟输出
"""

import time
import random
import pyperclip
import keyboard
import threading
import pyautogui
import signal
import sys
import ctypes
import tokenize  # 新增：用于精确解析和剥离注释
import io  # 新增：配合tokenize使用
import ast  # <-- 新增：用于解析Python代码结构

# ==============================================================================
# --- 全局配置参数 ---
# ==============================================================================

# 【模式开关】设置为 True 以精确粘贴代码；False 以模拟带错误的真人打字
PERFECT_CODE_MODE = False

# 【光标隐藏开关】设置为 True，在模拟输入期间隐藏系统鼠标光标
HIDE_MOUSE_CURSOR = True

# 【文档字符串剥离开关】设置为 True，在模拟输入前自动移除所有 """...""" 形式的文档字符串
STRIP_DOCSTRINGS_MODE = True
# 【单行注释剥离开关】设置为 True，在模拟输入前自动移除所有 # 形式的注释
STRIP_COMMENTS_MODE = True

# --- 核心行为配置 (仅在 PERFECT_CODE_MODE = False 时生效) ---
ERROR_RATE = 0.2

# --- 打字速度与节奏配置 (始终生效) ---
# 真实人较慢打字速度
MIN_TYPING_DELAY = 0.25
MAX_TYPING_DELAY = 0.50
PAUSE_CHANCE = 0.2
MIN_PAUSE_DURATION = 0.99
MAX_PAUSE_DURATION = 1.9

# 较快打字速度
# MIN_TYPING_DELAY = 0.1
# MAX_TYPING_DELAY = 0.3
# PAUSE_CHANCE = 0.2
# MIN_PAUSE_DURATION = 0.5
# MAX_PAUSE_DURATION = 1.2


# ==============================================================================
# --- 键盘布局定义 ---
# ==============================================================================
KEYBOARD_LAYOUT = {'q': ['w', 'a'], 'w': ['q', 's', 'e'], 'e': ['w', 'd', 'r'], 'r': ['e', 'f', 't'],
                   't': ['r', 'g', 'y'], 'y': ['t', 'h', 'u'], 'u': ['y', 'j', 'i'], 'i': ['u', 'k', 'o'],
                   'o': ['i', 'l', 'p'], 'p': ['o', 'l'], 'a': ['q', 's', 'z'], 's': ['a', 'w', 'd', 'x'],
                   'd': ['s', 'e', 'f', 'c'], 'f': ['d', 'r', 'g', 'v'], 'g': ['f', 't', 'h', 'b'],
                   'h': ['g', 'y', 'j', 'n'], 'j': ['h', 'u', 'k', 'm'], 'k': ['j', 'i', 'l'], 'l': ['k', 'o', 'p'],
                   'z': ['a', 's', 'x'], 'x': ['z', 's', 'd', 'c'], 'c': ['x', 'd', 'f', 'v'],
                   'v': ['c', 'f', 'g', 'b'], 'b': ['v', 'g', 'h', 'n'], 'n': ['b', 'h', 'j', 'm'],
                   'm': ['n', 'j', 'k']}


# ==============================================================================
# --- 【核心修改点】代码净化工具函数 (V2.1) ---
# ==============================================================================

def _strip_docstrings(source_code: str) -> str:
    """
    使用 ast 模块精确识别文档字符串的行号，并从原始文本中移除它们，以保留格式。
    """
    try:
        tree = ast.parse(source_code)
        docstring_lines = set()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef, ast.Module)):
                # ast.get_docstring() 是一个安全的方式来获取文档字符串节点
                docstring_node = ast.get_docstring(node, clean=False)
                if docstring_node:
                    # 找到文档字符串所在的表达式节点
                    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Str):
                        expr_node = node.body[0]
                        # 记录从开始到结束的所有行号
                        start_line = expr_node.lineno
                        end_line = expr_node.end_lineno
                        docstring_lines.update(range(start_line, end_line + 1))

        if not docstring_lines:
            return source_code

        original_lines = source_code.splitlines()
        # 只保留不在 docstring_lines 集合中的行
        result_lines = [line for i, line in enumerate(original_lines, 1) if i not in docstring_lines]
        return '\n'.join(result_lines)
    except Exception:
        # 如果解析失败，优雅地回退到原始代码
        return source_code


def _strip_hash_comments(code_string: str) -> str:
    """使用 tokenize 模块移除所有单行 # 注释，同时保留空行。"""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(code_string).readline)
        # 只过滤掉 COMMENT 类型的 token
        non_comment_tokens = [t for t in tokens if t.type != tokenize.COMMENT]
        # untokenize 会保留原始的换行和空行结构
        return tokenize.untokenize(non_comment_tokens)
    except Exception:
        # 如果解析失败，优雅地回退
        return code_string


def process_code_for_typing(source_code: str) -> str:
    """
    对源代码进行两阶段净化，为模拟输入做准备。
    """
    processed_code = source_code

    if STRIP_DOCSTRINGS_MODE:
        print("[策略] 文档字符串剥离模式已开启，正在处理...")
        processed_code = _strip_docstrings(processed_code)

    if STRIP_COMMENTS_MODE:
        print("[策略] 单行注释剥离模式已开启，正在处理...")
        processed_code = _strip_hash_comments(processed_code)

    return processed_code


# ==============================================================================
# --- 核心模拟器与触发逻辑 (保持不变) ---
# ==============================================================================
class TypingSimulator:
    def simulate_typing(self, text_to_type: str):
        print(f"\n--- 开始模拟输入 (模式: {'代码完美' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}) ---")
        lines = text_to_type.splitlines()
        for i, line in enumerate(lines):
            # 保持原有的空行处理逻辑
            if not line.strip() and i > 0:
                keyboard.write('\n')
                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))
                continue

            if random.random() < PAUSE_CHANCE: time.sleep(random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION))

            if i > 0:
                keyboard.send('esc')
                time.sleep(0.03)
                keyboard.write('\n')
                time.sleep(0.02)
                keyboard.send('home')
                time.sleep(0.02)

            leading_spaces = len(line) - len(line.lstrip(' '))
            if leading_spaces > 0:
                keyboard.write(' ' * leading_spaces)
                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY * 1.5))

            content_of_line = line.lstrip(' ')
            for char in content_of_line:
                if not PERFECT_CODE_MODE and char.lower() in KEYBOARD_LAYOUT and random.random() < ERROR_RATE:
                    error_char = random.choice(KEYBOARD_LAYOUT[char.lower()])
                    keyboard.write(error_char)
                    time.sleep(random.uniform(0.05, 0.15))
                    keyboard.send('backspace')
                    time.sleep(random.uniform(0.05, 0.1))
                keyboard.write(char)
                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))
        print("\n--- 模拟输入完成 ---")


is_simulation_running = False


def run_simulation_in_thread(content):
    global is_simulation_running
    try:
        if HIDE_MOUSE_CURSOR:
            ctypes.windll.user32.ShowCursor(False)
            print("[策略] 鼠标光标已隐藏。")

        pyperclip.copy('')
        pyautogui.click()
        time.sleep(0.1)

        processed_content = content.replace('\r\n', '\n').expandtabs(4)
        processed_content = process_code_for_typing(processed_content)

        simulator = TypingSimulator()
        simulator.simulate_typing(processed_content)
    except Exception as e:
        print(f"[线程错误] 在模拟过程中发生异常: {e}")
    finally:
        if HIDE_MOUSE_CURSOR:
            ctypes.windll.user32.ShowCursor(True)
            print("[策略] 鼠标光标已恢复。")

        is_simulation_running = False
        print("[状态] 模拟线程已结束，所有控制已释放。等待下一次触发...")


def trigger_simulation():
    global is_simulation_running
    if is_simulation_running: return
    is_simulation_running = True
    try:
        clipboard_content = pyperclip.paste()
        if clipboard_content and isinstance(clipboard_content, str):
            threading.Thread(target=run_simulation_in_thread, args=(clipboard_content,)).start()
        else:
            is_simulation_running = False
    except Exception:
        is_simulation_running = False


def cleanup(signum=None, frame=None):
    print("\n[清理] 检测到退出信号，正在卸载键盘钩子...")
    keyboard.unhook_all()
    if HIDE_MOUSE_CURSOR:
        ctypes.windll.user32.ShowCursor(True)
        print("[清理] 鼠标光标已强制恢复。")
    print("[清理] 钩子已卸载，程序安全退出。")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    print("=" * 50)
    print("  真实打字模拟器已启动... (V2.1 - 格式保持版)")
    print(f"  - 剥离文档字符串: {'开启' if STRIP_DOCSTRINGS_MODE else '关闭'}")
    print(f"  - 剥离单行注释: {'开启' if STRIP_COMMENTS_MODE else '关闭'}")
    print("  用法：复制任意Python代码，将光标置于目标位置，然后按下 Ctrl + V")
    print("  按 ESC 键可随时退出本程序。")
    print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)
    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)
    keyboard.wait('esc')
    cleanup()