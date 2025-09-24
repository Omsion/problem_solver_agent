# -*- coding: utf-8 -*-
"""
silent_screencapper.py - 静默热键截图工具 (V1.0)

本文件是一个独立的后台应用程序，旨在提供一个通过全局热键进行
静默截图的功能，并与主Agent项目无缝集成。

核心功能:
1.  **后台运行**: 启动后，程序将持续在后台运行，等待热键事件。
2.  **全局热键**: 监听全局热键 `Ctrl+Shift+X`。无论当前焦点在哪个窗口，
    只要按下此组合键，即可触发截图。
3.  **完全静默**: 截图过程不会显示任何GUI、通知或播放任何声音。
4.  **自动保存**: 截图将以时间戳命名，并自动保存到主项目 `config.py`
    中定义的 `MONITOR_DIR` 目录中。
5.  **低资源占用**: 使用事件驱动的 `pynput` 库，CPU和内存占用极低。

使用方法:
1.  确保已安装所需库: pip install pynput pillow
2.  将此文件放置在主Agent项目的根目录下。
3.  在独立的终端中运行此脚本: python silent_screencapper.py
4.  脚本将保持运行。按下 Ctrl+Shift+X 进行截图。
5.  在终端中按 Ctrl+C 可以干净地停止此脚本。
"""

import time
from pathlib import Path
from pynput import keyboard
from PIL import ImageGrab

# 尝试从主项目中导入配置。
# 这种设计使得此工具可以作为独立脚本运行，同时又能与主项目共享配置。
try:
    import config

    # 从配置中获取截图保存的目标目录
    SAVE_DIRECTORY = config.MONITOR_DIR
    print(f"成功加载配置，截图将保存至: {SAVE_DIRECTORY}")
except (ImportError, AttributeError):
    # 如果导入失败或配置不完整，则使用一个安全的回退路径。
    SAVE_DIRECTORY = Path.home() / "silent_screenshots"
    print(f"警告: 无法从 'config.py' 加载配置。")
    print(f"将使用默认回退目录: {SAVE_DIRECTORY}")

# 定义要监听的热键组合
HOTKEY = {keyboard.Key.ctrl, keyboard.Key.shift, keyboard.KeyCode.from_char('x')}

# 一个集合，用于跟踪当前按下的键
current_keys = set()


def take_silent_screenshot():
    """
    执行核心的静默截图和保存操作。
    """
    try:
        # 1. 生成一个基于当前时间的、独一无二的文件名。
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"Screenshot_{timestamp}.png"

        # 2. 构造完整的文件保存路径。
        filepath = SAVE_DIRECTORY / filename

        # 3. 确保保存目录存在。
        #    `parents=True` 表示如果父目录不存在，也会一并创建。
        #    `exist_ok=True` 表示如果目录已存在，不会引发错误。
        SAVE_DIRECTORY.mkdir(parents=True, exist_ok=True)

        # 4. 调用Pillow的ImageGrab来捕获全屏。这是实现静默的关键。
        screenshot = ImageGrab.grab()

        # 5. 保存截图文件。
        screenshot.save(filepath, "PNG")

        # 6. 在控制台打印一条确认信息，以便于调试和确认操作成功。
        #    这是脚本在截图时唯一会产生的输出。
        print(f"截图成功! 已保存至: {filepath}")

    except Exception as e:
        # 捕获所有可能的异常（如权限问题、磁盘已满等），并打印错误信息。
        print(f"截图失败: {e}")


def on_key_press(key):
    """
    pynput 库的回调函数，在每次按键时被调用。
    """
    if key in HOTKEY:
        current_keys.add(key)
        # 检查当前按下的键是否构成了我们定义的热键组合
        if all(k in current_keys for k in HOTKEY):
            take_silent_screenshot()


def on_key_release(key):
    """
    pynput 库的回调函数，在每次按键释放时被调用。
    """
    try:
        current_keys.remove(key)
    except KeyError:
        pass  # 如果按键不在集合中，忽略错误


def main():
    """
    主执行函数：启动并管理热键监听器。
    """
    print("... 静默截图工具已启动 ...")
    print(f"[*] 正在监听热键: Ctrl + Shift + X")
    print("[*] 按下热键即可截图。")
    print("[*] 在此终端按 Ctrl + C 即可退出程序。")

    # 创建并启动键盘监听器。
    # on_press 和 on_release 是在发生相应事件时要调用的函数。
    with keyboard.Listener(on_press=on_key_press, on_release=on_key_release) as listener:
        # listener.join() 会阻塞主线程，使其持续运行以等待键盘事件。
        # 这是使脚本能够“一直在后台运行”的关键。
        listener.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n程序已通过 Ctrl+C 正常退出。")