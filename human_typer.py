# -*- coding: utf-8 -*-

"""
项目名称: 真实打字模拟器 (最终形态)
描述:       1. 完善“真人模拟”模式，确保所有模拟错误都必然被修正，保证最终文本100%正确。
            2. 引入真正的“隐藏/恢复鼠标光标”功能，提供最干净、无干扰的视觉体验。
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
import ctypes  # 新增：用于调用Windows API隐藏/显示光标

# ==============================================================================
# --- 全局配置参数 ---
# ==============================================================================

# 【模式开关】设置为 True 以精确粘贴代码；False 以模拟带错误的真人打字
PERFECT_CODE_MODE = True

# 【光标隐藏开关】设置为 True，在模拟输入期间隐藏系统鼠标光标
HIDE_MOUSE_CURSOR = True

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
# --- 核心模拟器类 (集成100%修正逻辑) ---
# ==============================================================================
class TypingSimulator:
    def simulate_typing(self, text_to_type: str):
        print(f"\n--- 开始模拟输入 (模式: {'代码完美' if PERFECT_CODE_MODE else '真人模拟(100%修正)'}) ---")

        lines = text_to_type.splitlines()

        for i, line in enumerate(lines):
            # 随机停顿
            if random.random() < PAUSE_CHANCE:
                time.sleep(random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION))

            # 绝对光标复位
            if i > 0:
                keyboard.send('enter')
                time.sleep(0.01)
                keyboard.send('home')
                time.sleep(0.01)

            # 逐字输入当前行的全部内容
            for char in line:
                # 【优化1】完善错误模拟逻辑
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

        print("\n--- 模拟输入完成 ---")


# ==============================================================================
# --- 核心触发逻辑 (集成光标隐藏) ---
# ==============================================================================
is_simulation_running = False


def run_simulation_in_thread(content):
    global is_simulation_running
    try:
        # 【优化2】在模拟开始时隐藏光标
        if HIDE_MOUSE_CURSOR:
            ctypes.windll.user32.ShowCursor(False)
            print("[策略] 鼠标光标已隐藏。")

        pyperclip.copy('')
        pyautogui.click()
        time.sleep(0.1)

        processed_content = content.replace('\r\n', '\n').expandtabs(4)

        simulator = TypingSimulator()
        simulator.simulate_typing(processed_content)
    except Exception as e:
        print(f"[线程错误] 在模拟过程中发生异常: {e}")
    finally:
        # 【优化2】在模拟结束后，无论成功与否，都必须恢复光标
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


# ==============================================================================
# --- 程序退出清理 ---
# ==============================================================================
def cleanup(signum=None, frame=None):
    print("\n[清理] 检测到退出信号，正在卸载键盘钩子...")
    keyboard.unhook_all()
    # 确保退出时，如果光标被隐藏了，也能恢复
    if HIDE_MOUSE_CURSOR:
        ctypes.windll.user32.ShowCursor(True)
        print("[清理] 鼠标光标已强制恢复。")
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
    print(f"  隐藏光标: {'开启' if HIDE_MOUSE_CURSOR else '关闭'}")
    print("  用法：复制任意文本，将鼠标光标置于目标位置，然后按下 Ctrl + V")
    print("  按 ESC 键可随时退出本程序。")
    print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)

    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)
    keyboard.wait('esc')
    cleanup()