"""webapp 路由模块 — REST API + SSE 流式端点"""

import asyncio
import json
import socket
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path

import qrcode
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import config as web_config

router = APIRouter()

# 由 app.py 在启动时注入
task_manager = None
pipeline_service = None

# 任务取消事件存储
_task_cancel_events: dict[str, threading.Event] = {}


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
_processing_locks: dict[str, bool] = {}
_proc_lock = threading.Lock()
# 记录已检测到的远程连接 IP（避免重复推送 remote_connected 事件）
_remote_connected_ips: set[str] = set()


def init_router(tm, ps):
    global task_manager, pipeline_service
    task_manager = tm
    pipeline_service = ps


# ==============================================================================
# REST API
# ==============================================================================

@router.post("/api/tasks")
async def create_task(files: list[UploadFile] = File(...)):
    """上传图片，创建任务，返回 task_id。"""
    if not files:
        return JSONResponse({"error": "请至少上传一张图片"}, status_code=400)

    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in web_config.ALLOWED_EXTENSIONS:
            return JSONResponse({"error": f"不支持的文件类型: {ext}"}, status_code=400)

    task_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    task_dir = web_config.UPLOAD_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    image_paths = []
    for f in files:
        file_path = task_dir / f.filename
        content = await f.read()
        file_path.write_bytes(content)
        image_paths.append(file_path)

    task_manager.create_task(task_id, len(image_paths))
    return {"task_id": task_id, "num_images": len(image_paths)}


@router.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str, thinking: bool = False):
    """SSE 流式端点 — 启动流水线处理并实时推送进度。

    Query Parameters:
        thinking: 设为 True 可启用求解器思考模式（DeepSeek reasoning），
                  思考过程将以 type="reasoning" 事件独立推送。
    """
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    # 已完成/失败的任务，直接返回状态
    if task["status"] == "completed":
        async def _done():
            ev = json.dumps({"type": "done", "task_id": task_id, "filename": task.get("filename", "")}, ensure_ascii=False)
            yield f"event: done\ndata: {ev}\n\n"
        return StreamingResponse(_done(), media_type="text/event-stream")

    if task["status"] == "failed":
        async def _err():
            ev = json.dumps({"type": "error", "message": task.get("error_message", "未知错误")}, ensure_ascii=False)
            yield f"event: error\ndata: {ev}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    task_dir = web_config.UPLOAD_DIR / task_id
    image_paths = sorted(task_dir.glob("*"))
    if not image_paths:
        task_manager.update_task(task_id, status="failed", error_message="上传文件已过期，请重新提交")
        async def _no_files():
            ev = json.dumps({"type": "error", "message": "上传文件已过期，请重新提交"}, ensure_ascii=False)
            yield f"event: error\ndata: {ev}\n\n"
        return StreamingResponse(_no_files(), media_type="text/event-stream")

    q: asyncio.Queue = event_bus.subscribe(task_id)
    loop = asyncio.get_event_loop()

    should_start = False
    with _proc_lock:
        if not _processing_locks.get(task_id):
            _processing_locks[task_id] = True
            should_start = True

    if should_start:
        # 创建取消事件
        cancel_event = threading.Event()
        _task_cancel_events[task_id] = cancel_event

        def _on_progress(event: dict) -> None:
            event_bus.publish(task_id, event)

        def _run() -> None:
            try:
                pipeline_service.run(task_id, image_paths, _on_progress, enable_thinking=thinking)
            finally:
                with _proc_lock:
                    _processing_locks.pop(task_id, None)
                _task_cancel_events.pop(task_id, None)
                event_bus.cleanup(task_id)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    async def _event_generator():
        init_event = {"type": "init", "task_id": task_id, "num_images": len(image_paths)}
        yield f"event: init\ndata: {json.dumps(init_event, ensure_ascii=False)}\n\n"

        # SSE 心跳间隔（秒）
        heartbeat_interval = 30
        last_heartbeat = time.time()

        while True:
            try:
                # 使用带超时的 get，以便发送心跳
                try:
                    event = await asyncio.wait_for(q.get(), timeout=heartbeat_interval)
                except asyncio.TimeoutError:
                    # 发送心跳
                    now = time.time()
                    if now - last_heartbeat >= heartbeat_interval:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    continue

                ev_type = event.get("type", "message")
                yield f"event: {ev_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

                if event["type"] in ("done", "error"):
                    break
            except asyncio.CancelledError:
                # 客户端断开连接
                event_bus.unsubscribe(task_id, q)
                raise
            except Exception as e:
                # 其他错误，记录并继续
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


@router.delete("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消正在处理的任务。"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    if task["status"] not in ("pending", "processing"):
        return JSONResponse({"error": "任务无法取消（已完成或失败）"}, status_code=400)

    cancel_event = _task_cancel_events.get(task_id)
    if cancel_event:
        cancel_event.set()

    # 更新任务状态
    task_manager.update_task(task_id, status="failed", error_message="用户取消任务")

    # 推送取消事件
    event_bus.publish(task_id, {"type": "error", "message": "任务已被用户取消"})

    return {"status": "ok", "message": "任务取消请求已发送"}


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

    同时检测远程客户端连接：当来自非本地 IP 的客户端（如手机扫码）连接时，
    推送 remote_connected 事件告知桌面端可以隐藏扫码按钮。
    """
    q = event_bus.subscribe_global()

    # 检测远程客户端连接
    client_host = request.client.host if request.client else "unknown"
    lan_ip = _get_lan_ip()
    is_client_remote = client_host not in ("127.0.0.1", "::1", "localhost", lan_ip)
    should_notify_remote = is_client_remote and client_host not in _remote_connected_ips

    if should_notify_remote:
        _remote_connected_ips.add(client_host)

    async def _event_generator():
        # 发送 init 事件确认连接建立
        yield f"event: init\ndata: {json.dumps({'type': 'init', 'message': 'connected'}, ensure_ascii=False)}\n\n"

        # 如果连接来自远程设备，推送全局 remote_connected 事件
        if should_notify_remote:
            rc_event = json.dumps({
                "type": "remote_connected",
                "client_ip": client_host,
            }, ensure_ascii=False)
            yield f"event: remote_connected\ndata: {rc_event}\n\n"
            # 同步发布到全局事件总线，其他已连接的全局 SSE 订阅者也能收到
            event_bus.publish_global({"type": "remote_connected", "client_ip": client_host})

        # SSE 心跳间隔（秒）
        heartbeat_interval = 30
        last_heartbeat = time.time()

        while True:
            try:
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
                event_bus.unsubscribe_global(q)
                raise
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


# ==============================================================================
# 工具 API
# ==============================================================================

def _get_lan_ip() -> str:
    """获取本机局域网 IP，失败则返回 localhost。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


@router.get("/api/qrcode")
async def qr_code(request: Request):
    """生成局域网访问二维码（PNG 图片）。手机扫码即可在同一局域网访问。"""
    lan_ip = _get_lan_ip()
    port = request.url.port or 8000
    url = f"http://{lan_ip}:{port}"
    img = qrcode.make(url, box_size=8, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")
