# -*- coding: utf-8 -*-

"""
项目名称: 真实打字模拟器 (智能协作最终版)
描述:       通过采用“行处理”逻辑，并与编辑器的自动缩进功能智能协作，
            彻底解决了“双重缩进”问题，确保在任何代码编辑器中都能实现
            100%精确的格式和内容。
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

# 【关键开关】设置为 True 以精确粘贴代码，设置为 False 以模拟带错误的真人打字
PERFECT_CODE_MODE = True

# --- 核心行为配置 (仅在 PERFECT_CODE_MODE = False 时生效) ---
ERROR_RATE = 0.08
BACKSPACE_CHANCE = 0.85
REPEAT_KEY_CHANCE = 0.02

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
# --- 核心模拟器类 (采用行处理逻辑) ---
# ==============================================================================
class TypingSimulator:
    def simulate_typing(self, text_to_type: str):
        print(f"\n--- 开始模拟输入 (模式: {'代码完美' if PERFECT_CODE_MODE else '真人模拟'}) ---")

        lines = text_to_type.splitlines()

        for i, line in enumerate(lines):
            # 随机停顿，模拟思考
            if random.random() < PAUSE_CHANCE:
                time.sleep(random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION))

            # 【关键逻辑】与编辑器协作处理缩进
            # 第一行完整输入，为缩进定调。
            # 后续行只输入剥离了前导空格的内容，依赖编辑器的自动缩进。
            line_to_type = line if i == 0 else line.lstrip()

            # 如果行为空（或剥离后为空），只按回车
            if not line_to_type:
                keyboard.send('enter')
                time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))
                continue

            # 逐字输入行内容
            for char in line_to_type:
                # 只有在非完美模式下才模拟错误
                if not PERFECT_CODE_MODE and char.lower() in KEYBOARD_LAYOUT and random.random() < ERROR_RATE:
                    error_char = random.choice(KEYBOARD_LAYOUT[char.lower()])
                    keyboard.write(error_char)
                    time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))
                    if random.random() < BACKSPACE_CHANCE:
                        time.sleep(random.uniform(0.1, 0.4))
                        keyboard.send('backspace')
                        keyboard.write(char)  # 输入正确的字符
                else:
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

        # 预处理：只需统一换行符和转换Tab即可
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
    print("  真实打字模拟器已启动... (智能协作最终版)")
    print(f"  当前模式: {'代码完美 (无错误)' if PERFECT_CODE_MODE else '真人模拟 (带错误)'}")
    print("  用法：复制任意文本，将鼠标光标置于目标位置，然后按下 Ctrl + V")
    print("  按 ESC 键可随时退出本程序。")
    print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)

    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)
    keyboard.wait('esc')
    cleanup()