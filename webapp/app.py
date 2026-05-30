# -*- coding: utf-8 -*-
"""FastAPI 应用工厂"""

import time
import re
from pathlib import Path

import markdown
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config as web_config
from .models import TaskManager
from .pipeline import PipelineService
from .routes import router, init_router


def _format_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _status_label(status: str) -> str:
    return {"pending": "等待中", "processing": "处理中", "completed": "已完成", "failed": "失败"}.get(status, status)


def _safe_markdown(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=["fenced_code", "codehilite", "tables", "mdx_math"],
        extension_configs={"mdx_math": {"enable_dollar_delimiter": True}},
    )


def create_app() -> FastAPI:
    for d in [web_config.UPLOAD_DIR, web_config.SOLUTION_DIR, web_config.DATA_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    task_manager = TaskManager(web_config.DB_PATH)
    pipeline_service = PipelineService(web_config.SOLUTION_DIR, task_manager)
    templates = Jinja2Templates(directory=str(web_config.TEMPLATES_DIR))
    templates.env.filters["format_ts"] = _format_ts
    templates.env.filters["status_label"] = _status_label
    templates.env.filters["safe_markdown"] = _safe_markdown

    init_router(task_manager, pipeline_service, templates)

    app = FastAPI(title="自动化解题 Agent", version="1.0.0", docs_url=None, redoc_url=None)
    app.include_router(router)

    if web_config.STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(web_config.STATIC_DIR)), name="static")
    if web_config.SOLUTION_DIR.exists():
        app.mount("/solutions", StaticFiles(directory=str(web_config.SOLUTION_DIR)), name="solutions")

    return app
