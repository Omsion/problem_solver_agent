# -*- coding: utf-8 -*-

"""
项目名称: 基于键盘布局的真实打字错误与纠正模拟器
描述:       该脚本在后台运行，监听 Ctrl+V 快捷键。
            触发后，它会获取剪贴板内容，并以模拟真人的方式逐字输入，
            包括打字速度波动、基于QWERTY键盘布局的随机错误、停顿以及错误纠正。
作者:       [Your Name/Agent]
依赖库:     pynput, pyperclip
运行方式:   在 PowerShell 或其他终端中执行 python human_typer.py
"""

import time
import random
import pyperclip
from pynput import keyboard

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
# 只定义了小写字母和数字，其他字符（如空格、标点）将不会触发临近键错误
KEYBOARD_LAYOUT = {
    '1': ['2', 'q'], '2': ['1', 'w', '3'], '3': ['2', 'w', 'e', '4'],
    '4': ['3', 'e', 'r', '5'], '5': ['4', 'r', 't', '6'], '6': ['5', 't', 'y', '7'],
    '7': ['6', 'y', 'u', '8'], '8': ['7', 'u', 'i', '9'], '9': ['8', 'i', 'o', '0'],
    '0': ['9', 'o', 'p', '-'],
    'q': ['w', 'a', 's'], 'w': ['q', 'a', 's', 'd', 'e'], 'e': ['w', 's', 'd', 'f', 'r'],
    'r': ['e', 'd', 'f', 'g', 't'], 't': ['r', 'f', 'g', 'h', 'y'], 'y': ['t', 'g', 'h', 'j', 'u'],
    'u': ['y', 'h', 'j', 'k', 'i'], 'i': ['u', 'j', 'k', 'l', 'o'], 'o': ['i', 'k', 'l', 'p'],
    'p': ['o', 'l', ';', '['],
    'a': ['q', 'w', 's', 'z', 'x'], 's': ['a', 'w', 'e', 'd', 'z', 'x', 'c'], 'd': ['s', 'e', 'r', 'f', 'x', 'c', 'v'],
    'f': ['d', 'r', 't', 'g', 'c', 'v', 'b'], 'g': ['f', 't', 'y', 'h', 'v', 'b', 'n'],
    'h': ['g', 'y', 'u', 'j', 'b', 'n', 'm'],
    'j': ['h', 'u', 'i', 'k', 'n', 'm'], 'k': ['j', 'i', 'o', 'l', 'm', ','], 'l': ['k', 'o', 'p', ';', ','],
    'z': ['a', 's', 'x'], 'x': ['z', 's', 'd', 'c'], 'c': ['x', 'd', 'f', 'v'],
    'v': ['c', 'f', 'g', 'b'], 'b': ['v', 'g', 'h', 'n'], 'n': ['b', 'h', 'j', 'm'],
    'm': ['n', 'j', 'k', ',']
}


# ==============================================================================
# --- 核心模拟器类 ---
# ==============================================================================

class TypingSimulator:
    def __init__(self):
        """初始化键盘控制器。"""
        self.keyboard_controller = keyboard.Controller()

    def _type_char_with_delay(self, char):
        """模拟单个字符的按下和释放，并在之后加入随机延迟。"""
        self.keyboard_controller.press(char)
        self.keyboard_controller.release(char)
        time.sleep(random.uniform(MIN_TYPING_DELAY, MAX_TYPING_DELAY))

    def _press_backspace(self, count=1):
        """模拟按下退格键。"""
        for _ in range(count):
            self.keyboard_controller.press(keyboard.Key.backspace)
            self.keyboard_controller.release(keyboard.Key.backspace)
            time.sleep(random.uniform(0.05, 0.1))  # 模拟修正时的轻微延迟

    def simulate_typing(self, text_to_type: str):
        """
        主模拟函数，负责处理完整的文本输入模拟。
        """
        print("\n--- 开始模拟输入 ---")
        for char in text_to_type:
            # 1. 模拟节奏变化：随机停顿
            if random.random() < PAUSE_CHANCE:
                pause_duration = random.uniform(MIN_PAUSE_DURATION, MAX_PAUSE_DURATION)
                print(f"[节奏: 停顿 {pause_duration:.2f} 秒]")
                time.sleep(pause_duration)

            # 2. 模拟节奏变化：重复按键
            if random.random() < REPEAT_KEY_CHANCE and char.isalnum():
                repeat_count = random.randint(MIN_REPEAT_COUNT, MAX_REPEAT_COUNT)
                print(f"[节奏: 重复按键 '{char}' {repeat_count} 次]")
                for _ in range(repeat_count):
                    self._type_char_with_delay(char)
                # 重复后进行修正
                time.sleep(random.uniform(0.1, 0.3))  # “反应过来”的延迟
                self._press_backspace(count=repeat_count - 1)

            # 3. 模拟基于键盘布局的错误输入
            if char.lower() in KEYBOARD_LAYOUT and random.random() < ERROR_RATE:
                neighbors = KEYBOARD_LAYOUT[char.lower()]
                error_char = random.choice(neighbors)
                print(f"[错误: '{char}' -> '{error_char}']")
                self._type_char_with_delay(error_char)

                # 4. 模拟退格修正
                if random.random() < BACKSPACE_CHANCE:
                    time.sleep(random.uniform(0.1, 0.4))  # “意识到错误”的延迟
                    print(f"[修正: 删除 '{error_char}' 并输入 '{char}']")
                    self._press_backspace()
                    self._type_char_with_delay(char)  # 输入正确的字符
                else:
                    print("[修正: 未察觉错误]")  # 错误被保留，不进行修正
            else:
                # 5. 正常输入
                self._type_char_with_delay(char)

        print("\n--- 模拟输入完成 ---")


# ==============================================================================
# --- 全局键盘监听器 ---
# ==============================================================================

current_pressed_keys = set()
is_simulation_running = False


def on_press(key):
    global is_simulation_running

    if is_simulation_running:
        return

    try:
        if key.char == 'v':
            current_pressed_keys.add('v')
    except AttributeError:
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            current_pressed_keys.add(keyboard.Key.ctrl_l)

    if 'v' in current_pressed_keys and keyboard.Key.ctrl_l in current_pressed_keys:
        is_simulation_running = True

        try:
            clipboard_content = pyperclip.paste()
            if clipboard_content and isinstance(clipboard_content, str):
                print(f"\n[触发] 检测到 Ctrl+V, 准备模拟输入...")

                # 【关键改动】在开始模拟前，先释放按键，防止它们被“卡住”
                current_pressed_keys.clear()

                simulator = TypingSimulator()
                simulator.simulate_typing(clipboard_content)
            else:
                print("\n[信息] 剪贴板为空或内容非文本。")

        except Exception as e:
            print(f"[错误] 在模拟过程中发生异常: {e}")

        finally:
            is_simulation_running = False

        # 【关键改动】返回 False 来阻止原始的 Ctrl+V 事件继续传播
        # 这可以防止系统执行默认的粘贴操作。
        return False


def on_release(key):
    try:
        if key.char == 'v':
            current_pressed_keys.discard('v')
    except AttributeError:
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            current_pressed_keys.discard(keyboard.Key.ctrl_l)

    if key == keyboard.Key.esc:
        print("\n[退出] 按下 ESC，程序结束。")
        return False


# ==============================================================================
# --- 主程序入口 ---
# ==============================================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  真实打字模拟器已启动...")
    print("  用法：复制任意文本，然后在需要输入的地方按下 Ctrl + V")
    print("  按 ESC 键可随时退出本程序。")
    print("  (请确保本终端以管理员身份运行)")
    print("=" * 50)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()