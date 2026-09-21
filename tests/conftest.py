"""
conftest.py - 测试公共夹具

1. 把仓库根目录加入 sys.path，使 `pytest` 可以从任意目录运行
2. 关闭自动截图导入：测试不希望真的去监控用户的截图目录
3. **把"会删文件"的路径全部重定向到 tmp_path**：见下面的 autouse 夹具
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在使用 create_app() 之前设置：WebAutoImporter 在构造时读取该变量。
# 否则每个 TestClient 都会真的启动一个监控用户 Screenshots 目录的 watchdog。
os.environ.setdefault("AUTO_IMPORT_ENABLED", "false")


@pytest.fixture(autouse=True)
def isolate_destructive_paths(tmp_path, monkeypatch):
    """把可能被清理的目录默认指向 `tmp_path`（所有用例，包括纯单测）。

    为什么需要这层全局网（2026-09-20 事故）：
    - `webapp.app._startup_cleanup(task_manager)` 会按 `web_config.UPLOAD_DIR` 删除
      "无主"上传目录；而它接收的 `task_manager` 由调用方传入，两者可能来自**不同配置**
      （tmp 任务库 + 全局上传目录）。真实事故就丢掉了 `uploads/*/` 下 8 个目录。
    - `webapp.pipeline._cleanup_old()` 与 `prune_image_cache(core_config.IMAGE_CACHE_DIR)`
      同理：删的是**全局配置里的真实目录**，只要某个用例忘了 monkeypatch，就会动到真数据。

    这层网把"忘打补丁"的代价从"不可逆地删掉用户数据"降为"多测一个空目录"。
    个别用例想覆盖时仍可自行 `monkeypatch.setattr`（后设置的生效）。
    """
    from problem_solver_agent import config as core_config
    from webapp import config as web_config

    overrides = (
        (web_config, "UPLOAD_DIR", tmp_path / "uploads"),
        (web_config, "SOLUTION_DIR", tmp_path / "solutions"),
        (web_config, "DATA_DIR", tmp_path / "data"),
        (web_config, "DB_PATH", tmp_path / "data" / "tasks.db"),
        (core_config, "IMAGE_CACHE_DIR", tmp_path / "image_cache"),
        (core_config, "OCR_DIR", tmp_path / "ocr"),
    )
    for module, attribute, value in overrides:
        monkeypatch.setattr(module, attribute, value, raising=False)
    yield
