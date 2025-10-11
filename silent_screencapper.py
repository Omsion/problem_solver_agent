# -*- coding: utf-8 -*-
r"""
silent_screencapper.py - 系统级热键重映射器 (V8.1 - 混合动力版)

本文件是一个独立的后台应用程序，其核心功能是将一个自定义热键映射为
系统级的 Win+PrtSc 按键事件。

V8.1 版本更新:
- 【核心重构】: 采用混合实现策略以解决顽固的热键注册失败问题。
  - 使用 `keyboard` 库进行热键的监听，该库使用键盘钩子，能更好地兼容
    被安全软件等限制的环境。
  - 保留使用 `ctypes` 和 `SendInput` API 来发送 `Win+PrtSc` 事件，
    确保模拟的按键具有最高的系统优先级。
- 【配置简化】: `config.py` 中的热键配置简化为直接的字符串（如 'alt+x'）。
- 【依赖恢复】: 重新引入 `keyboard` 库作为依赖。
conda activate llm && python D:\Users\wzw\Pictures\problem_solver_agent\silent_screencapper.py

"""

import time
import ctypes
from ctypes import wintypes
from pathlib import Path

# 重新引入 keyboard 库用于监听
try:
    import keyboard
except ImportError:
    print("错误: 缺少 'keyboard' 库。请以管理员身份运行: pip install keyboard")
    exit(1)

try:
    import win32con
except ImportError:
    print("错误: 缺少 'pywin32' 库。请运行: pip install pywin32")
    exit(1)

# --- 使用 ctypes 定义 SendInput 相关的 API 和结构体 ---
user32 = ctypes.windll.user32


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)))


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),)

    _anonymous_ = ("_input",)
    _fields_ = (("type", wintypes.DWORD),
                ("_input", _INPUT))


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)

# --- 配置加载 ---
try:
    import config

    SAVE_DIRECTORY = config.MONITOR_DIR
    print(f"配置加载成功，Agent正在监控目录: {SAVE_DIRECTORY}")
except (ImportError, AttributeError):
    print("警告: 无法加载主配置文件。")

# --- 从 config 文件读取热键定义 ---
try:
    HOTKEY_STRING = config.HOTKEY_CONFIG["STRING"]
    print(f"成功从 config.py 加载热键配置: {HOTKEY_STRING}")
except (ImportError, AttributeError, KeyError):
    print("警告: 无法从 config.py 加载热键配置，将使用默认值 'alt+x'。")
    HOTKEY_STRING = 'alt+x'


# --- 核心功能：模拟系统级 Win + PrtSc 按键 (此函数不变) ---
def press_win_prtscr():
    """使用 SendInput API 模拟一次 'Win + PrintScreen' 组合键。"""
    print(f"\n热键 '{HOTKEY_STRING}' 触发: 正在模拟按下 Win + PrtSc...")
    VK_LWIN = 0x5B
    VK_SNAPSHOT = 0x2C
    events = (
        INPUT(type=win32con.INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_LWIN, dwFlags=0)),
        INPUT(type=win32con.INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_SNAPSHOT, dwFlags=0)),
        INPUT(type=win32con.INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_SNAPSHOT, dwFlags=win32con.KEYEVENTF_KEYUP)),
        INPUT(type=win32con.INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_LWIN, dwFlags=win32con.KEYEVENTF_KEYUP))
    )
    input_array = (INPUT * len(events))(*events)
    sent_events = user32.SendInput(len(events), input_array, ctypes.sizeof(INPUT))
    if sent_events == len(events):
        print("  => 系统级 Win + PrtSc 事件已成功发送。")
    else:
        print("  => 警告: 模拟按键事件发送不完整。")


def main():
    """主执行函数：使用 keyboard 库注册热键并启动监听。"""
    try:
        # ---【核心修改点：移除 suppress=True】---
        # 我们不再抑制原始热键，以避免与 SendInput 的底层冲突，
        # 从而确保模拟按键能够100%成功注入。
        keyboard.add_hotkey(HOTKEY_STRING, press_win_prtscr)

        print("\n... 系统级热键重映射器已启动 ...")
        print(f"[*] 成功注册监听热键: {HOTKEY_STRING.upper()}")
        print(f"[*] 按下此热键将触发一次系统级的 'Win + PrtSc' 截图。")
        print("[*] 在此终端按 'ESC' 键可随时退出程序。")
        print("[!] 重要提示: 为确保热键在所有应用中生效，请务必以【管理员身份】运行此脚本！")

        # keyboard.wait() 会阻塞程序，直到按下 'esc' 键
        keyboard.wait('esc')

    except Exception as e:
        print(f"程序运行时发生错误: {e}")
        print("请确保：1. 以管理员身份运行。 2. 检查热键字符串是否正确。")
    finally:
        print("\n正在关闭并清理资源...")
        keyboard.unhook_all()
        print("所有热键已注销，程序已退出。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # 在 finally 块中处理清理，所以这里 pass 即可
        pass