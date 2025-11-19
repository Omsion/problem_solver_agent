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
# --- 【新增】代码处理工具函数 (优化版) ---
# ==============================================================================
def _strip_docstrings(source_code: str) -> str:
    """
    使用 ast 模块精确地移除所有函数、类和模块的文档字符串。
    需要 Python 3.9+ 才能使用 ast.unparse。
    """
    try:
        tree = ast.parse(source_code)

        # 定义一个转换器类来遍历和修改AST
        class DocstringRemover(ast.NodeTransformer):
            def visit_FunctionDef(self, node):
                if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Str):
                    node.body = node.body[1:]
                return self.generic_visit(node)

            def visit_ClassDef(self, node):
                return self.visit_FunctionDef(node)  # 类和函数的处理逻辑相同

            def visit_Module(self, node):
                return self.visit_FunctionDef(node)  # 模块的处理逻辑也相同

            visit_AsyncFunctionDef = visit_FunctionDef  # 异步函数也一样处理

        # 应用转换并生成新代码
        transformer = DocstringRemover()
        new_tree = transformer.visit(tree)
        ast.fix_missing_locations(new_tree)
        return ast.unparse(new_tree)
    except (SyntaxError, AttributeError):
        # 如果代码不完整或 ast.unparse 不可用，则优雅地回退
        return source_code
    except Exception as e:
        print(f"[警告] 文档字符串剥离过程中发生未知错误: {e}")
        return source_code


def _strip_hash_comments(code_string: str) -> str:
    """使用 tokenize 模块移除所有单行 # 注释并清理行尾空格。"""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(code_string).readline)
        non_comment_tokens = [t for t in tokens if t.type != tokenize.COMMENT]
        code_without_comments = tokenize.untokenize(non_comment_tokens)
        lines = code_without_comments.splitlines()
        stripped_lines = [line.rstrip() for line in lines]
        # 移除因删除注释而产生的空行
        final_lines = [line for line in stripped_lines if line]
        return '\n'.join(final_lines)
    except tokenize.TokenError as e:
        print(f"[警告] 单行注释剥离失败，将返回原始文本。错误: {e}")
        return code_string
    except Exception as e:
        print(f"[警告] 注释剥离过程中发生未知错误: {e}")
        return code_string


def process_code_for_typing(source_code: str) -> str:
    """
    对源代码进行两阶段净化，为模拟输入做准备。
    """
    processed_code = source_code

    # 阶段一：剥离文档字符串
    if STRIP_DOCSTRINGS_MODE:
        print("[策略] 文档字符串剥离模式已开启，正在处理...")
        processed_code = _strip_docstrings(processed_code)

    # 阶段二：剥离单行注释
    if STRIP_COMMENTS_MODE:
        print("[策略] 单行注释剥离模式已开启，正在处理...")
        processed_code = _strip_hash_comments(processed_code)

    return processed_code


# ==============================================================================
# --- 核心模拟器与触发逻辑 (已更新) ---
# ==============================================================================
class TypingSimulator:
    def simulate_typing(self, text_to_type: str):
        print(f"\n--- 开始模拟输入 (模式: {'代码完美' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}) ---")
        lines = text_to_type.splitlines()
        for i, line in enumerate(lines):
            if not line.strip():  # 如果是空行，则只打印换行符
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

        # 1. 基础预处理
        processed_content = content.replace('\r\n', '\n').expandtabs(4)

        # 2. 【核心修改点】调用新的两阶段代码净化函数
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
    # print("  真实打字模拟器已启动... (智能净化版)")
    # print(f"  - 剥离文档字符串: {'开启' if STRIP_DOCSTRINGS_MODE else '关闭'}")
    # print(f"  - 剥离单行注释: {'开启' if STRIP_COMMENTS_MODE else '关闭'}")
    # print("  用法：复制任意Python代码，将光标置于目标位置，然后按下 Ctrl + V")
    # print("  按 ESC 键可随时退出本程序。")
    # print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)
    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)
    keyboard.wait('esc')
    cleanup()