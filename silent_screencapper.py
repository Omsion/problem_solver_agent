# -*- coding: utf-8 -*-
"""
silent_screencapper.py - 静默热键截图工具 (V6.1 - 终极 GDI 对象版)

本文件是一个独立的后台应用程序，旨在提供一个通过全局热键进行
智能、静默截图的功能，并与主Agent项目无缝集成。

V6.1 版本更新:
- 【核心修复】: 修复了因混用 GDI 句柄 (handle) 和 GDI 对象 (object)
  导致的 `AttributeError`。现在代码完全使用 `win32ui` 提供的面向
  对象的封装（如 PyCDC, PyCBitmap），确保了 GDI 操作的正确性和健壮性。
- 这从根本上解决了截图失败的问题，使多显示器截图功能完美运行。
"""

import time
import ctypes
from pathlib import Path
import keyboard

try:
    from PIL import Image
except ImportError:
    print("错误: 缺少 'Pillow' 库。")
    print("请在您的终端中运行: pip install Pillow")
    exit(1)

try:
    import win32gui
    import win32api
    import win32con
    import win32ui  # 导入 GDI 对象封装模块
except ImportError:
    print("错误: 缺少 'pywin32' 库。")
    print("请在您的终端中运行: pip install pywin32")
    exit(1)

# 将应用程序标记为 DPI 感知
try:
    ctypes.windll.user32.SetProcessDPIAware()
except AttributeError:
    print("非 Windows 系统，跳过 DPI 设置。")
    pass

# --- 配置加载 ---
try:
    import config

    SAVE_DIRECTORY = config.MONITOR_DIR
    print(f"成功加载配置，截图将保存至: {SAVE_DIRECTORY}")
except (ImportError, AttributeError):
    SAVE_DIRECTORY = Path.home() / "silent_screenshots"
    print(f"警告: 无法加载配置，将使用默认目录: {SAVE_DIRECTORY}")

# --- 热键定义 ---
HOTKEY_STRING = 'alt+x'


def take_screenshot():
    """
    执行核心的、针对【当前显示器】的静默截图和保存操作。
    此版本使用 `win32ui` 对象，确保 GDI 操作的正确性。
    """
    print(f"\n热键 '{HOTKEY_STRING}' 触发: 尝试截取当前显示器...")

    # 初始化所有 GDI 对象为 None，以便在 finally 中安全地清理
    desktop_dc = None
    mem_dc = None
    bitmap = None

    try:
        # 1. 获取目标显示器的边界
        pos = win32gui.GetCursorPos()
        monitor_handle = win32api.MonitorFromPoint(pos, win32con.MONITOR_DEFAULTTONEAREST)
        monitor_info = win32api.GetMonitorInfo(monitor_handle)
        left, top, right, bottom = monitor_info['Monitor']
        width = right - left
        height = bottom - top

        print(f"  -> 检测到显示器，边界: {(left, top, right, bottom)}")

        # 2. 【核心修复】: 创建 GDI 对象，而不是原始句柄
        # 获取桌面窗口的设备上下文句柄
        h_desktop_dc = win32gui.GetWindowDC(win32gui.GetDesktopWindow())
        # 从句柄创建 PyCDC 对象
        desktop_dc = win32ui.CreateDCFromHandle(h_desktop_dc)
        # 创建一个与桌面DC兼容的内存DC对象
        mem_dc = desktop_dc.CreateCompatibleDC()

        # 创建一个空的、与桌面DC兼容的位图对象 (PyCBitmap)
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(desktop_dc, width, height)

        # 3. 将位图选入内存DC，作为绘图的目标
        mem_dc.SelectObject(bitmap)

        # 4. 执行 BitBlt (Bit Block Transfer) 操作
        # 使用 PyCDC 对象的 BitBlt 方法，语法更清晰
        mem_dc.BitBlt((0, 0), (width, height), desktop_dc, (left, top), win32con.SRCCOPY)

        # 5. 从 PyCBitmap 对象获取像素数据
        # 现在 bitmap 是一个完整的对象，可以调用 GetBitmapBits 方法
        signed_ints_array = bitmap.GetBitmapBits(True)

        # 6. 将 GDI 位图数据转换为 Pillow Image 对象
        img = Image.frombuffer('RGB', (width, height), signed_ints_array, 'raw', 'BGRX', 0, 1)

        # 7. 生成唯一文件名并保存
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        microsecond = f"{time.time():.6f}"[-6:]
        filename = f"Screenshot_{timestamp}_{microsecond}.png"
        filepath = SAVE_DIRECTORY / filename

        SAVE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        img.save(filepath, "PNG")

        print(f"  => 截图成功! 已保存至: {filepath}")

    except Exception as e:
        print(f"  => 截图失败: {e}")
    finally:
        # 8. 【关键】: 安全、正确地清理所有 GDI 对象
        # win32ui 对象通常有自己的清理方法或应通过 DeleteObject 删除
        if bitmap:
            # 【核心修复】: 正确调用 DeleteObject，需要传递句柄
            win32gui.DeleteObject(bitmap.GetHandle())
        if mem_dc:
            mem_dc.DeleteDC()
        if desktop_dc:
            desktop_dc.DeleteDC()


def main():
    """主执行函数：注册热键并启动监听。"""
    print("... 静默截图工具已启动 ...")
    try:
        keyboard.add_hotkey(HOTKEY_STRING, take_screenshot)
        print(f"[*] 成功注册热键: {HOTKEY_STRING.upper()}")
        print("[*] 将鼠标移动到目标显示器，然后按下热键即可截图。")
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