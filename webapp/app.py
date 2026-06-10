"""FastAPI 应用工厂"""


from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config as web_config
from .auto_import import start_auto_import
from .models import TaskManager
from .pipeline import PipelineService
from .routes import init_router, router


def create_app() -> FastAPI:
    for d in [web_config.UPLOAD_DIR, web_config.SOLUTION_DIR, web_config.DATA_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    task_manager = TaskManager(web_config.DB_PATH)
    pipeline_service = PipelineService(web_config.SOLUTION_DIR, task_manager)

    init_router(task_manager, pipeline_service)

    app = FastAPI(title="自动化解题 Agent", version="1.0.0", docs_url=None, redoc_url=None)
    app.include_router(router)

    # CORS 中间件 — 允许 React 开发服务器 (localhost:5173) 跨域访问
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

    # 启动时预热所有 API 客户端，避免首次调用时初始化耗时
    @app.on_event("startup")
    async def _warmup_clients():
        import logging
        logger = logging.getLogger("WebappStartup")
        try:
            from problem_solver_agent import config as core_config
            from problem_solver_agent import solver_client, vision_client
            # 预热求解器客户端
            for provider in core_config.SOLVER_CONFIG:
                try:
                    solver_client.get_client(provider)
                    logger.info("求解器客户端 '%s' 预热完成", provider)
                except Exception as e:
                    logger.warning("预热求解器 '%s' 失败: %s", provider, e)
            # 预热辅助模型客户端（如果与求解器不同）
            aux = core_config.AUX_PROVIDER
            if aux not in core_config.SOLVER_CONFIG:
                try:
                    solver_client.get_client(aux)
                    logger.info("辅助模型客户端 '%s' 预热完成", aux)
                except Exception as e:
                    logger.warning("预热辅助客户端 '%s' 失败: %s", aux, e)
            # 预热视觉客户端
            try:
                vision_client._get_vision_client()
                logger.info("视觉客户端预热完成")
            except Exception as e:
                logger.warning("预热视觉客户端失败: %s", e)

            # 启动自动截图导入监控
            try:
                start_auto_import(task_manager, pipeline_service)
                logger.info("自动截图导入功能已启动")
            except Exception as e:
                logger.warning("启动自动截图导入失败: %s", e, exc_info=True)
        except Exception as e:
            logger.warning("客户端预热过程出错: %s", e)

    # SPA fallback 路由 — 生产模式下，未匹配的 GET 请求返回 React index.html
    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str, request: Request):
        index_path = web_config.STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return JSONResponse({"error": "Not Found"}, status_code=404)

    return app
