# -*- coding: utf-8 -*-
r"""
remote_trigger.py - 网络遥控截图服务器 (V12.0 - 健壮配置加载版)

本文件作为一个微型Web服务器运行，允许通过手机等外部设备触发截图，
以此绕过PC上所有的键盘输入限制。

V12.0 版本更新:
- 【核心优化】: 重构了配置加载逻辑，不再使用宽泛的 `try-except` 块，
  而是改为对每一个必需的配置项进行独立、明确的检查。
- 【精准错误提示】: 当某个关键配置（如 `MONITOR_DIR` 或 `REMOTE_TRIGGER_PORT`）
  在 config.py 中缺失时，系统会打印出具体的错误信息，并优雅地回退到
  一个安全的默认值，而不是给出一个模糊的加载失败警告。
- 【代码清晰度】: 增加了详细的注释，解释了配置加载、检查、回退的每一步，
  使代码更易于理解和维护。

conda activate llm; cd "D:\Users\wzw\Pictures\OnlineTest"; python tools/remote_trigger.py
"""
import importlib
import time
import ctypes
from pathlib import Path
import threading
import socket
import sys

# 将项目根目录（tools/的上级目录）添加到Python的模块搜索路径
# 这样才能正确地找到 'problem_solver_agent' 这个包
sys.path.append(str(Path(__file__).resolve().parents[1]))

# --- 依赖库导入 ---
from flask import Flask, jsonify, render_template_string
import qrcode
from PIL import Image
import win32gui, win32api, win32con, win32ui

# --- DPI 感知设置 ---
ctypes.windll.user32.SetProcessDPIAware()

# ==============================================================================
# --- 【核心优化点】健壮的配置加载与验证 ---
# ==============================================================================
SAVE_DIRECTORY = None
PORT = None

try:
    from problem_solver_agent import config  # 现在可以这样导入了
    print("主配置文件 config.py 加载成功。")

    # 步骤 2: 【逐项检查】必需的配置，并提供清晰的回退逻辑
    # 检查截图保存目录
    if hasattr(config, 'MONITOR_DIR'):
        SAVE_DIRECTORY = config.MONITOR_DIR
        print(f"  ✓ 'MONITOR_DIR' 配置已加载，截图将保存至: {SAVE_DIRECTORY}")
    else:
        SAVE_DIRECTORY = Path.home() / "Pictures" / "Screenshots"
        print(f"  ✗ 警告: config.py 中缺少 'MONITOR_DIR' 配置。")
        print(f"  → 已回退至默认目录: {SAVE_DIRECTORY}")

    # 检查服务器端口
    if hasattr(config, 'REMOTE_TRIGGER_PORT'):
        PORT = config.REMOTE_TRIGGER_PORT
        print(f"  ✓ 'REMOTE_TRIGGER_PORT' 配置已加载，服务器端口为: {PORT}")
    else:
        PORT = 5555
        print(f"  ✗ 警告: config.py 中缺少 'REMOTE_TRIGGER_PORT' 配置。")
        print(f"  → 已回退至默认端口: {PORT}")

except Exception as e:
    # 如果在加载模块本身的过程中就发生严重错误，则全部使用默认值
    print(f"严重错误: 无法加载主配置文件 config.py。将全部使用默认值。错误: {e}")
    SAVE_DIRECTORY = Path.home() / "Pictures" / "Screenshots"
    PORT = 5555
    print(f"  → 已回退至默认目录: {SAVE_DIRECTORY}")
    print(f"  → 已回退至默认端口: {PORT}")

# --- Flask Web 服务器实例 ---
app = Flask(__name__)

# --- 核心截图逻辑 ---
is_capturing = False # 使用一个简单的锁，防止因快速连续点击导致并发截图

def take_screenshot_action():
    """
    截图动作的实际执行者。
    该函数通过调用Windows GDI API直接从屏幕缓冲区复制像素，实现完全静默的截图。
    """
    global is_capturing
    print(f"\n[{time.strftime('%H:%M:%S')}] 接收到网络请求: 正在执行GDI屏幕捕获...")
    desktop_dc, mem_dc, bitmap = None, None, None
    try:
        # 1. 获取鼠标当前所在的显示器信息，以支持多显示器环境
        pos = win32gui.GetCursorPos()
        monitor_handle = win32api.MonitorFromPoint(pos, win32con.MONITOR_DEFAULTTONEAREST)
        monitor_info = win32api.GetMonitorInfo(monitor_handle)
        left, top, right, bottom = monitor_info['Monitor']
        width, height = right - left, bottom - top

        # 2. 创建与屏幕兼容的设备上下文（DC）和位图对象，用于在内存中绘制图像
        h_desktop_dc = win32gui.GetWindowDC(win32gui.GetDesktopWindow())
        desktop_dc = win32ui.CreateDCFromHandle(h_desktop_dc)
        mem_dc = desktop_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(desktop_dc, width, height)
        mem_dc.SelectObject(bitmap)

        # 3. 核心操作：使用 BitBlt 函数将屏幕像素块传输（blitting）到内存位图中
        mem_dc.BitBlt((0, 0), (width, height), desktop_dc, (left, top), win32con.SRCCOPY)

        # 4. 从内存位图对象中提取像素数据，并使用Pillow库转换为图像对象
        signed_ints_array = bitmap.GetBitmapBits(True)
        img = Image.frombuffer('RGB', (width, height), signed_ints_array, 'raw', 'BGRX', 0, 1)

        # 5. 生成带微秒的时间戳以确保文件名唯一，并保存文件
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        microsecond = f"{time.time():.6f}"[-6:]
        filename = f"Screenshot_{timestamp}_{microsecond}.png"
        SAVE_DIRECTORY.mkdir(parents=True, exist_ok=True) # 确保目录存在
        filepath = SAVE_DIRECTORY / filename
        img.save(filepath, "PNG")
        print(f"  => 截图成功! 已保存至: {filepath}")

    except Exception as e:
        print(f"  => 截图失败: {e}")
    finally:
        # 6. 关键步骤：无论成功与否，都必须清理所有GDI对象，防止内存泄漏
        if bitmap:
            try: win32gui.DeleteObject(bitmap.GetHandle())
            except: pass
        if mem_dc:
            try: mem_dc.DeleteDC()
            except: pass
        if desktop_dc:
            try: desktop_dc.DeleteDC()
            except: pass
        is_capturing = False # 释放锁

