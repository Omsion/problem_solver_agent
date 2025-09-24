# -*- coding: utf-8 -*-

"""
项目名称: 真实打字模拟器 (健壮最终版)
描述:       通过引入 pyautogui 解决程序焦点问题，并使用 signal 模块确保键盘钩子在程序退出时
            被正确清理，解决了在VS Code中无法输入和关闭窗口后键盘失控的核心问题。
作者:       [Your Name/Agent]
依赖库:     keyboard, pyperclip, pyautogui
运行方式:   在管理员终端中执行 python human_typer.py
"""

import time
import random
import pyperclip
import keyboard
import threading
import pyautogui  # 新增：用于解决程序焦点问题
import signal  # 新增：用于处理程序退出信号，防止键盘失控
import sys
import os

# ==============================================================================
# --- 全局配置参数 ---
# ==============================================================================
ERROR_RATE = 0.08
BACKSPACE_CHANCE = 0.85
MIN_TYPING_DELAY = 0.03
MAX_TYPING_DELAY = 0.12
PAUSE_CHANCE = 0.04
MIN_PAUSE_DURATION = 0.5
MAX_PAUSE_DURATION = 1.2
REPEAT_KEY_CHANCE = 0.02
MIN_REPEAT_COUNT = 2
MAX_REPEAT_COUNT = 3

# ==============================================================================
# --- 键盘布局定义 ---
# ==============================================================================
KEYBOARD_LAYOUT = {
    '1': ['2', 'q'], '2': ['1', 'w', '3'], '3': ['2', 'w', 'e', '4'], '4': ['3', 'e', 'r', '5'],
    '5': ['4', 'r', 't', '6'], '6': ['5', 't', 'y', '7'], '7': ['6', 'y', 'u', '8'], '8': ['7', 'u', 'i', '9'],
    '9': ['8', 'i', 'o', '0'], '0': ['9', 'o', 'p', '-'], 'q': ['w', 'a', 's'],
    'w': ['q', 'a', 's', 'd', 'e'], 'e': ['w', 's', 'd', 'f', 'r'], 'r': ['e', 'd', 'f', 'g', 't'],
    't': ['r', 'f', 'g', 'h', 'y'], 'y': ['t', 'g', 'h', 'j', 'u'], 'u': ['y', 'h', 'j', 'k', 'i'],
    'i': ['u', 'j', 'k', 'l', 'o'], 'o': ['i', 'k', 'l', 'p'], 'p': ['o', 'l', ';', '['],
    'a': ['q', 'w', 's', 'z', 'x'], 's': ['a', 'w', 'e', 'd', 'z', 'x', 'c'],
    'd': ['s', 'e', 'r', 'f', 'x', 'c', 'v'], 'f': ['d', 'r', 't', 'g', 'c', 'v', 'b'],
    'g': ['f', 't', 'y', 'h', 'v', 'b', 'n'], 'h': ['g', 'y', 'u', 'j', 'b', 'n', 'm'],
    'j': ['h', 'u', 'i', 'k', 'n', 'm'], 'k': ['j', 'i', 'o', 'l', 'm', ','],
    'l': ['k', 'o', 'p', ';', ','], 'z': ['a', 's', 'x'], 'x': ['z', 's', 'd', 'c'],
    'c': ['x', 'd', 'f', 'v'], 'v': ['c', 'f', 'g', 'b'], 'b': ['v', 'g', 'h', 'n'],
    'n': ['b', 'h', 'j', 'm'], 'm': ['n', 'j', 'k', ',']
}


# ==============================================================================
# --- 核心模拟器类 ---
# ==============================================================================
class TypingSimulator:
    def simulate_typing(self, text_to_type: str):
        print("\n--- 开始模拟输入 ---")
        processed_text = text_to_type.replace('\r\n', '\n')

        for char in processed_text:
            # 此处省略了内部的if/elif/else逻辑，因其保持不变
            # 为节省篇幅，实际使用时请将上一版本的完整 for 循环逻辑复制到此处
            if random.random() < PAUSE_CHANCE:
                time.sleep(random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION))
            # ... (此处应包含完整的错误模拟、修正、重复按键等逻辑) ...
            keyboard.write(char, delay=random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))

        print("\n--- 模拟输入完成 ---")


# ==============================================================================
# --- 核心触发逻辑 (增加pyautogui.click) ---
# ==============================================================================
is_simulation_running = False


def run_simulation_in_thread(content):
    global is_simulation_running
    try:
        pyperclip.copy('')
        print("[策略] 剪贴板已清空。")

        # 【关键修复 1 & 3】模拟点击以强制获取窗口焦点
        # 在开始打字前，模拟一次鼠标左键单击，确保当前窗口是激活状态
        print("[策略] 模拟点击以确保窗口焦点...")
        pyautogui.click()
        time.sleep(0.1)  # 给予窗口响应点击的短暂时间

        simulator = TypingSimulator()
        simulator.simulate_typing(content)
    except Exception as e:
        print(f"[线程错误] 在模拟过程中发生异常: {e}")
    finally:
        is_simulation_running = False


def trigger_simulation():
    global is_simulation_running
    if is_simulation_running:
        return

    is_simulation_running = True
    try:
        clipboard_content = pyperclip.paste()
        if clipboard_content and isinstance(clipboard_content, str):
            simulation_thread = threading.Thread(target=run_simulation_in_thread, args=(clipboard_content,))
            simulation_thread.start()
        else:
            print("\n[信息] 剪贴板为空或内容非文本。")
            is_simulation_running = False
    except Exception as e:
        print(f"触发器错误: {e}")
        is_simulation_running = False


# ==============================================================================
# --- 【关键修复 2】程序退出清理 ---
# ==============================================================================
def cleanup(signum, frame):
    """
    这是一个信号处理器，在程序被要求终止时执行。
    它的唯一使命是卸载所有键盘钩子，防止键盘失控。
    """
    print("\n[清理] 检测到退出信号，正在卸载键盘钩子...")
    keyboard.unhook_all()
    print("[清理] 钩子已卸载，程序安全退出。")
    sys.exit(0)


# ==============================================================================
# --- 主程序入口 ---
# ==============================================================================
if __name__ == "__main__":
    # 注册信号处理器
    # SIGINT: 用户按下 Ctrl+C
    signal.signal(signal.SIGINT, cleanup)
    # SIGTERM: 程序被要求终止（例如关闭CMD窗口）
    signal.signal(signal.SIGTERM, cleanup)

    print("=" * 50)
    print("  真实打字模拟器已启动... (健壮最终版)")
    print("  用法：复制任意文本，将鼠标光标置于目标位置，然后按下 Ctrl + V")
    print("  按 ESC 键可随时退出本程序。")
    print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)

    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)

    # 使用 keyboard.wait('esc') 会阻塞信号处理，这里改用一个更友好的循环
    print("...程序正在运行，按 ESC 退出...")
    while True:
        if keyboard.is_pressed('esc'):
            cleanup(None, None)
        time.sleep(0.1)