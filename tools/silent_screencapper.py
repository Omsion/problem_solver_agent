# -*- coding: utf-8 -*-
r"""
“高级系统设置”，打开“系统属性”窗口,关闭“在最大化和最小化时显示窗口动画”选项：关闭 Win+PrtSc 的原生闪屏特效！！！

或者运行以下silent_screencapper.py程序：
silent_screencapper.py - 静默热键截图工具 (V9.1 - 终极注释版)

本文件通过将可靠的热键监听与直接的GDI屏幕复制功能在独立的线程中
结合，实现了在高限制性环境下（如在线考试客户端）的静默截图。

V9.1 版本更新:
- 【最终注释】: 对代码进行了最终的注释和文档重构，清晰地阐述了为何
  "直接GDI截图" 是比 "模拟Win+PrtSc" 更优越且能满足所有需求的终极方案。

---------------------------------------------------------------------------
最终设计思想解析：
本方案的核心是“动作替代”而非“事件模拟”。

1.  **监听 (The Ear)**: 使用 `keyboard` 库的钩子监听自定义热键。
    实践证明，这是在用户复杂环境下最可靠的事件触发方式。

2.  **解耦 (The Brain)**: 使用 `threading.Thread` 将耗时的截图动作
    放入一个独立的后台线程。这打破了在键盘钩子回调函数内部直接执行
    重度任务的限制，避免了所有潜在的底层输入流冲突，是程序健壮性的关键。

3.  **动作 (The Hand)**: 使用 `pywin32` 和 `Pillow` 直接调用GDI的
    `BitBlt` 函数复制屏幕像素。这相当于程序自己扮演了操作系统的角色，
    直接完成了截图的“动作”。

这个流程完美达成了所有目标：
- **有效性**: `keyboard` 钩子能穿透限制。
- **静默性**: `BitBlt` 是内存操作，没有任何UI特效（如闪屏）。
- **结果一致性**: 截图文件被保存到主Agent监控的同一目录下，
  完美复现了 `Win+PrtSc` 的最终效果。
---------------------------------------------------------------------------
conda activate llm && python D:\Users\wzw\Pictures\problem_solver_agent\silent_screencapper.py

"""

import time
import ctypes
from pathlib import Path
import threading
import sys

# 将项目根目录（tools/的上级目录）添加到Python的模块搜索路径
sys.path.append(str(Path(__file__).resolve().parents[1]))

from PIL import Image
import keyboard
import win32gui
import win32api
import win32con
import win32ui

# --- DPI 感知设置 ---
ctypes.windll.user32.SetProcessDPIAware()

# --- 配置加载 ---
try:
    from problem_solver_agent import config
    SAVE_DIRECTORY = config.MONITOR_DIR
    print(f"配置加载成功，截图将保存至: {SAVE_DIRECTORY}")
except (ImportError, AttributeError):
    SAVE_DIRECTORY = Path.home() / "silent_screenshots"
    print(f"警告: 无法加载主配置文件，将使用默认目录: {SAVE_DIRECTORY}")

try:
    # config 已经在上面导入了，这里可以直接使用
    HOTKEY_STRING = config.HOTKEY_CONFIG["STRING"]
    print(f"成功从 config.py 加载热键配置: {HOTKEY_STRING}")
except (NameError, AttributeError, KeyError): # NameError 是因为如果上面导入失败，config就不存在
    print("警告: 无法从 config.py 加载热键配置，将使用默认值 'alt+x'。")
    HOTKEY_STRING = 'alt+x'

# --- 核心逻辑 ---
is_capturing = False  # 用于防止快速连按时产生多个截图线程的锁

def take_screenshot_action():
    """
    截图的实际执行者。它不模拟任何按键，而是直接复制屏幕像素。
    """
    global is_capturing
    print(f"\n热键 '{HOTKEY_STRING}' 触发: 正在执行GDI屏幕捕获...")
    desktop_dc, mem_dc, bitmap = None, None, None
    try:
        # 1. 定位鼠标所在的屏幕及其尺寸
        pos = win32gui.GetCursorPos()
        monitor_handle = win32api.MonitorFromPoint(pos, win32con.MONITOR_DEFAULTTONEAREST)
        monitor_info = win32api.GetMonitorInfo(monitor_handle)
        left, top, right, bottom = monitor_info['Monitor']
        width, height = right - left, bottom - top

        # 2. 准备内存中的“画布”
        h_desktop_dc = win32gui.GetWindowDC(win32gui.GetDesktopWindow())
        desktop_dc = win32ui.CreateDCFromHandle(h_desktop_dc)
        mem_dc = desktop_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(desktop_dc, width, height)
        mem_dc.SelectObject(bitmap)

        # 3. 执行截图核心操作：将屏幕像素块传输到内存画布上
        mem_dc.BitBlt((0, 0), (width, height), desktop_dc, (left, top), win32con.SRCCOPY)

        # 4. 将内存中的像素数据转换为图片文件
        signed_ints_array = bitmap.GetBitmapBits(True)
        img = Image.frombuffer('RGB', (width, height), signed_ints_array, 'raw', 'BGRX', 0, 1)

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        # microsecond = f"{time.time():.6f}"[-6:]
        # filename = f"Screenshot_{timestamp}_{microsecond}.png"
        filename = f"Screenshot_{timestamp}.png"
        filepath = SAVE_DIRECTORY / filename
        SAVE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        img.save(filepath, "PNG")

        print(f"  => 截图成功! 已保存至: {filepath}")

    except Exception as e:
        print(f"  => 截图失败: {e}")
    finally:
        # 5. 清理GDI资源，防止内存泄漏
        if bitmap:
            try: win32gui.DeleteObject(bitmap.GetHandle())
            except: pass
        if mem_dc:
            try: mem_dc.DeleteDC()
            except: pass
        if desktop_dc:
            try: desktop_dc.DeleteDC()
            except: pass
        # 释放锁，允许下一次截图
        is_capturing = False

def trigger_screenshot_thread():
    """热键回调函数，仅负责启动截图工作线程，实现解耦。"""
    global is_capturing
    if not is_capturing:
        is_capturing = True
        worker_thread = threading.Thread(target=take_screenshot_action, daemon=True)
        worker_thread.start()

def main():
    """主执行函数：使用 keyboard 库注册热键并启动监听。"""
    try:
        keyboard.add_hotkey(HOTKEY_STRING, trigger_screenshot_thread)

        print("\n... 静默截图工具已启动 ...")
        print(f"[*] 成功注册监听热键: {HOTKEY_STRING.upper()}")
        print(f"[*] 按下此热键将直接捕获屏幕并保存。")
        print("[*] 程序正在后台运行，请在本终端按 'Ctrl+C' 来终止程序。")
        print("[!] 重要提示: 为确保热键在所有应用中生效，请务必以【管理员身份】运行此脚本！")

        while True:
            time.sleep(1)

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