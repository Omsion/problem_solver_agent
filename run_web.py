# -*- coding: utf-8 -*-
r"""Web 应用入口
用法: python run_web.py        # 默认端口 8000
      python run_web.py 9000   # 自定义端口
"""

import sys
import time
import threading
import webbrowser
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from webapp.config import HOST, PORT as DEFAULT_PORT


def _open_browser(port: int, delay: float = 1.5) -> None:
    """等待服务就绪后自动打开浏览器（守护线程）。"""
    time.sleep(delay)
    webbrowser.open(f"http://localhost:{port}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    print(f"启动 Web 服务 → http://localhost:{port}")
    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()
    uvicorn.run("webapp.app:create_app", host=HOST, port=port, factory=True, log_level="info")
