# -*- coding: utf-8 -*-
r"""
silent_screencapper.py - 静默热键截图工具 (V9.0 - GDI截图+线程解耦最终版)

本文件回归截图本质，通过将可靠的热键监听与可靠的GDI屏幕复制功能
在独立的线程中结合，以应对最复杂的系统环境。

V9.0 版本更新:
- 【核心重构】: 彻底放弃了模拟 Win+PrtSc 的方案。
  - 使用 `keyboard` 库进行热键监听，因为它已被证明在用户环境中可以稳定触发。
  - 恢复使用 `pywin32` 和 `Pillow` 直接进行GDI屏幕像素复制，这是最直接的截图方式。
- 【解决痛点】:
  1.  **线程解耦**: 将GDI截图这一耗时操作放在一个独立的后台线程中执行，
      避免了阻塞键盘钩子，解决了所有底层冲突。
  2.  **绕过限制**: 此方案不再依赖 `RegisterHotKey` 或 `SendInput`，
      而是直接读取屏幕缓冲区，极大可能绕过考试客户端对“事件”的限制。
- 【依赖恢复】: 重新引入 `Pillow` 库作为截图的必要依赖。

conda activate llm && python D:\Users\wzw\Pictures\problem_solver_agent\silent_screencapper.py

"""

import time
import ctypes
from pathlib import Path
import threading

try:
    from PIL import Image
except ImportError:
    print("错误: 缺少 'Pillow' 库。请以管理员身份运行: pip install Pillow")
    exit(1)

try:
    import keyboard
except ImportError:
    print("错误: 缺少 'keyboard' 库。请以管理员身份运行: pip install keyboard")
    exit(1)

try:
    import win32gui
    import win32api
    import win32con
    import win32ui
except ImportError:
    print("错误: 缺少 'pywin32' 库。请运行: pip install pywin32")
    exit(1)

# --- DPI 感知设置 ---
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

# --- 配置加载 ---
try:
    import config

    SAVE_DIRECTORY = config.MONITOR_DIR
    print(f"配置加载成功，截图将保存至: {SAVE_DIRECTORY}")
except (ImportError, AttributeError):
    SAVE_DIRECTORY = Path.home() / "silent_screenshots"
    print(f"警告: 无法加载主配置文件，将使用默认目录: {SAVE_DIRECTORY}")

try:
    HOTKEY_STRING = config.HOTKEY_CONFIG["STRING"]
    print(f"成功从 config.py 加载热键配置: {HOTKEY_STRING}")
except (ImportError, AttributeError, KeyError):
    print("警告: 无法从 config.py 加载热键配置，将使用默认值 'alt+x'。")
    HOTKEY_STRING = 'alt+x'

# --- 核心逻辑 ---
is_capturing = False  # 用于防止快速连按时产生多个截图线程的锁


def take_screenshot_action():
    """
    实际执行GDI截图和保存的操作。此函数将在一个独立的线程中被调用。
    """
    global is_capturing
    print(f"\n热键 '{HOTKEY_STRING}' 触发: 正在执行GDI屏幕捕获...")

    desktop_dc, mem_dc, bitmap = None, None, None
    try:
        pos = win32gui.GetCursorPos()
        monitor_handle = win32api.MonitorFromPoint(pos, win32con.MONITOR_DEFAULTTONEAREST)
        monitor_info = win32api.GetMonitorInfo(monitor_handle)
        left, top, right, bottom = monitor_info['Monitor']
        width, height = right - left, bottom - top

        h_desktop_dc = win32gui.GetWindowDC(win32gui.GetDesktopWindow())
        desktop_dc = win32ui.CreateDCFromHandle(h_desktop_dc)
        mem_dc = desktop_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(desktop_dc, width, height)
        mem_dc.SelectObject(bitmap)

        mem_dc.BitBlt((0, 0), (width, height), desktop_dc, (left, top), win32con.SRCCOPY)

        signed_ints_array = bitmap.GetBitmapBits(True)
        img = Image.frombuffer('RGB', (width, height), signed_ints_array, 'raw', 'BGRX', 0, 1)

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        microsecond = f"{time.time():.6f}"[-6:]
        filename = f"Screenshot_{timestamp}.png"
        filepath = SAVE_DIRECTORY / filename
        SAVE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        img.save(filepath, "PNG")

        print(f"  => 截图成功! 已保存至: {filepath}")

    except Exception as e:
        print(f"  => 截图失败: {e}")
    finally:
        # 清理GDI资源
        if bitmap:
            try:
                win32gui.DeleteObject(bitmap.GetHandle())
            except:
                pass
        if mem_dc:
            try:
                mem_dc.DeleteDC()
            except:
                pass
        if desktop_dc:
            try:
                desktop_dc.DeleteDC()
            except:
                pass
        # 释放锁
        is_capturing = False


def trigger_screenshot_thread():
    """
    热键回调函数，仅负责启动截图工作线程。
    """
    global is_capturing
    if not is_capturing:
        is_capturing = True
        worker_thread = threading.Thread(target=take_screenshot_action, daemon=True)
        worker_thread.start()


def main():
    """主执行函数：使用 keyboard 库注册热键并启动监听。"""
    try:
        # 使用 keyboard.add_hotkey 监听，回调是我们的线程触发器
        keyboard.add_hotkey(HOTKEY_STRING, trigger_screenshot_thread)

        print("\n... 静默截图工具已启动 ...")
        print(f"[*] 成功注册监听热键: {HOTKEY_STRING.upper()}")
        print(f"[*] 按下此热键将直接捕获屏幕并保存。")
        print("[*] 在此终端按 'ESC' 键可随时退出程序。")
        print("[!] 重要提示: 为确保热键在所有应用中生效，请务必以【管理员身份】运行此脚本！")

        keyboard.wait('esc')

    except Exception as e:
        print(f"程序运行时发生错误: {e}")
    finally:
        print("\n正在关闭并清理资源...")
        keyboard.unhook_all()
        print("所有热键已注销，程序已退出。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass