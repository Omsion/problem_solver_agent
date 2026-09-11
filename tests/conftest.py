"""
conftest.py - 测试公共夹具

1. 把仓库根目录加入 sys.path，使 `pytest` 可以从任意目录运行
2. 关闭自动截图导入：测试不希望真的去监控用户的截图目录
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在使用 create_app() 之前设置：WebAutoImporter 在构造时读取该变量。
# 否则每个 TestClient 都会真的启动一个监控用户 Screenshots 目录的 watchdog。
os.environ.setdefault("AUTO_IMPORT_ENABLED", "false")
