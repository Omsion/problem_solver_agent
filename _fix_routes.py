# -*- coding: utf-8 -*-
"""webapp 路由模块 — 页面路由 + REST API + SSE 流式端点"""

import asyncio
import json
import socket
import threading
import uuid
from io import BytesIO
from pathlib import Path

import qrcode
from fastapi import APIRouter, UploadFile, File, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response

from . import config as web_config

router = APIRouter()

# 由 app.py 在启动时注入
task_manager = None
pipeline_service = None
templates = None


class TaskEventBus:
    """Per-task event bus — 广播进度事件给同一任务的所有 SSE 订阅者。"""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._history: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._queues.setdefault(task_id, []).append(q)
            for event in self._history.get(task_id, []):
                q.put_nowait(event)
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

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            if task_id in self._queues:
                self._queues[task_id] = [x for x in self._queues[task_id] if x is not q]
                if not self._queues[task_id]:
                    del self._queues[task_id]

    def cleanup(self, task_id: str) -> None:
        with self._lock:
            self._queues.pop(task_id, None)
            self._history.pop(task_id, None)


event_bus = TaskEventBus()
_processing_locks: dict[str, bool] = {}
_proc_lock = threading.Lock()


def init_router(tm, ps, tpl):
    global task_manager, pipeline_service, templates
    task_manager = tm
    pipeline_service = ps
    templates = tpl


# ==============================================================================
# 页面路由
# ==============================================================================

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.get("/tasks", response_class=HTMLResponse)
async def history_page(request: Request):
    tasks = task_manager.get_recent_tasks()
    return templates.TemplateResponse(request, "tasks.html", {"tasks": tasks})


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail_page(request: Request, task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        return templates.TemplateResponse(request, "404.html", status_code=404)

    solution_content = ""
    if task["status"] == "completed" and task["solution_path"]:
        sp = Path(task["solution_path"])
        if sp.exists():
            solution_content = sp.read_text(encoding="utf-8")

    return templates.TemplateResponse(request, "task.html", {
        "task": task,
        "solution_content": solution_content,
    })


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

    task_id = uuid.uuid4().hex[:12]
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
async def stream_task(task_id: str):
    """SSE 流式端点 — 启动流水线处理并实时推送进度。"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    # 已完成/失败的任务，直接返回状态
    if task["status"] == "completed":
        async def _done():
            yield f"data: {json.dumps({'type': 'done', 'task_id': task_id, 'filename': task.get('filename', '')})}\n\n"
        return StreamingResponse(_done(), media_type="text/event-stream")

    if task["status"] == "failed":
        async def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': task.get('error_message', '未知错误')})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    # 防止重复处理
    with _proc_lock:
        if _processing_locks.get(task_id):
            async def _dup():
                yield f"data: {json.dumps({'type': 'status', 'phase': 'solving', 'message': '任务已在其他窗口中处理中，实时同步进度…'})}\n\n"
            return StreamingResponse(_dup(), media_type="text/event-stream")
        _processing_locks[task_id] = True

    task_dir = web_config.UPLOAD_DIR / task_id
    image_paths = sorted(task_dir.glob("*"))
    if not image_paths:
        # 上传目录已被清理，任务无法重试，标记为失败
        task_manager.update_task(task_id, status="failed", error_message="上传文件已过期，请重新提交")
        with _proc_lock:
            _processing_locks.pop(task_id, None)
        async def _no_files():
            yield f"data: {json.dumps({'type': 'error', 'message': '上传文件已过期，请重新提交'})}\n\n"
        return StreamingResponse(_no_files(), media_type="text/event-stream")
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _on_progress(event: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, event)

    def _run() -> None:
        try:
            pipeline_service.run(task_id, image_paths, _on_progress)
        finally:
            with _proc_lock:
                _processing_locks.pop(task_id, None)
            # 清理上传目录
            try:
                for p in task_dir.glob("*"):
                    p.unlink()
                task_dir.rmdir()
            except OSError:
                pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    async def _event_generator():
        while True:
            event = await q.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """获取单个任务的详情（含解答内容）。"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    solution_content = ""
    if task["status"] == "completed" and task["solution_path"]:
        sp = Path(task["solution_path"])
        if sp.exists():
            solution_content = sp.read_text(encoding="utf-8")

    return {"task": task, "solution_content": solution_content}


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除任务及其解答文件。"""
    solution_path = task_manager.delete_task(task_id)
    if solution_path:
        try:
            Path(solution_path).unlink(missing_ok=True)
        except OSError:
            pass
    return {"status": "ok"}


@router.get("/api/tasks")
async def list_tasks(limit: int = 100):
    """获取最近的任务列表。"""
    tasks = task_manager.get_recent_tasks(limit=limit)
    return {"tasks": tasks}


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
    """生成局域网访问二维码（PNG）。"""
    lan_ip = _get_lan_ip()
    port = request.url.port or 8000
    url = f"http://{lan_ip}:{port}"
    img = qrcode.make(url, box_size=8, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")
