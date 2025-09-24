# -*- coding: utf-8 -*-

"""
项目名称: 基于键盘布局的真实打字错误与纠正模拟器 (最终优化版)
描述:       该脚本在后台运行，监听 Ctrl+V 快捷键。
            通过多线程和清空剪贴板的策略，彻底解决了原生粘贴与模拟输入冲突的竞态条件问题，
            确保在任何编辑器中都能实现纯净、无错的模拟输入效果。
作者:       [Your Name/Agent]
依赖库:     keyboard, pyperclip
运行方式:   在管理员终端中执行 python human_typer.py
"""

import time
import random
import pyperclip
import keyboard
import threading  # 引入多线程库，解决竞态条件的关键

# ==============================================================================
# --- 全局配置参数 (可根据喜好调整) ---
# ==============================================================================

# --- 核心行为配置 ---
ERROR_RATE = 0.08  # 8% 的概率在输入每个字符时犯错
BACKSPACE_CHANCE = 0.85  # 85% 的概率在犯错后会“察觉”并立即修正

# --- 打字速度配置 (单位：秒) ---
MIN_TYPING_DELAY = 0.03  # 每个字符输入后的最小延迟
MAX_TYPING_DELAY = 0.12  # 每个字符输入后的最大延迟

# --- 节奏变化配置 ---
PAUSE_CHANCE = 0.04  # 4% 的概率在输入字符前进行一次“思考”停顿
MIN_PAUSE_DURATION = 0.5  # 最小“思考”停顿时长
MAX_PAUSE_DURATION = 1.2  # 最大“思考”停顿时长

REPEAT_KEY_CHANCE = 0.02  # 2% 的概率会重复输入一个按键 (例如 "helllo")
MIN_REPEAT_COUNT = 2  # 最小重复次数
MAX_REPEAT_COUNT = 3  # 最大重复次数

# ==============================================================================
# --- 键盘布局定义 (用于模拟真实错误) ---
# ==============================================================================

# 标准 QWERTY 键盘布局的邻近键位映射
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
    def __init__(self):
        pass

    def _type_char_with_delay(self, char: str):
        keyboard.write(char, delay=random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))

    def _press_backspace(self, count: int = 1):
        for _ in range(count):
            keyboard.send('backspace')
            time.sleep(random.uniform(0.05, 0.1))

    def simulate_typing(self, text_to_type: str):
        print("\n--- 开始模拟输入 ---")
        # 统一处理换行符，增强兼容性
        processed_text = text_to_type.replace('\r\n', '\n')

        for char in processed_text:
            if random.random() < PAUSE_CHANCE:
                pause_duration = random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION)
                print(f"[节奏: 停顿 {pause_duration:.2f} 秒]")
                time.sleep(pause_duration)

            if random.random() < REPEAT_KEY_CHANCE and char.isalnum():
                repeat_count = random.randint(MIN_REPEAT_COUNT, MAX_REPEAT_COUNT)
                print(f"[节奏: 重复按键 '{char}' {repeat_count} 次]")
                for _ in range(repeat_count):
                    keyboard.write(char)  # 在循环中直接写入，避免重复延迟
                time.sleep(random.uniform(0.1, 0.3))
                self._press_backspace(count=repeat_count - 1)

            elif char.lower() in KEYBOARD_LAYOUT and random.random() < ERROR_RATE:
                error_char = random.choice(KEYBOARD_LAYOUT[char.lower()])
                print(f"[错误: '{char}' -> '{error_char}']")
                self._type_char_with_delay(error_char)

                if random.random() < BACKSPACE_CHANCE:
                    time.sleep(random.uniform(0.1, 0.4))
                    print(f"[修正: 删除 '{error_char}' 并输入 '{char}']")
                    self._press_backspace()
                    self._type_char_with_delay(char)
                else:
                    print("[修正: 未察觉错误]")
            else:
                self._type_char_with_delay(char)

        print("\n--- 模拟输入完成 ---")


# ==============================================================================
# --- 核心触发逻辑 (多线程 + 剪贴板清除) ---
# ==============================================================================
is_simulation_running = False


def run_simulation_in_thread(content):
    """这个函数在独立的线程中运行，执行耗时的模拟任务"""
    global is_simulation_running
    try:
        # 【关键策略 B】立即清空剪贴板，让任何潜在的原生粘贴操作失效
        pyperclip.copy('')
        print("[策略] 剪贴板已清空，防止原生粘贴。")

        simulator = TypingSimulator()
        simulator.simulate_typing(content)
    except Exception as e:
        print(f"[线程错误] 在模拟过程中发生异常: {e}")
    finally:
        is_simulation_running = False  # 任务完成，释放锁


def trigger_simulation():
    """热键回调函数，必须极快地执行完毕"""
    global is_simulation_running
    if is_simulation_running:
        return

    is_simulation_running = True

    # 【关键策略 A】将耗时任务交给新线程，让回调函数立即返回
    try:
        # 1. 快速读取剪贴板
        clipboard_content = pyperclip.paste()
        if clipboard_content and isinstance(clipboard_content, str):
            # 2. 创建并启动一个新线程来处理模拟输入
            simulation_thread = threading.Thread(
                target=run_simulation_in_thread,
                args=(clipboard_content,)
            )
            simulation_thread.start()
        else:
            print("\n[信息] 剪贴板为空或内容非文本。")
            is_simulation_running = False  # 没有任务，立即释放锁

    except Exception as e:
        print(f"触发器错误: {e}")
        is_simulation_running = False  # 出现异常，释放锁


# ==============================================================================
# --- 主程序入口 ---
# ==============================================================================
if __name__ == "__main__":
    print("=" * 50)
    print("  真实打字模拟器已启动... (最终优化版)")
    print("  用法：复制任意文本，然后在需要输入的地方按下 Ctrl + V")
    print("  按 ESC 键可随时退出本程序。")
    print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)

    keyboard.add_hotkey('ctrl+v', trigger_simulation, suppress=True)
    keyboard.wait('esc')
    print("\n[退出] 按下 ESC，程序结束。")