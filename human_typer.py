# -*- coding: utf-8 -*-

"""
项目名称: 真实打字模拟器 (最终形态)
描述:       1. 采用“双重Home键”策略，彻底解决与编辑器“智能Home键”的冲突，实现绝对精确的缩进。
            2. 引入可选的“鼠标光标跟随”功能，极大提升模拟的沉浸感和视觉效果。
            3. 完善“真人模拟”模式，确保所有错误都必然被修正。
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

# 【模式开关】设置为 True 以精确粘贴代码；False 以模拟带错误的真人打字
PERFECT_CODE_MODE = True

# 【光标跟随开关】设置为 True，让鼠标光标在打字时跟随文本向下移动
MOUSE_FOLLOWS_CURSOR = True
APPROX_LINE_HEIGHT = 18  # 每行的大致像素高度，可根据你的屏幕分辨率和字体大小微调

# --- 核心行为配置 (仅在 PERFECT_CODE_MODE = False 时生效) ---
ERROR_RATE = 0.08

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
# --- 核心模拟器类 (集成所有终极优化) ---
# ==============================================================================
class TypingSimulator:
    def simulate_typing(self, text_to_type: str, start_pos=None):
        print(f"\n--- 开始模拟输入 (模式: {'代码完美' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}) ---")

        lines = text_to_type.splitlines()

        for i, line in enumerate(lines):
            # 随机停顿
            if random.random() < PAUSE_CHANCE:
                time.sleep(random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION))

            # 【优化2】鼠标光标跟随
            if MOUSE_FOLLOWS_CURSOR and start_pos:
                # 使用相对移动，更健壮
                if i > 0:
                    pyautogui.move(0, APPROX_LINE_HEIGHT, duration=0.05)
                else:
                    # 对于第一行，确保鼠标在初始点击位置
                    pyautogui.moveTo(start_pos[0], start_pos[1], duration=0.1)


            # 【优化1】绝对光标复位 + 智能缩进
            if i > 0:
                keyboard.send('enter')
                time.sleep(0.02)
                keyboard.send('home')  # 第一次Home，应对“智能Home键”
                time.sleep(0.02)
                keyboard.send('home')  # 第二次Home，确保到达物理行首
                time.sleep(0.02)

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
# --- 核心触发逻辑 ---
# ==============================================================================
is_simulation_running = False


def run_simulation_in_thread(content):
    global is_simulation_running
    try:
        pyperclip.copy('')
        start_pos = pyautogui.position()  # 记录初始鼠标位置
        pyautogui.click()
        time.sleep(0.1)

        processed_content = content.replace('\r\n', '\n').expandtabs(4)

        simulator = TypingSimulator()
        simulator.simulate_typing(processed_content, start_pos=start_pos)  # 传递初始位置
    except Exception as e:
        print(f"[线程错误] 在模拟过程中发生异常: {e}")
    finally:
        # 【关键点3】当此函数结束，线程销毁，所有鼠标/键盘控制自然结束
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
    print("  真实打字模拟器已启动... (最终形态)")
    print(f"  当前模式: {'代码完美 (无错误)' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}")
    print(f"  鼠标跟随: {'开启' if MOUSE_FOLLOWS_CURSOR else '关闭'}")
    print("  用法：复制任意文本，将鼠标光标置于目标位置，然后按下 Ctrl + V")
    print("  按 ESC 键可随时退出本程序。")
    print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)

    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)
    keyboard.wait('esc')
    cleanup()