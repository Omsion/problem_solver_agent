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
from .accounts import AccountManager
from .auto_import import start_auto_import, stop_auto_import
from .models import TaskManager
from .pipeline import PipelineService, delete_ocr_archives
from .retention import prune_image_cache, prune_uploads, stale_uploads
from .routers.admin import router as admin_router
from .routers.auth import router as auth_router
from .routes import init_router, router

logger = logging.getLogger("WebappStartup")


def _check_capabilities() -> dict:
    """启动时做一次"能力探测"。

    缺陷 N4：`validate_config()` 只在 CLI 里被调用，Web 端配置写错时一路启动
    正常，直到用户提交第一道题才报错。这里改为启动阶段就把问题说清楚，
    但不阻断启动（缺少可选 provider 时仍可只用已配置的那个）。
    """
    problems: list[str] = []
    # 视觉层密钥按**当前 provider** 判定：迁移到 deepseek 后再检查 ZHIPU_API_KEY
    # 会永远报"缺少密钥"，而实际配置是对的（回退到 zhipu 时反过来同理）。
    if not core_config.VISION_API_KEY:
        vision_cfg = core_config.VISION_PROVIDER_CONFIG[core_config.VISION_PROVIDER]
        problems.append(
            f"缺少 {vision_cfg['api_key_env']}：VISION_PROVIDER={core_config.VISION_PROVIDER} "
            "的视觉分类/OCR 将不可用"
        )
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
    logger.info("  OCR 归档目录  : %s", core_config.OCR_DIR)
    logger.info("  解答输出目录  : %s", core_config.SOLUTION_DIR)
    logger.info("  图片缓存目录  : %s (上限 %d MB)", core_config.IMAGE_CACHE_DIR, core_config.IMAGE_CACHE_MAX_MB)
    logger.info(
        "  图片预处理    : 最长边 %d / JPEG q%d / 合并视觉调用=%s（上限 %d 张）",
        core_config.IMAGE_MAX_EDGE,
        core_config.IMAGE_JPEG_QUALITY,
        core_config.USE_COMBINED_VISION_CALL,
        core_config.COMBINED_VISION_MAX_IMAGES,
    )
    logger.info("  并发上限      : %d 个任务", core_config.MAX_CONCURRENT_TASKS)
    # 视觉层是迁移后最容易"跑在别的 provider 上而不自知"的地方：
    # 把 provider/模型/思考开关显式打出来，日志里一眼能确认走的是哪条路。
    vision_config = core_config.VISION_PROVIDER_CONFIG[core_config.VISION_PROVIDER]
    if vision_config.get("supports_thinking_control") == "1":
        vision_thinking = "关闭" if core_config.VISION_DISABLE_THINKING else "开启"
    else:
        vision_thinking = "不适用（该 provider 无思考开关）"
    logger.info(
        "  视觉层        : provider=%s / 模型=%s / 思考=%s",
        core_config.VISION_PROVIDER,
        core_config.VISION_CLASSIFY_MODEL,
        vision_thinking,
    )
    if web_config.AUTH_ENABLED:
        logger.info("  访问控制      : 已启用（需登录，额度 %.2f 元起）", web_config.DEFAULT_USER_BUDGET)
        if not web_config.AUTH_SECRET_KEY:
            logger.warning(
                "AUTH_ENABLED=true 但未设置 AUTH_SECRET_KEY：已生成临时密钥，"
                "服务重启后所有登录令牌会失效。生产环境请在 .env 中显式配置。"
            )
    else:
        logger.warning(
            "  访问控制      : 未启用（AUTH_ENABLED=false）—— "
            "任何能访问本机 8000 端口的人都可以提交任务并消耗你的 API 额度。"
            "如需多用户与额度控制，请在 .env 中设置 AUTH_ENABLED=true。"
        )
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


def _startup_cleanup(task_manager: TaskManager, *, upload_dir: Path | None = None) -> None:
    """启动时清理：残留上传目录、超量图片缓存。

    缺陷 N2：旧实现只删解答文件，`uploads/<task_id>/` 从未被清理，
    实测已累积 45.6 MB。

    **安全护栏（2026-09-20 事故后新增）**：真实事故中 `webapp/uploads/*/` 下 8 个
    上传目录被整批删除，而这 8 个目录**都对应真实 DB 里的任务** —— 也就是说删除
    发生在一个"任务库为空/不是这一份"的调用上下文里。根因是签名本身：`task_manager`
    由调用方传入，而上传目录取自全局 `web_config.UPLOAD_DIR`，两者可以来自**不同的
    配置**（测试用 tmp 库、探针脚本自造库、`SOLVER_ROOT_DIR`/`DB_PATH` 被覆盖的第二个
    实例）。此时每个真实上传目录都会被视为"无主残留"而被删除，且不可逆。

    因此这里加两条护栏：
    1. 显式接受 `upload_dir` 参数（默认仍是全局值），调用方想清理别处必须显式传；
    2. **任务库为空而上传目录非空时，只告警不删除** —— "空库 + 有上传目录"正是
       配置配错的特征，而真正的"删库后残留"场景下用户多半想要那些目录。
    代价是这种组合下残留不会被自动回收（可用 `tools` 手工清理或补齐 DB）。

    清理无主目录时**同时删除对应的 OCR 归档**（计划书 11.6-1）：这条路径处理的任务
    DB 行已经消失，永远不会再被 `delete_task()` 或保留策略扫到，不在这里删就会留下
    永久性的题面归档。两条护栏对归档同样成立（空任务库走的是上面的告警分支）。
    """
    target_dir = upload_dir if upload_dir is not None else web_config.UPLOAD_DIR
    try:
        task_ids = task_manager.all_task_ids()
        orphans = stale_uploads(target_dir, task_ids)
        if orphans:
            if not task_ids:
                logger.warning(
                    "启动清理已跳过：任务库为空而上传目录 %s 下有 %d 个目录。"
                    "这通常意味着 DB_PATH 与 UPLOAD_DIR 不是同一套配置，"
                    "继续清理会不可逆地删掉真实上传原图。",
                    target_dir, len(orphans),
                )
            else:
                prune_uploads(target_dir, task_ids)
                logger.info("启动清理：移除 %d 个无主上传目录", len(orphans))
                # 11.6-1：无主任务的 OCR 归档必须跟着上传目录一起走。
                # 这条路径处理的是"DB 行已消失"的任务，它们永远不会再被 `delete_task()`
                # 或保留策略扫到 —— 不在这里删，`<OCR_DIR>/<日期>/<task_id>.md` 里的
                # **原始题面**就会永久留在盘上（归档比上传目录多留一份题面）。
                # 护栏与上面同源：空任务库已在 `if not task_ids` 分支里提前返回，
                # 因此这里不可能拿到"整批任务 id"去误删（删除本身还带 OCR_DIR 包含性校验）。
                removed_archives = delete_ocr_archives(orphans)
                if removed_archives:
                    logger.info("启动清理：移除 %d 份无主 OCR 归档", removed_archives)
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
    # 账户体系与任务库共用同一个 SQLite 文件：单机部署下少一个要备份的东西
    accounts = AccountManager(web_config.DB_PATH)

    init_router(task_manager, pipeline_service, accounts)

    app = FastAPI(
        title="自动化解题 Agent",
        version="2.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    # 生命周期与依赖注入都要拿到这些对象
    app.state.task_manager = task_manager
    app.state.pipeline_service = pipeline_service
    app.state.accounts = accounts

    app.include_router(router)
    # 认证与账户
    app.include_router(auth_router)
    # 管理员看板
    app.include_router(admin_router)

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
