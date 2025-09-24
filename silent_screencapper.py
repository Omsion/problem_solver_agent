# -*- coding: utf-8 -*-
"""
silent_screencapper.py - 静默热键截图工具 (V3.0 - 窗口感知版)

本文件是一个独立的后台应用程序，旨在提供一个通过全局热键进行
智能、静默截图的功能，并与主Agent项目无缝集成。

V3.0 版本更新:
- 【核心功能】: 新增窗口感知能力。现在截图时会自动识别鼠标光标
  所在的窗口，并只截取该窗口的内容。
- 【健壮性】: 如果鼠标不在任何窗口上（例如在桌面），程序会自动回退
  到截取整个屏幕，确保总能捕获到内容。
- 【依赖变更】: 引入了 `pywin32` 库来实现与 Windows API 的交互。
- 保持使用 `keyboard` 库以确保在现代终端中的兼容性。
"""

import time
from pathlib import Path
import keyboard
from PIL import ImageGrab

# 【新增】: 导入 pywin32 库用于与 Windows API 交互
# pywintypes 用于捕获特定的API错误
try:
    import win32gui
    import pywintypes
except ImportError:
    print("错误: 缺少 'pywin32' 库。")
    print("请在您的终端中运行: pip install pywin32")
    exit(1)

# 尝试从主项目中导入配置，实现无缝集成。
try:
    import config

    SAVE_DIRECTORY = config.MONITOR_DIR
    print(f"成功加载配置，截图将保存至: {SAVE_DIRECTORY}")
except (ImportError, AttributeError):
    # 如果主项目配置不存在，则提供一个安全的回退方案。
    SAVE_DIRECTORY = Path.home() / "silent_screenshots"
    print(f"警告: 无法从 'config.py' 加载配置。")
    print(f"将使用默认回退目录: {SAVE_DIRECTORY}")

# 定义热键字符串
HOTKEY_STRING = 'alt+x'


def take_silent_screenshot():
    """
    执行核心的静默截图和保存操作。
    此版本会自动检测鼠标下的窗口并仅截取该窗口。
    """
    try:
        bbox = None  # 初始化边界框为 None

        # --- 步骤 1: 使用 Windows API 获取窗口边界 ---
        try:
            # 获取鼠标当前位置的 (x, y) 坐标
            pos = win32gui.GetCursorPos()
            # 根据坐标获取窗口句柄 (hwnd)
            hwnd = win32gui.WindowFromPoint(pos)

            # 如果 hwnd 不为 0 (表示找到了一个窗口)
            if hwnd:
                # 获取该窗口的边界矩形 (left, top, right, bottom)
                bbox = win32gui.GetWindowRect(hwnd)
                print(f"检测到窗口句柄 {hwnd}，将在边界 {bbox} 内截图。")
            else:
                # 如果鼠标不在任何窗口上，则回退到全屏截图
                print("未检测到窗口，将进行全屏截图。")
        except pywintypes.error as e:
            # 捕获可能发生的API错误 (例如，窗口在获取句柄和截图之间被关闭)
            print(f"Windows API 错误: {e} - 回退到全屏截图。")
            bbox = None

        # --- 步骤 2: 使用 Pillow 进行截图 ---
        # 如果 bbox 有效，则只截取该区域；否则，grab() 默认截取全屏。
        screenshot = ImageGrab.grab(bbox=bbox, all_screens=True)

        # --- 步骤 3: 保存文件 ---
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        microsecond = f"{time.time():.6f}"[-6:]
        filename = f"Screenshot_{timestamp}_{microsecond}.png"
        filepath = SAVE_DIRECTORY / filename

        SAVE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        screenshot.save(filepath, "PNG")

        print(f"热键触发! 截图已保存至: {filepath}")

    except Exception as e:
        print(f"截图失败: {e}")


def main():
    """
    主执行函数：注册热键并启动监听。
    """
    print("... 静默截图工具已启动 ...")

    try:
        keyboard.add_hotkey(HOTKEY_STRING, take_silent_screenshot)

        print(f"[*] 成功注册热键: {HOTKEY_STRING.upper()}")
        print("[*] 将鼠标悬停在目标窗口上，然后按下热键即可截图。")
        print("[*] 在此终端按 Ctrl + C 即可退出程序。")

        keyboard.wait()

    except Exception as e:
        print(f"注册热键或启动监听时发生错误: {e}")
        print("请确保您拥有足够的权限，或尝试以管理员身份运行此脚本。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n程序已通过 Ctrl+C 正常退出。")