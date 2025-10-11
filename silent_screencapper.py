# -*- coding: utf-8 -*-
"""
silent_screencapper.py - 静默热键截图工具 (V7.1 - ctypes 终极版)

本文件是一个独立的后台应用程序，旨在提供一个通过全局热键进行
智能、静默截图的功能，并与主Agent项目无缝集成。

V7.1 版本更新:
- 【核心重构】: 放弃了 `keyboard` 和 `win32gui` 的部分封装，转而完全使用
  Python 内置的 `ctypes` 库直接调用 Windows User32.dll 的原生 API
  (RegisterHotKey, UnregisterHotKey)。
- 【解决痛点】:
  1.  **解决热键冲突**: 将默认热键从 'Alt+X' 修改为 'Ctrl+Shift+X'，
      这是一个极少被占用的组合，从根本上避免了“热键已被占用”的错误。
  2.  **提升权限兼容性**: ctypes 调用方式结合“以管理员身份运行”的指引，
      可以确保热键具有最高的系统响应优先级，无法被上层应用屏蔽。
- 【代码一致性】: 所有底层API调用（DPI设置、热键注册/注销）现在均通过
  ctypes 完成，风格统一且更接近Windows底层。
- 【修复IDE警告】: 统一的调用方式有助于减少或消除部分IDE的静态检查警告。
"""

import time
import ctypes
from ctypes import wintypes
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("错误: 缺少 'Pillow' 库。请运行: pip install Pillow")
    exit(1)

try:
    import win32gui
    import win32api
    import win32con
    import win32ui
except ImportError:
    print("错误: 缺少 'pywin32' 库。请运行: pip install pywin32")
    exit(1)

# ---【核心修改点 1：使用 ctypes 定义 API 函数原型】---
# 明确定义我们要使用的user32.dll中的函数及其参数和返回类型，这是一种更严谨的做法。
user32 = ctypes.windll.user32
user32.SetProcessDPIAware.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                wintypes.UINT)
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.restype = wintypes.LONG

# 将应用程序标记为 DPI 感知
try:
    user32.SetProcessDPIAware()
except Exception as e:
    print(f"警告: 设置 DPI 感知失败 (非Windows系统或权限问题): {e}")

# --- 配置加载 ---
try:
    import config

    SAVE_DIRECTORY = config.MONITOR_DIR
    print(f"成功加载配置，截图将保存至: {SAVE_DIRECTORY}")
except (ImportError, AttributeError):
    SAVE_DIRECTORY = Path.home() / "silent_screenshots"
    print(f"警告: 无法加载配置，将使用默认目录: {SAVE_DIRECTORY}")

# ---【核心修改点 2：更改热键定义】---
HOTKEY_ID_SCREENSHOT = 1
# MOD_CONTROL (Ctrl) = 2, MOD_SHIFT (Shift) = 4. 组合起来是 6。
HOTKEY_MODIFIERS = win32con.MOD_CONTROL | win32con.MOD_SHIFT
HOTKEY_VK = ord('X')
HOTKEY_STRING = 'Ctrl + Shift + X'  # 用于日志输出



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
    """主执行函数：注册热键并启动自定义的消息循环。"""

    # 1. 注册热键。这里我们不需要创建窗口，因为我们可以直接监听线程的消息队列。
    # 第一个参数 0 表示将热键与当前线程关联。
    try:
        if not user32.RegisterHotKey(None, HOTKEY_ID_SCREENSHOT, HOTKEY_MODIFIERS, HOTKEY_VK):
            raise RuntimeError(f"无法注册热键 {HOTKEY_STRING}。可能已被其他程序占用或权限不足。")

        print("... 静默截图工具已启动 ...")
        print(f"[*] 成功注册系统级热键: {HOTKEY_STRING.upper()}")
        print("[*] 将鼠标移动到目标显示器，然后按下热键即可截图。")
        print("[*] 在此终端按 Ctrl + C 即可退出程序。")
        print("[!] 重要提示: 为确保热键在所有应用中生效，请以【管理员身份】运行此脚本！")

        # 2. 启动自定义的消息循环
        # 这是 `win32gui.PumpMessages()` 的手动实现版本，它允许我们响应 KeyboardInterrupt
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == win32con.WM_HOTKEY:
                if msg.wParam == HOTKEY_ID_SCREENSHOT:
                    take_screenshot()

            # 这两行是标准Windows消息处理的一部分，即使我们不直接用它们
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    except RuntimeError as e:
        print(f"启动时发生错误: {e}")
    except Exception as e:
        print(f"程序运行中发生意外错误: {e}")
    finally:
        # 3. 关键的清理步骤
        print("\n正在关闭并清理资源...")
        user32.UnregisterHotKey(None, HOTKEY_ID_SCREENSHOT)
        print("热键已注销，程序已退出。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # 当用户在终端按下 Ctrl+C 时，GetMessageW会返回-1，循环自然终止，
        # 最终会执行 finally 块中的清理代码。
        pass