def trigger_screenshot_thread():
    """
    线程触发器。将耗时的截图操作放入后台线程，防止阻塞Web服务器的响应。
    这是确保遥控器界面在点击后能立即反馈的关键。
    """
    global is_capturing
    if not is_capturing:
        is_capturing = True
        worker_thread = threading.Thread(target=take_screenshot_action, daemon=True)
        worker_thread.start()
        return True
    return False

# --- Web 服务器路由 (API Endpoints) ---
# (前端HTML和后端API路由保持不变)
REMOTE_CONTROL_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>截图遥控器</title>
    <style>
        body, html { height: 100%; margin: 0; display: flex; justify-content: center; align-items: center; background-color: #282c34; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
        .button {
            display: flex; justify-content: center; align-items: center;
            width: 70vmin; height: 70vmin;
            background-color: #61afef; color: white;
            font-size: 10vmin; font-weight: bold; text-align: center;
            border-radius: 50%; user-select: none; cursor: pointer;
            box-shadow: 0 1vh 2vh rgba(0,0,0,0.25);
            transition: transform 0.1s ease, background-color 0.2s ease;
        }
        .button:active { transform: scale(0.95); background-color: #528bce; }
        .message { position: fixed; bottom: 5vh; color: #9da5b4; font-size: 4vmin; transition: opacity 1s ease; opacity: 0; padding: 1vh 3vh; background-color: rgba(0,0,0,0.3); border-radius: 2vh; }
    </style>
</head>
<body>
    <div class="button" onclick="triggerScreenshot()">截 图</div>
    <div id="message" class="message"></div>
    <script>
        function triggerScreenshot() {
            const msgDiv = document.getElementById('message');
            fetch('/trigger-screenshot', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    msgDiv.textContent = data.message;
                    msgDiv.style.opacity = 1;
                    setTimeout(() => { msgDiv.style.opacity = 0; }, 2000);
                })
                .catch(err => {
                    msgDiv.textContent = '错误: 无法连接到PC';
                    msgDiv.style.opacity = 1;
                    setTimeout(() => { msgDiv.style.opacity = 0; }, 2000);
                });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    """提供遥控器页面。"""
    return render_template_string(REMOTE_CONTROL_HTML)

@app.route('/trigger-screenshot', methods=['POST'])
def trigger_api():
    """处理来自遥控器页面的截图请求。"""
    if is_capturing:
        return jsonify({"status": "busy", "message": "正在处理上一个截图..."}), 429
    if trigger_screenshot_thread():
        return jsonify({"status": "ok", "message": "截图指令已发送！"}), 200
    else:
        return jsonify({"status": "error", "message": "未知错误"}), 500

def get_local_ip():
    """获取本机在局域网中的IP地址，以便在终端显示。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 这个IP地址不需要是可达的，只是用来触发系统选择一个合适的网络接口
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def main():
    """主执行函数：启动Web服务器并显示访问信息及二维码。"""
    try:
        local_ip = get_local_ip()
        url = f"http://{local_ip}:{PORT}"
        print("\n... 远程截图服务器已启动 ...")
        print("=" * 50)
        print("[*] 请确保您的手机和电脑连接到【同一个WiFi网络】。")
        print(f"[*] 在您手机的浏览器中访问以下地址，或直接扫描下方二维码:")
        print(f"    {url}")
        print("[*] 如果Windows防火墙弹出提示，请务必【允许访问】。")
        print("[*] 在此终端按 'Ctrl+C' 来终止程序。")
        print("=" * 50)
        qr = qrcode.QRCode()
        qr.add_data(url)
        qr.print_ascii(invert=True)
        # 使用 waitress 或其他生产级WSGI服务器会更佳，但对于本地工具 Flask 自带服务器足够
        app.run(host='0.0.0.0', port=PORT, debug=False)
    except PermissionError:
        print(f"\n[错误] 权限不足，无法在端口 {PORT} 上启动服务器。")
        print("请务必以【管理员身份】运行此脚本！")
    except OSError as e:
        if "make_sock: could not bind to address" in str(e):
             print(f"\n[错误] 端口 {PORT} 已被占用。")
             print("请检查是否有其他程序正在使用此端口，或在 config.py 中更换端口号。")
        else:
            print(f"程序运行时发生操作系统错误: {e}")
    except Exception as e:
        print(f"程序运行时发生未知错误: {e}")
    finally:
        print("\n服务器已关闭，程序退出。")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # 允许用户通过 Ctrl+C 优雅地退出程序
        pass