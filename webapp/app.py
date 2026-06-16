"""FastAPI 应用工厂"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from problem_solver_agent import config as core_config

from . import config as web_config
from .auto_import import start_auto_import, stop_auto_import
from .models import TaskManager
from .pipeline import PipelineService
from .retention import prune_image_cache, prune_uploads, stale_uploads
from .routes import init_router, router

logger = logging.getLogger("WebappStartup")


def _check_capabilities() -> dict:
    """启动时做一次"能力探测"。

    缺陷 N4：`validate_config()` 只在 CLI 里被调用，Web 端配置写错时一路启动
    正常，直到用户提交第一道题才报错。这里改为启动阶段就把问题说清楚，
    但不阻断启动（缺少可选 provider 时仍可只用已配置的那个）。
    """
    problems: list[str] = []
    if not core_config.ZHIPU_API_KEY:
        problems.append("缺少 ZHIPU_API_KEY：视觉分类/OCR 将不可用")
    for provider in core_config.SOLVER_CONFIG:
        key = getattr(core_config, f"{provider.upper()}_API_KEY", None)
        if not key:
            problems.append(f"缺少 {provider.upper()}_API_KEY：求解器 '{provider}' 不可用")
    return {"problems": problems, "ok": not problems}


def _startup_report() -> None:
    """打印关键路径与监控配置，避免"产物跑到别处去了"这类困惑。"""
    logger.info("=" * 60)
    logger.info("自动化解题 Agent 启动中")
    logger.info("  项目目录      : %s", core_config._PROJECT_DIR)  # noqa: SLF001 - 诊断信息
    logger.info("  工作根目录    : %s", core_config.ROOT_DIR)
    logger.info("  截图监控目录  : %s", core_config.MONITOR_DIR)
    logger.info("  归档目录      : %s", core_config.PROCESSED_DIR)
    logger.info("  解答输出目录  : %s", core_config.SOLUTION_DIR)
    logger.info("  图片缓存目录  : %s (上限 %d MB)", core_config.IMAGE_CACHE_DIR, core_config.IMAGE_CACHE_MAX_MB)
    logger.info(
        "  图片预处理    : 最长边 %d / JPEG q%d / 合并视觉调用=%s",
        core_config.IMAGE_MAX_EDGE,
        core_config.IMAGE_JPEG_QUALITY,
        core_config.USE_COMBINED_VISION_CALL,
    )
    logger.info("  并发上限      : %d 个任务", core_config.MAX_CONCURRENT_TASKS)
    logger.info("=" * 60)

    caps = _check_capabilities()
    for problem in caps["problems"]:
        logger.warning("配置问题：%s", problem)


def _warmup_clients() -> None:
    """预热 API 客户端，避免首次调用时初始化耗时。"""
    from problem_solver_agent import solver_client, vision_client

    for provider in core_config.SOLVER_CONFIG:
        try:
            solver_client.get_client(provider)
            logger.info("求解器客户端 '%s' 预热完成", provider)
        except Exception as exc:
            logger.warning("预热求解器 '%s' 失败: %s", provider, exc)

    aux = core_config.AUX_PROVIDER
    if aux not in core_config.SOLVER_CONFIG:
        try:
            solver_client.get_client(aux)
            logger.info("辅助模型客户端 '%s' 预热完成", aux)
        except Exception as exc:
            logger.warning("预热辅助客户端 '%s' 失败: %s", aux, exc)

    try:
        vision_client._get_vision_client()  # noqa: SLF001 - 预热入口
        logger.info("视觉客户端预热完成")
    except Exception as exc:
        logger.warning("预热视觉客户端失败: %s", exc)


def _startup_cleanup(task_manager: TaskManager) -> None:
    """启动时清理：残留上传目录、超量图片缓存。

    缺陷 N2：旧实现只删解答文件，`uploads/<task_id>/` 从未被清理，
    实测已累积 45.6 MB。
    """
    try:
        orphans = stale_uploads(web_config.UPLOAD_DIR, task_manager.all_task_ids())
        if orphans:
            prune_uploads(web_config.UPLOAD_DIR, task_manager.all_task_ids())
            logger.info("启动清理：移除 %d 个无主上传目录", len(orphans))
    except Exception as exc:
        logger.warning("启动清理上传目录失败: %s", exc)

    try:
        removed = prune_image_cache(core_config.IMAGE_CACHE_DIR, core_config.IMAGE_CACHE_MAX_MB * 1024 * 1024)
        if removed:
            logger.info("启动清理：移除 %d 个过期图片缓存", removed)
    except Exception as exc:
        logger.warning("启动清理图片缓存失败: %s", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """应用生命周期：原实现使用已弃用的 `@app.on_event("startup")`。"""
    _startup_report()

    task_manager: TaskManager = app.state.task_manager
    pipeline_service: PipelineService = app.state.pipeline_service

    _startup_cleanup(task_manager)
    _warmup_clients()

    # 启动自动截图导入（可用 AUTO_IMPORT_ENABLED=false 关闭）
    try:
        start_auto_import(task_manager, pipeline_service)
    except Exception as exc:
        logger.warning("启动自动截图导入失败: %s", exc, exc_info=True)

    try:
        yield
    finally:
        try:
            stop_auto_import()
        except Exception as exc:
            logger.warning("停止自动截图导入失败: %s", exc)


def create_app() -> FastAPI:
    for directory in (web_config.UPLOAD_DIR, web_config.SOLUTION_DIR, web_config.DATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    task_manager = TaskManager(web_config.DB_PATH)
    pipeline_service = PipelineService(web_config.SOLUTION_DIR, task_manager)

    init_router(task_manager, pipeline_service)

    app = FastAPI(
        title="自动化解题 Agent",
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    # 生命周期需要拿到这两个对象
    app.state.task_manager = task_manager
    app.state.pipeline_service = pipeline_service
    app.include_router(router)

    # CORS：仅开发时前端跑在 5173 端口需要，生产同源访问不需要
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if web_config.STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(web_config.STATIC_DIR)), name="static")
    if web_config.SOLUTION_DIR.exists():
        app.mount("/solutions", StaticFiles(directory=str(web_config.SOLUTION_DIR)), name="solutions")
    if web_config.UPLOAD_DIR.exists():
        app.mount("/uploads", StaticFiles(directory=str(web_config.UPLOAD_DIR)), name="uploads")

    # SPA fallback 路由 — 生产模式下未匹配的 GET 请求返回 React index.html。
    # 注意：以 /api/ 开头的未知路径必须返回 404 JSON，否则前端会把 HTML 当成
    # 接口响应解析，报出难以定位的错误。
    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str, request: Request):
        if full_path.startswith("api/"):
            return JSONResponse({"error": f"未知接口: /{full_path}"}, status_code=404)
        index_path = web_config.STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return JSONResponse(
            {"error": "前端尚未构建，请先执行 cd frontend && npm run build"},
            status_code=404,
        )

    return app
