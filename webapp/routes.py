"""webapp 路由模块 — REST API + SSE 流式端点"""

import asyncio
import io
import json
import shutil
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path

import qrcode
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image

from problem_solver_agent import config as core_config
from problem_solver_agent.netcheck import get_lan_ip, is_remote_device
from problem_solver_agent.utils import sanitize_filename

from . import config as web_config
from .jobs import TaskRegistry
from .presence import RemotePresence, watch_connection
from .retention import dir_size_bytes
from .timings import aggregate_timings

router = APIRouter()

# 由 app.py 在启动时注入
task_manager = None
pipeline_service = None


class TaskEventBus:
    """Per-task event bus — 广播进度事件给同一任务的所有 SSE 订阅者。"""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._history: dict[str, list[dict]] = {}
        self._global_queues: list[asyncio.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._queues.setdefault(task_id, []).append(q)
            for event in self._history.get(task_id, []):
                q.put_nowait(event)
        return q

    def subscribe_global(self) -> asyncio.Queue:
        """订阅全局事件（用于 auto_imported 等广播事件）。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._global_queues.append(q)
        return q

    def publish(self, task_id: str, event: dict) -> None:
        with self._lock:
            self._history.setdefault(task_id, []).append(event)
            queues = list(self._queues.get(task_id, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def publish_global(self, event: dict) -> None:
        """发布全局广播事件。"""
        with self._lock:
            queues = list(self._global_queues)
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            if task_id in self._queues:
                self._queues[task_id] = [x for x in self._queues[task_id] if x is not q]
                if not self._queues[task_id]:
                    del self._queues[task_id]

    def unsubscribe_global(self, q: asyncio.Queue) -> None:
        """取消订阅全局事件。"""
        with self._lock:
            self._global_queues = [x for x in self._global_queues if x is not q]

    def cleanup(self, task_id: str) -> None:
        with self._lock:
            self._queues.pop(task_id, None)
            self._history.pop(task_id, None)


event_bus = TaskEventBus()
# 运行中任务的取消令牌与并发上限统一由 TaskRegistry 管理
tasks_registry = TaskRegistry(max_concurrent=core_config.MAX_CONCURRENT_TASKS)
# 远程手机连接跟踪：只有"断开全部连接"才视为手机离开
remote_presence = RemotePresence()

# 任务终态：到达这些状态后不再启动流水线
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# 会让 SSE 生成器结束的事件
TERMINAL_EVENTS = ("done", "error", "cancelled")


def init_router(tm, ps):
    global task_manager, pipeline_service
    task_manager = tm
    pipeline_service = ps


def _task_images(task_dir: Path) -> list[Path]:
    """列出任务的上传图片（只接受图片扩展名，避免把杂项文件当图片）。"""
    if not task_dir.exists():
        return []
    return sorted(
        p for p in task_dir.iterdir()
        if p.is_file() and p.suffix.lower() in web_config.ALLOWED_EXTENSIONS
    )


def _is_valid_image_bytes(payload: bytes) -> bool:
    """用 Pillow 校验内容确实是可解码的图片（只信内容，不信扩展名）。"""
    if not payload:
        return False
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        return True
    except Exception:
        return False


def _remove_tree(path: Path) -> None:
    """上传失败时清理已创建的目录（尽力而为）。"""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # pragma: no cover - ignore_errors 已兜底
        pass


def _single_event_response(event_type: str, payload: dict) -> StreamingResponse:
    """返回只包含一个事件的 SSE 响应。"""
    async def _generator():
        yield f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(_generator(), media_type="text/event-stream")


def _terminal_response(task: dict) -> StreamingResponse:
    """终态任务的 SSE 响应：让客户端立刻得到结论而不是一直等待。"""
    status = task["status"]
    if status == "completed":
        return _single_event_response("done", {
            "type": "done",
            "task_id": task["id"],
            "filename": task.get("filename", ""),
            "timings": task.get("timings"),
        })
    if status == "cancelled":
        return _single_event_response("cancelled", {
            "type": "cancelled",
            "task_id": task["id"],
            "message": "任务已取消，已保留已生成的内容",
        })
    return _single_event_response("error", {
        "type": "error",
        "task_id": task["id"],
        "message": task.get("error_message") or "任务失败",
    })


# ==============================================================================
# REST API
# ==============================================================================

@router.post("/api/tasks")
async def create_task(files: list[UploadFile] = File(...)):
    """上传图片，创建任务，返回 task_id。

    安全要点：绝不直接使用客户端提供的文件名拼接路径。旧实现写成
    `task_dir / f.filename`，`../../.env` 这样的文件名可以覆盖任意文件。
    现在只取 basename、清理非法字符、同名自动加序号，并校验文件确实是图片。
    """
    if not files:
        return JSONResponse({"error": "请至少上传一张图片"}, status_code=400)

    max_bytes = web_config.MAX_UPLOAD_SIZE * 1024 * 1024
    total_bytes = 0

    task_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    task_dir = web_config.UPLOAD_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[Path] = []
    used_names: set[str] = set()

    for upload in files:
        raw_name = Path(upload.filename or "").name  # 去掉任何目录部分
        safe_name = sanitize_filename(raw_name).strip(" .")
        if not safe_name:
            _remove_tree(task_dir)
            return JSONResponse({"error": "文件名无效"}, status_code=400)

        ext = Path(safe_name).suffix.lower()
        if ext not in web_config.ALLOWED_EXTENSIONS:
            _remove_tree(task_dir)
            return JSONResponse({"error": f"不支持的文件类型: {ext or '(无扩展名)'}"}, status_code=400)

        content = await upload.read()
        total_bytes += len(content)
        if total_bytes > max_bytes:
            _remove_tree(task_dir)
            return JSONResponse(
                {"error": f"上传内容超过 {web_config.MAX_UPLOAD_SIZE} MB 限制", "code": "too_large"},
                status_code=413,
            )

        if not _is_valid_image_bytes(content):
            _remove_tree(task_dir)
            return JSONResponse(
                {"error": f"文件不是有效图片: {safe_name}", "code": "invalid_image"},
                status_code=400,
            )

        # 同名冲突时加序号，避免互相覆盖
        candidate = safe_name
        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        counter = 2
        while candidate in used_names or (task_dir / candidate).exists():
            candidate = f"{stem}_{counter}{suffix}"
            counter += 1

        file_path = task_dir / candidate
        file_path.write_bytes(content)
        used_names.add(candidate)
        image_paths.append(file_path)

    task_manager.create_task(task_id, len(image_paths))
    return {"task_id": task_id, "num_images": len(image_paths)}


@router.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str, thinking: bool = False, style: str | None = None):
    """SSE 流式端点 — 启动流水线处理并实时推送进度。

    Query Parameters:
        thinking: 设为 True 启用求解器思考模式（DeepSeek reasoning），
                  思考过程以 type="reasoning" 事件独立推送。
        style: 编程题求解风格（OPTIMAL / EXPLORATORY），留空用全局配置。
    """
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    # 终态任务直接返回对应事件，不再启动流水线
    if task["status"] in TERMINAL_STATUSES:
        return _terminal_response(task)

    task_dir = web_config.UPLOAD_DIR / task_id
    image_paths = _task_images(task_dir)
    if not image_paths:
        message = "上传文件已过期，请重新提交"
        task_manager.update_task(task_id, status="failed", error_message=message)
        return _single_event_response("error", {"type": "error", "message": message})

    q: asyncio.Queue = event_bus.subscribe(task_id)

    def _on_progress(event: dict) -> None:
        event_bus.publish(task_id, event)

    def _run(token) -> None:
        try:
            pipeline_service.run(
                task_id,
                image_paths,
                _on_progress,
                enable_thinking=thinking,
                cancel=token,
                style=style,
            )
        except Exception:
            # 具体错误已由 PipelineService 写入数据库并通过事件推送
            pass
        finally:
            # 运行结束：清理事件历史，避免下次重试时重放旧事件
            event_bus.cleanup(task_id)

    def _work() -> None:
        tasks_registry.run_with_slot(task_id, _run)

    threading.Thread(target=_work, daemon=True).start()

    async def _event_generator():
        try:
            init_event = {"type": "init", "task_id": task_id, "num_images": len(image_paths)}
            event_bus.publish(task_id, init_event)
            yield f"event: init\ndata: {json.dumps(init_event, ensure_ascii=False)}\n\n"

            heartbeat_interval = 30
            last_heartbeat = time.time()

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=heartbeat_interval)
                except asyncio.TimeoutError:
                    now = time.time()
                    if now - last_heartbeat >= heartbeat_interval:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    continue

                ev_type = event.get("type", "message")
                yield f"event: {ev_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

                if ev_type in TERMINAL_EVENTS:
                    break
        except asyncio.CancelledError:
            # 客户端断开：不取消任务，只停止推送
            raise
        finally:
            event_bus.unsubscribe(task_id, q)

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


@router.post("/api/tasks/{task_id}/cancel")
@router.delete("/api/tasks/{task_id}/cancel")  # 兼容旧前端
async def cancel_task(task_id: str):
    """取消正在处理的任务。

    与旧实现的区别：
    - 真的会让流水线停下来（令牌在阶段边界与流式分片之间被检查）
    - 立即把状态置为 `cancelled`，并在运行中的线程结束时由流水线写入部分内容
    - 不再因为 `_processing_locks` 未释放而导致"取消后无法重试"
    """
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    if task["status"] in TERMINAL_STATUSES:
        return JSONResponse({"error": "任务已结束，无法取消", "code": "not_cancellable"}, status_code=400)

    signalled = tasks_registry.cancel(task_id)
    # 立即更新状态，让界面无须等待线程收敛
    task_manager.update_task(task_id, status="cancelled", error_message="")
    event_bus.publish(task_id, {
        "type": "cancelled",
        "task_id": task_id,
        "message": "任务已取消，已保留已生成的内容",
    })
    return {"status": "ok", "signalled": signalled, "message": "取消请求已发送"}


@router.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str, thinking: bool = True):
    """重试失败或已取消的任务。

    会复用阶段缓存（分类/转录结果），因此重试不会重复消耗视觉模型额度。
    """
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    if task["status"] not in ("failed", "cancelled"):
        return JSONResponse(
            {"error": "只有失败或已取消的任务可以重试", "code": "not_retryable"},
            status_code=400,
        )

    if tasks_registry.is_running(task_id):
        return JSONResponse({"error": "任务正在处理中", "code": "already_running"}, status_code=409)

    task_dir = web_config.UPLOAD_DIR / task_id
    if not _task_images(task_dir):
        return JSONResponse(
            {"error": "原图已被清理，请重新截图或上传", "code": "upload_expired"},
            status_code=410,
        )

    # 清掉上一次的错误信息与残留事件，避免前端重放旧状态
    task_manager.update_task(task_id, status="pending", error_message="")
    event_bus.cleanup(task_id)
    return {"status": "ok", "task_id": task_id, "resumed": True, "thinking": thinking}


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """获取单个任务的详情（含解答内容和图片 URL）。"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    solution_content = ""
    if task["status"] == "completed" and task["solution_path"]:
        sp = Path(task["solution_path"])
        if sp.exists():
            solution_content = sp.read_text(encoding="utf-8")

    # 列出已上传的图片 URL
    image_urls: list[str] = []
    task_dir = web_config.UPLOAD_DIR / task_id
    if task_dir.exists():
        image_urls = [f"/uploads/{task_id}/{p.name}" for p in sorted(task_dir.glob("*")) if p.is_file()]

    return {"task": task, "solution_content": solution_content, "image_urls": image_urls}


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除任务及其解答文件和上传图片。"""
    solution_path = task_manager.delete_task(task_id)
    if solution_path:
        try:
            Path(solution_path).unlink(missing_ok=True)
        except OSError:
            pass
    # 清理上传图片目录
    task_dir = web_config.UPLOAD_DIR / task_id
    if task_dir.exists():
        try:
            for p in task_dir.glob("*"):
                p.unlink(missing_ok=True)
            task_dir.rmdir()
        except OSError:
            pass
    return {"status": "ok"}


@router.get("/api/tasks")
async def list_tasks(limit: int = 100):
    """获取最近的任务列表。"""
    tasks = task_manager.get_recent_tasks(limit=limit)
    return {"tasks": tasks}


@router.get("/api/events/stream")
async def stream_global_events(request: Request):
    """全局 SSE 端点 — 接收 auto_imported 等广播事件。

    同时检测手机连接：判定条件是「来源不是本机 **且** User-Agent 像移动设备」
    （见 problem_solver_agent/netcheck.py）。电脑端浏览器自身的所有请求都来自
    本机地址，因此不会被误判，二维码按钮也不会因为弹出二维码而自己消失。
    """
    q = event_bus.subscribe_global()

    client_host = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    lan_ip = get_lan_ip()
    is_remote = is_remote_device(client_host, user_agent, extra_local=[lan_ip])

    # 登记远程设备连接；断开时由 close_connection 统一注销（幂等）
    # 注意：publish_global 只投递给"已订阅"的队列，而本函数在上面已经先
    # subscribe_global 了，所以这台手机自己的第一条连接也能收到 remote_connected。
    if is_remote and client_host:
        def _on_connected(identity: str) -> None:
            event_bus.publish_global({"type": "remote_connected", "client_ip": identity})

        def _on_disconnected(identity: str) -> None:
            event_bus.publish_global({"type": "remote_disconnected", "client_ip": identity})

        close_connection = watch_connection(remote_presence, client_host, _on_connected, _on_disconnected)
    else:
        close_connection = None

    async def _event_generator():
        # 整个生成器体（含第一个 yield）都必须包在 try/finally 里：
        # 客户端可能在只消费了第一段之后就断开，此时只有 finally 能保证
        # 远程连接计数与全局订阅被正确注销。
        try:
            yield f"event: init\ndata: {json.dumps({'type': 'init', 'message': 'connected'}, ensure_ascii=False)}\n\n"

            # SSE 心跳间隔（秒）
            heartbeat_interval = 30
            last_heartbeat = time.time()

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=heartbeat_interval)
                except asyncio.TimeoutError:
                    now = time.time()
                    if now - last_heartbeat >= heartbeat_interval:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    continue

                ev_type = event.get("type", "message")
                yield f"event: {ev_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        except GeneratorExit:
            raise
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            event_bus.unsubscribe_global(q)
            if close_connection is not None:
                close_connection()

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


# ==============================================================================
# 工具 API
# ==============================================================================

@router.get("/api/qrcode")
async def qr_code(request: Request):
    """生成局域网访问二维码（PNG 图片）。手机扫码即可在同一局域网访问。"""
    lan_ip = get_lan_ip()
    port = request.url.port or 8000
    url = f"http://{lan_ip}:{port}"
    img = qrcode.make(url, box_size=8, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


# ==============================================================================
# 系统状态 API —— 让"监控是否在跑""磁盘占用多少"变成可见信息
# ==============================================================================

@router.get("/api/status")
async def system_status():
    """系统运行状态：自动截图监控是否在跑、监控目录、磁盘占用、远程连接。"""
    from .auto_import import get_auto_importer

    importer = get_auto_importer()
    monitor = importer.status() if importer else {
        "enabled": False,
        "running": False,
        "monitor_dir": "",
        "started_at": None,
        "last_group_at": None,
        "groups_handled": 0,
        "processing": 0,
    }

    return {
        "auto_import_enabled": monitor["enabled"],
        "running": monitor["running"],
        "monitor_dir": monitor["monitor_dir"],
        "group_timeout": monitor.get("group_timeout"),
        "started_at": monitor.get("started_at"),
        "last_group_at": monitor.get("last_group_at"),
        "groups_handled": monitor.get("groups_handled", 0),
        "processing": monitor.get("processing", 0),
        "uploads_bytes": dir_size_bytes(web_config.UPLOAD_DIR),
        "solutions_bytes": dir_size_bytes(web_config.SOLUTION_DIR),
        "remote_connected": remote_presence.any_connected(),
        "lan_ip": get_lan_ip(),
    }


@router.get("/api/stats")
async def aggregate_stats(limit: int = 50):
    """最近若干任务的阶段耗时与缓存命中统计，用于回答"到底慢在哪"。"""
    rows = task_manager.list_timings(limit=max(1, min(limit, 200)))
    stats = aggregate_timings(rows)
    return {
        "sample_size": stats.sample_size,
        "completed": stats.completed,
        "failed": stats.failed,
        "cache_hit_rate": stats.cache_hit_rate,
        "stages": {
            name: {
                "p50": stage.p50,
                "p90": stage.p90,
                "average": stage.average,
                "samples": stage.samples,
                "cache_hits": stage.cache_hits,
            }
            for name, stage in stats.stages.items()
        },
    }


@router.get("/api/health")
async def health():
    """轻量健康检查：不做任何外部 API 调用，用于探活与手机端连通性自检。"""
    from problem_solver_agent import config as core_config

    return {
        "status": "ok",
        "version": __import__("webapp").__version__,
        "vision_configured": bool(core_config.ZHIPU_API_KEY),
        "solver_providers": sorted(core_config.SOLVER_CONFIG.keys()),
        "keys_configured": {
            provider: bool(getattr(core_config, f"{provider.upper()}_API_KEY", None))
            for provider in core_config.SOLVER_CONFIG
        },
    }
