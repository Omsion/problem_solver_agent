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
conda activate llm && cd /d D:\Users\wzw\Pictures\problem_solver_agent && python human_typer.py
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

# ==============================================================================
# --- 全局配置参数 ---
# ==============================================================================

# 【模式开关】设置为 True 以精确粘贴代码；False 以模拟带错误的真人打字
PERFECT_CODE_MODE = False

# 【光标隐藏开关】设置为 True，在模拟输入期间隐藏系统鼠标光标
HIDE_MOUSE_CURSOR = True

# 【注释剥离开关】设置为 True，在模拟输入前自动移除所有Python注释
STRIP_COMMENTS_MODE = True

# --- 核心行为配置 (仅在 PERFECT_CODE_MODE = False 时生效) ---
ERROR_RATE = 0.2

# --- 打字速度与节奏配置 (始终生效) ---
MIN_TYPING_DELAY = 0.1
MAX_TYPING_DELAY = 0.3
PAUSE_CHANCE = 0.2
MIN_PAUSE_DURATION = 0.5
MAX_PAUSE_DURATION = 1.2

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
def strip_comments_from_code(code_string: str) -> str:
    """
    使用tokenize模块，精确地从一个Python代码字符串中移除所有注释，
    并清理掉因移除注释而产生的行尾多余空格。
    """
    try:
        tokens = tokenize.generate_tokens(io.StringIO(code_string).readline)

        # 第一步：移除注释类型的token
        non_comment_tokens = [t for t in tokens if t.type != tokenize.COMMENT]

        # 第二步：将非注释的token重新组合成代码
        code_without_comments = tokenize.untokenize(non_comment_tokens)

        # 【关键优化】第三步：进行后处理，移除每行末尾的多余空格
        lines = code_without_comments.splitlines()
        stripped_lines = [line.rstrip() for line in lines]

        # 重新组合成最终的、纯净的代码
        return '\n'.join(stripped_lines)

    except tokenize.TokenError as e:
        print(f"[警告] 代码解析失败，将返回原始文本。错误: {e}")
        return code_string
    except Exception as e:
        print(f"[警告] 注释剥离过程中发生未知错误: {e}")
        return code_string


# ==============================================================================
# --- 核心模拟器类 ---
# ==============================================================================
class TypingSimulator:
    def simulate_typing(self, text_to_type: str):
        # ... (这个类的内部逻辑保持不变，因为它只负责输入处理好的文本)
        # 为了简洁，此处省略其内部代码，请使用上一版本的完整TypingSimulator类
        print(f"\n--- 开始模拟输入 (模式: {'代码完美' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}) ---")
        lines = text_to_type.splitlines()
        for i, line in enumerate(lines):
            if random.random() < PAUSE_CHANCE: time.sleep(random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION))
            if i > 0:
                # 步骤 1: 发送 'esc' 键信号，强制关闭任何自动补全的提示框。
                keyboard.send('esc')
                # 给予IDE一个极短的反应时间来关闭窗口
                time.sleep(0.03)

                # 步骤 2: 现在可以安全地写入换行符，因为它只会执行换行操作。
                # keyboard.send('enter')
                keyboard.write('\n')

                time.sleep(0.02)
                keyboard.send('home')
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
                else:
                    keyboard.write(char)
                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))
        print("\n--- 模拟输入完成 ---")


# ==============================================================================
# --- 核心触发逻辑 (集成注释剥离) ---
# ==============================================================================
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

        # 2. 【新增】根据开关决定是否剥离注释
        if STRIP_COMMENTS_MODE:
            print("[策略] 注释剥离模式已开启，正在处理...")
            processed_content = strip_comments_from_code(processed_content)

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


# ... (trigger_simulation, cleanup, 和 __main__ 部分保持不变)
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
    # print("  真实打字模拟器已启动... (智能注释剥离版)")
    # print(f"  当前模式: {'代码完美 (无错误)' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}")
    # print(f"  隐藏光标: {'开启' if HIDE_MOUSE_CURSOR else '关闭'}")
    # print(f"  剥离注释: {'开启' if STRIP_COMMENTS_MODE else '关闭'}")
    # print("  用法：复制任意文本，将鼠标光标置于目标位置，然后按下 Ctrl + V")
    # print("  按 ESC 键可随时退出本程序。")
    # print("  (请确保本终端以管理员身份运行)")
    # print("=" * 50)

    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)
    keyboard.wait('esc')
    cleanup()