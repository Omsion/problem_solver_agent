# -*- coding: utf-8 -*-
r"""开发用 Web 入口（带热重载）

与 `run_web.py` 的区别：这个脚本开启 uvicorn 的自动重载，改 Python 代码后
服务会自己重启，不需要手动停/起。仅在开发时使用。

用法：
    python run_web_dev.py            # 默认 8000
    python run_web_dev.py 9000       # 自定义端口

为什么要单独一个脚本：IDE 的「Python 运行配置」只接受一个脚本或模块名，
没法直接表达 `uvicorn webapp.app:create_app --factory --reload` 这种组合。
写在这里比在 IDE 里拼参数更不容易出错，命令行也能直接用。
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中（与 run_web.py 保持一致）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402
from webapp.config import HOST, PORT as DEFAULT_PORT  # noqa: E402


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    print(f"启动 Web 服务（热重载）→ http://localhost:{port}")
    print("修改 Python 代码后会自动重启；按 Ctrl+C 停止。")

    uvicorn.run(
        "webapp.app:create_app",
        host=HOST,
        port=port,
        factory=True,
        reload=True,
        # 只监听业务代码，避免 uploads/ 与 data/ 里的文件变动触发无谓重启
        reload_dirs=["problem_solver_agent", "webapp"],
        log_level="info",
    )
