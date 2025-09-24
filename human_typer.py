# -*- coding: utf-8 -*-

"""
项目名称: 真实打字模拟器 (最终优化版)
描述:       1. 引入智能缩进处理，将4个空格块替换为Tab键输入，使缩进更自然、更高效。
            2. 完善“真人模拟”模式，确保所有模拟错误都必然被修正，保证最终文本100%正确。
作者:       [Your Name/Agent]
依赖库:     keyboard, pyperclip, pyautogui
运行方式:   在管理员终端中执行 python human_typer.py
"""

import time
import random
import pyperclip
import keyboard
import threading
import pyautogui
import signal
import sys

# ==============================================================================
# --- 全局配置参数 ---
# ==============================================================================

# 设置为 True 以精确粘贴代码，设置为 False 以模拟带错误的真人打字
PERFECT_CODE_MODE = True

# --- 核心行为配置 (仅在 PERFECT_CODE_MODE = False 时生效) ---
ERROR_RATE = 0.08
# BACKSPACE_CHANCE 已被移除，因为现在所有错误都将被100%修正。

# --- 打字速度与节奏配置 (始终生效) ---
MIN_TYPING_DELAY = 0.03
MAX_TYPING_DELAY = 0.12
PAUSE_CHANCE = 0.04
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
# --- 核心模拟器类 (集成两大优化) ---
# ==============================================================================
class TypingSimulator:
    def simulate_typing(self, text_to_type: str):
        print(f"\n--- 开始模拟输入 (模式: {'代码完美' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}) ---")

        lines = text_to_type.splitlines()

        for i, line in enumerate(lines):
            # 随机停顿
            if random.random() < PAUSE_CHANCE:
                time.sleep(random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION))

            # 【优化1】智能缩进处理
            leading_spaces = len(line) - len(line.lstrip(' '))
            num_tabs = leading_spaces // 4
            remaining_spaces = leading_spaces % 4

            for _ in range(num_tabs):
                keyboard.send('tab')
                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))
            for _ in range(remaining_spaces):
                keyboard.write(' ')
                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))

            # 逐字输入剥离了前导空格的行内容
            content_of_line = line.lstrip(' ')
            for char in content_of_line:
                # 【优化2】完善错误模拟逻辑
                # 只有在非完美模式下才模拟错误
                if not PERFECT_CODE_MODE and char.lower() in KEYBOARD_LAYOUT and random.random() < ERROR_RATE:
                    error_char = random.choice(KEYBOARD_LAYOUT[char.lower()])
                    # 模拟错误输入
                    keyboard.write(error_char)
                    time.sleep(random.uniform(0.05, 0.15))  # 犯错后的短暂迟疑
                    # 必然、立即修正错误
                    keyboard.send('backspace')
                    time.sleep(random.uniform(0.05, 0.1))
                    keyboard.write(char)  # 输入正确的字符
                else:
                    # 正常输入
                    keyboard.write(char)

                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))

            # 输入完一行后，如果不是最后一行，则按回车换行
            if i < len(lines) - 1:
                keyboard.send('enter')
                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))

        print("\n--- 模拟输入完成 ---")


# ==============================================================================
# --- 核心触发逻辑 ---
# ==============================================================================
is_simulation_running = False


def run_simulation_in_thread(content):
    global is_simulation_running
    try:
        pyperclip.copy('')
        pyautogui.click()
        time.sleep(0.1)

        processed_content = content.replace('\r\n', '\n').expandtabs(4)

        simulator = TypingSimulator()
        simulator.simulate_typing(processed_content)
    except Exception as e:
        print(f"[线程错误] 在模拟过程中发生异常: {e}")
    finally:
        is_simulation_running = False


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


# ==============================================================================
# --- 程序退出清理 ---
# ==============================================================================
def cleanup(signum=None, frame=None):
    print("\n[清理] 检测到退出信号，正在卸载键盘钩子...")
    keyboard.unhook_all()
    print("[清理] 钩子已卸载，程序安全退出。")
    sys.exit(0)


# ==============================================================================
# --- 主程序入口 ---
# ==============================================================================
if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print("=" * 50)
    print("  真实打字模拟器已启动... (最终优化版)")
    print(f"  当前模式: {'代码完美 (无错误)' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}")
    print("  用法：复制任意文本，将鼠标光标置于目标位置，然后按下 Ctrl + V")
    print("  按 ESC 键可随时退出本程序。")
    print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)

    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)
    keyboard.wait('esc')
    cleanup()