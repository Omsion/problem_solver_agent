# -*- coding: utf-8 -*-
r"""Web 应用入口
用法: python run_web.py
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from webapp.config import HOST, PORT

if __name__ == "__main__":
    print(f"启动 Web 服务 → http://localhost:{PORT}")
    uvicorn.run("webapp.app:create_app", host=HOST, port=PORT, factory=True, log_level="info")
