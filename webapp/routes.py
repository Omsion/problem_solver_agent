"""webapp 路由模块 — REST API + SSE 流式端点"""

import asyncio
import io
import json
import logging
import shutil
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path

import qrcode
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image

from problem_solver_agent import config as core_config
from problem_solver_agent.answer_card import extract_answer_card
from problem_solver_agent.netcheck import get_lan_ip, is_remote_device
from problem_solver_agent.utils import sanitize_filename

from . import config as web_config
from .accounts import AccountManager, BudgetExceededError, User
from .deps import get_current_user
from .jobs import TaskRegistry
from .presence import RemotePresence, watch_connection
from .retention import dir_size_bytes
from .timings import aggregate_timings
from .usage import UsageRecorder

logger = logging.getLogger("WebappRoutes")

router = APIRouter()

# 由 app.py 在启动时注入
task_manager = None
pipeline_service = None
# 用量记账器：只有装配了账户体系才有值。旧装配路径（不传 accounts）下保持 None，
# 路由里的记账与额度校验会整体跳过，行为与改造前一致。
usage_recorder: "UsageRecorder | None" = None


class TaskEventBus:
    """Per-task event bus — 广播进度事件给同一任务的所有 SSE 订阅者。

    事件带自增序号：`publish` 时分配 `event["_id"]`。响应里把它写成 SSE 的
    `id:` 字段，客户端断线重连时浏览器会带 `Last-Event-ID` 请求头，
    `replay_since()` 就能把漏掉的事件补发，避免"重连后中间过程丢失"。
    """

    # 每个任务保留的可回放事件条数上限（覆盖一次断线重连足够）
    REPLAY_LIMIT = 500

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._history: dict[str, list[dict]] = {}
        self._counters: dict[str, int] = {}
        self._global_queues: list[asyncio.Queue] = []
        self._lock = threading.Lock()

    # ---- 序号 ----

    def _assign_id(self, event: dict) -> dict:
        """给事件分配序号（调用方需持有锁）。"""
        task_id = event.get("_task_id")
        counter = self._counters.get(task_id, 0) + 1
        self._counters[task_id] = counter
        event["_id"] = counter
        return event

    @staticmethod
    def event_id(event: dict) -> int:
        return int(event.get("_id") or 0)

    def last_event_id(self, task_id: str) -> int:
        with self._lock:
            return self._counters.get(task_id, 0)

    def replay_since(self, task_id: str, last_event_id: int) -> list[dict]:
        """返回序号大于 `last_event_id` 的事件（用于断线续传）。"""
        if last_event_id <= 0:
            return []
        with self._lock:
            history = list(self._history.get(task_id, []))
        return [event for event in history if self.event_id(event) > last_event_id]

    def subscribe(self, task_id: str, *, replay: bool = True) -> asyncio.Queue:
        """订阅任务事件。

        Args:
            replay: 是否把已有历史事件先灌进队列。断线续传场景应传 False，
                由调用方按 `Last-Event-ID` 精确补发，避免重复。
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._queues.setdefault(task_id, []).append(q)
            if replay:
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
            event = dict(event)  # 不修改调用方持有的对象
            event["_task_id"] = task_id
            self._assign_id(event)
            history = self._history.setdefault(task_id, [])
            history.append(event)
            # 环形上限，防止长任务把内存撑爆
            if len(history) > self.REPLAY_LIMIT:
                del history[: len(history) - self.REPLAY_LIMIT]
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
        """清理任务的事件历史与序号。

        **不会移除已订阅的队列**：订阅者是活着的 SSE 连接，它们还在等
        `q.get()`。早期实现把队列一起删掉，导致流水线结束时的终态事件
        根本投递不到——客户端只能等 30 秒心跳超时。历史与序号则可以清，
        它们的用途只是断线重放，任务结束后重试/重解会从 1 重新开始。
        """
        with self._lock:
            self._history.pop(task_id, None)
            self._counters.pop(task_id, None)


event_bus = TaskEventBus()
# 运行中任务的取消令牌与并发上限统一由 TaskRegistry 管理
tasks_registry = TaskRegistry(max_concurrent=core_config.MAX_CONCURRENT_TASKS)
# 远程手机连接跟踪：只有"断开全部连接"才视为手机离开
remote_presence = RemotePresence()

# 任务终态：到达这些状态后不再启动流水线
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# 会让 SSE 生成器结束的事件
TERMINAL_EVENTS = ("done", "error", "cancelled")


def init_router(tm, ps, accounts: "AccountManager | None" = None):
    """注入路由依赖。

    Args:
        accounts: 账户管理器。传入时同时构造用量记账器；为了让"只传前两个参数"
            的旧装配路径继续可用，这里保留默认值 None（此时跳过额度校验与记账）。
    """
    global task_manager, pipeline_service, usage_recorder
    task_manager = tm
    pipeline_service = ps
    # 多实例（例如测试里反复 create_app）时按最新注入重建，避免用到上一位的账户库
    usage_recorder = UsageRecorder(accounts) if accounts is not None else None


def _visible_user_id(user: User) -> str | None:
    """返回归属过滤条件：普通用户只能看自己的任务，管理员可以看全部。

    这里刻意返回 None（而不是空串）来表示"不过滤"：TaskManager 各方法用
    `user_id is None` 区分"不过滤"与"过滤到某个具体用户"，空串会变成一次
    永远匹配不到任何行的查询。
    """
    return None if user.is_admin else user.id


def _get_visible_task(task_id: str, user: User) -> dict | None:
    """按当前身份取任务。

    不属于该用户时返回 None，调用方统一按"任务不存在"给出 404：
    用 403 会暴露"这个 task_id 确实存在"，等于把别人的任务 ID 变成可探测的。
    """
    return task_manager.get_task(task_id, user_id=_visible_user_id(user))


def _check_budget(user: User, pages: int) -> JSONResponse | None:
    """提交/重解前的额度预检。

    Returns:
        额度不足时返回 402 响应；通过（或账户体系未装配）时返回 None。
    """
    if usage_recorder is None:
        # 账户体系没装配（旧装配路径或未初始化）：不校验，宁可放行也不要 500
        return None
    if user.is_admin:
        # 管理员（含 AUTH_ENABLED=false 时的内置本地用户）不受额度限制，
        # 与任务可见性上的管理员例外保持一致：单人自用模式不能被额度拦住。
        return None
    accounts = usage_recorder.accounts
    if accounts is None:
        return None
    estimated = usage_recorder.estimate_task_cost(max(0, pages))
    required = max(estimated, web_config.MIN_TASK_BUDGET)
    try:
        accounts.check_budget(user.id, required)
    except BudgetExceededError as exc:
        return JSONResponse(
            {
                "error": {
                    "code": "insufficient_budget",
                    "message": str(exc),
                    "remaining": exc.remaining,
                    "required": exc.required,
                }
            },
            status_code=402,
        )
    return None


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


def _sse_frame(event: dict) -> str:
    """把事件字典序列化成一条完整的 SSE 帧（含 id 与 event 名）。

    内部字段（下划线开头，如 `_id` / `_task_id`）只用于服务端排序与去重，
    不下发给前端。
    """
    payload = {k: v for k, v in event.items() if not k.startswith("_")}
    event_type = payload.get("type", "message")
    lines = []
    event_id = event_bus.event_id(event)
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def _parse_last_event_id(request: Request) -> int:
    """读取 `Last-Event-ID` 请求头（浏览器重连时自动携带）。"""
    raw = request.headers.get("last-event-id")
    if not raw:
        return 0
    try:
        return max(0, int(raw.strip()))
    except (ValueError, AttributeError):
        return 0


def _terminal_fallback(task_id: str) -> dict | None:
    """SSE 等待超时时回查任务终态，用于兜底补发结论。

    为什么需要：事件是"推"给当时已订阅的队列的。如果流水线线程先跑完并
    `cleanup`，订阅者可能一条终态事件都收不到；客户端就会一直等到心跳超时。
    这时直接读数据库给出结论，比让用户干等要好。
    """
    task = task_manager.get_task(task_id)
    if not task:
        return None
    status = task.get("status")
    if status == "completed":
        return {
            "type": "done",
            "task_id": task_id,
            "filename": task.get("filename", ""),
            "timings": task.get("timings"),
        }
    if status == "cancelled":
        return {"type": "cancelled", "task_id": task_id, "message": "任务已取消"}
    if status == "failed":
        return {
            "type": "error",
            "task_id": task_id,
            "message": task.get("error_message") or "任务失败",
        }
    return None


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
async def create_task(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
):
    """上传图片，创建任务，返回 task_id。

    安全要点：绝不直接使用客户端提供的文件名拼接路径。旧实现写成
    `task_dir / f.filename`，`../../.env` 这样的文件名可以覆盖任意文件。
    现在只取 basename、清理非法字符、同名自动加序号，并校验文件确实是图片。
    """
    if not files:
        return JSONResponse({"error": "请至少上传一张图片"}, status_code=400)

    # 额度预检放在读文件之前：跑一次视觉+求解是要真花钱的，
    # 余额不足就该在落盘之前拒绝，避免"上传完了才说没额度"。
    denied = _check_budget(user, len(files))
    if denied is not None:
        return denied

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

    # 记录归属：多用户隔离的根基，后续所有查询都靠 user_id 过滤
    task_manager.create_task(
        task_id, len(image_paths), user_id=user.id, tenant_id=user.tenant_id
    )
    return {"task_id": task_id, "num_images": len(image_paths)}


@router.get("/api/tasks/{task_id}/stream")
async def stream_task(
    request: Request,
    task_id: str,
    thinking: bool = False,
    style: str | None = None,
    user: User = Depends(get_current_user),
):
    """SSE 流式端点 — 启动流水线处理并实时推送进度。

    Query Parameters:
        thinking: 设为 True 启用求解器思考模式（DeepSeek reasoning），
                  思考过程以 type="reasoning" 事件独立推送。
        style: 编程题求解风格（OPTIMAL / EXPLORATORY），留空用全局配置。

    断线续传：响应中的每条事件都带 `id:`。浏览器重连 EventSource 时会自动带
    `Last-Event-ID` 请求头，此时服务端只补发该序号之后的事件，避免重连后
    丢失中间过程或重复渲染已收到的内容。
    """
    task = _get_visible_task(task_id, user)
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

    # 断线续传：浏览器重连时会自动带上 Last-Event-ID
    last_event_id = _parse_last_event_id(request)
    missed = event_bus.replay_since(task_id, last_event_id) if last_event_id else []

    # 先订阅、后启动流水线（顺序很关键，见 _event_generator 内的注释）。
    # replay=False：历史事件由下面按 `missed` 精确补发，避免重复注入。
    q: asyncio.Queue = event_bus.subscribe(task_id, replay=False)

    def _on_progress(event: dict) -> None:
        # usage 事件是"内部账目"：它既没有前端用途，也不属于既有的 SSE 契约，
        # 因此只落库、不下发（继续 publish 会让前端收到未知事件类型）。
        if event.get("type") == "usage":
            if usage_recorder is not None:
                # UsageRecorder.record 内部吞异常：记账失败绝不能影响解题
                usage_recorder.record(
                    user_id=user.id,
                    task_id=task_id,
                    report=event,
                    tenant_id=user.tenant_id,
                )
            return
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
            # 兜底补发终态事件：如果流水线因异常没发出 done/error/cancelled，
            # 订阅者会一直等到心跳超时。这里按数据库状态补一条，保证流能结束。
            try:
                fallback = _terminal_fallback(task_id)
                if fallback is not None:
                    event_bus.publish(task_id, fallback)
            except Exception:
                pass
            event_bus.cleanup(task_id)

    def _work() -> None:
        tasks_registry.run_with_slot(task_id, _run)

    # 先订阅、再启动流水线：订阅关系必须在流水线发布任何事件之前建立，
    # 否则终态事件可能落在没有订阅者的窗口里。
    threading.Thread(target=_work, daemon=True).start()

    async def _event_generator():
        try:
            if last_event_id:
                # 续传：先把客户端漏掉的事件补发，再接着推实时事件
                for event in missed:
                    yield _sse_frame(event)
                    if event.get("type") in TERMINAL_EVENTS:
                        return
            else:
                init_event = {"type": "init", "task_id": task_id, "num_images": len(image_paths)}
                event_bus.publish(task_id, init_event)
                yield _sse_frame(init_event)

            heartbeat_interval = 30
            last_heartbeat = time.time()

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=heartbeat_interval)
                except asyncio.TimeoutError:
                    # 保险绳：流水线已结束但终态事件没能送到这个队列时
                    # （线程完成早于订阅、或事件在 cleanup 时被丢弃），
                    # 客户端不应该干等 30 秒。这里回查数据库，直接把结论补上。
                    fallback = _terminal_fallback(task_id)
                    if fallback is not None:
                        yield _sse_frame(fallback)
                        break

                    now = time.time()
                    if now - last_heartbeat >= heartbeat_interval:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    continue

                # 续传时已补发过的事件可能仍在队列里，按序号去重
                if last_event_id and event_bus.event_id(event) <= last_event_id:
                    continue

                yield _sse_frame(event)

                if event.get("type") in TERMINAL_EVENTS:
                    break
        except asyncio.CancelledError:
            # 客户端断开：不取消任务，只停止推送
            raise
        finally:
            event_bus.unsubscribe(task_id, q)

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


@router.post("/api/tasks/{task_id}/cancel")
@router.delete("/api/tasks/{task_id}/cancel")  # 兼容旧前端
async def cancel_task(task_id: str, user: User = Depends(get_current_user)):
    """取消正在处理的任务。

    与旧实现的区别：
    - 真的会让流水线停下来（令牌在阶段边界与流式分片之间被检查）
    - 立即把状态置为 `cancelled`，并在运行中的线程结束时由流水线写入部分内容
    - 不再因为 `_processing_locks` 未释放而导致"取消后无法重试"
    """
    task = _get_visible_task(task_id, user)
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
async def retry_task(
    task_id: str,
    thinking: bool | None = None,
    user: User = Depends(get_current_user),
):
    """重试失败或已取消的任务。

    会复用阶段缓存（分类/转录结果），因此重试不会重复消耗视觉模型额度。

    `thinking` 留空（None）= 用配置默认值（默认不首选思考，答案不合格时自动升级）。
    """
    task = _get_visible_task(task_id, user)
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


@router.post("/api/tasks/{task_id}/resolve")
async def resolve_task(
    task_id: str,
    thinking: bool | None = None,
    style: str | None = None,
    user: User = Depends(get_current_user),
):
    """换路重解：复用已识别的题目文本，只重跑求解。

    适用场景：第一版答案不满意，想换求解风格（OPTIMAL / EXPLORATORY）、
    开关思考模式，或换个模型再要一版。**跳过分类与 OCR**，
    因此只有一次求解调用，比重新走完整流水线快得多。

    `thinking` 留空（None）= 用配置默认值；显式 true/false 优先（网页上的
    "开启/关闭思考模式重解"就是这么传的）。
    """
    task = _get_visible_task(task_id, user)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    if tasks_registry.is_running(task_id):
        return JSONResponse({"error": "任务正在处理中", "code": "already_running"}, status_code=409)

    if style and style.upper() not in ("OPTIMAL", "EXPLORATORY"):
        return JSONResponse({"error": "style 只能是 OPTIMAL 或 EXPLORATORY"}, status_code=400)

    # 需要已识别的题目文本：优先取解答文件里的记录，其次取阶段缓存
    transcribed_text = ""
    solution_path = task.get("solution_path") or ""
    if solution_path and Path(solution_path).exists():
        transcribed_text = pipeline_service.extract_problem_text(Path(solution_path))

    if not transcribed_text:
        cached = task_manager.get_cached_stage(task_id, "vision")
        pages = cached.get("pages") if isinstance(cached, dict) else None
        if pages:
            transcribed_text = "\n---[NEXT]---\n".join(str(p).strip() for p in pages)

    if not transcribed_text:
        return JSONResponse(
            {"error": "找不到已识别的题目文本，请先完整处理一次", "code": "no_transcript"},
            status_code=409,
        )

    task_dir = web_config.UPLOAD_DIR / task_id
    image_paths = _task_images(task_dir)
    problem_type = (task.get("problem_type") or "GENERAL").strip()

    # 图形推理题依赖原图；其它题型只要有文本就能重解
    if problem_type == "VISUAL_REASONING" and not image_paths:
        return JSONResponse(
            {"error": "图形推理题需要原图，但原图已被清理", "code": "upload_expired"},
            status_code=410,
        )

    # 换路重解同样会调用付费模型，所以和 create_task 一样先做额度预检。
    # 放在这里是因为此时才知道页数（阶段缓存里的题面可能有若干页）。
    denied = _check_budget(user, max(len(image_paths), 1))
    if denied is not None:
        return denied

    task_manager.update_task(task_id, status="pending", error_message="")
    event_bus.cleanup(task_id)

    def _on_progress(event: dict) -> None:
        # 同 stream_task：usage 只记账，不下发给前端（见那里的说明）
        if event.get("type") == "usage":
            if usage_recorder is not None:
                usage_recorder.record(
                    user_id=user.id,
                    task_id=task_id,
                    report=event,
                    tenant_id=user.tenant_id,
                )
            return
        event_bus.publish(task_id, event)

    def _run(token) -> None:
        try:
            pipeline_service.resolve(
                task_id,
                image_paths,
                _on_progress,
                problem_type=problem_type,
                transcribed_text=transcribed_text,
                enable_thinking=thinking,
                style=style,
                cancel=token,
            )
        except Exception:
            pass
        finally:
            event_bus.cleanup(task_id)

    threading.Thread(
        target=lambda: tasks_registry.run_with_slot(task_id, _run),
        daemon=True,
    ).start()

    return {
        "status": "ok",
        "task_id": task_id,
        "style": style or core_config.SOLUTION_STYLE,
        "thinking": thinking,
        "reused_transcript": True,
    }


@router.post("/api/tasks/{task_id}/verify")
async def verify_task(task_id: str, model: str | None = None, user: User = Depends(get_current_user)):
    """核对已完成的解答（默认关闭的可选功能）。

    用第二个视觉模型对照原图复核答案，结果追加到解答文件并返回结构化结论。
    核对不覆盖已有解答，失败也不影响原结果。
    """
    task = _get_visible_task(task_id, user)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    if task["status"] not in ("completed", "cancelled"):
        return JSONResponse(
            {"error": "只有已完成或已取消的任务可以核对", "code": "not_verifiable"},
            status_code=400,
        )

    answer_text = ""
    solution_path = task.get("solution_path") or ""
    if solution_path and Path(solution_path).exists():
        try:
            answer_text = Path(solution_path).read_text(encoding="utf-8")
        except OSError as exc:
            return JSONResponse({"error": f"读取解答失败: {exc}"}, status_code=500)

    if not answer_text.strip():
        return JSONResponse({"error": "解答内容为空，无法核对", "code": "empty_answer"}, status_code=409)

    task_dir = web_config.UPLOAD_DIR / task_id
    image_paths = _task_images(task_dir)
    if not image_paths:
        return JSONResponse(
            {"error": "原图已被清理，无法核对", "code": "upload_expired"},
            status_code=410,
        )

    event_bus.publish(task_id, {"type": "status", "phase": "verifying", "message": "正在核对答案…"})
    try:
        result = await asyncio.to_thread(pipeline_service.verify, task_id, image_paths, answer_text)
    except Exception as exc:
        logger.error("核对失败 task=%s: %s", task_id, exc, exc_info=True)
        event_bus.publish(task_id, {"type": "status", "phase": "verifying", "message": "核对失败"})
        return JSONResponse({"error": f"核对失败: {exc}"}, status_code=500)

    # 标记已核对（放在这里而不是 service 内，便于统计与幂等重试）
    task_manager.update_task(task_id, verified=1)

    payload = result.to_dict()
    event_bus.publish(task_id, {"type": "verified", "task_id": task_id, "verification": payload})
    return {"status": "ok", "task_id": task_id, "verification": payload}


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str, user: User = Depends(get_current_user)):
    """获取单个任务的详情（含解答内容和图片 URL）。

    任务不属于当前用户时按 404 处理而不是 403：403 等于承认"这个 id 存在"，
    会把任务 id 变成可枚举探测的信息。
    """
    task = _get_visible_task(task_id, user)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    solution_content = ""
    if task["status"] == "completed" and task["solution_path"]:
        sp = Path(task["solution_path"])
        if sp.exists():
            solution_content = sp.read_text(encoding="utf-8")

    # 答案卡：与 SSE `done` 事件同源，避免前端在整篇文件上自己"猜答案"
    # （那会把 YAML frontmatter 和「题目文本」当成最终答案显示出来）。
    answer_card = None
    if solution_content.strip():
        extracted = extract_answer_card(solution_content)
        # 抽不出正文时宁可不给卡：空卡片会把完整解答折叠起来，用户反而什么都看不到
        if extracted.get("text", "").strip():
            answer_card = extracted
    if answer_card is None and (task.get("answer_card") or "").strip():
        # 解答文件已被清理时的兜底：用流水线落库的卡片文本
        answer_card = {
            "text": task["answer_card"].strip(),
            "extracted": True,
            "section": None,
            "truncated": False,
        }

    # 列出已上传的图片 URL
    image_urls: list[str] = []
    task_dir = web_config.UPLOAD_DIR / task_id
    if task_dir.exists():
        image_urls = [f"/uploads/{task_id}/{p.name}" for p in sorted(task_dir.glob("*")) if p.is_file()]

    return {
        "task": task,
        "solution_content": solution_content,
        "image_urls": image_urls,
        "answer_card": answer_card,
    }


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, user: User = Depends(get_current_user)):
    """删除任务及其解答文件和上传图片。

    先按身份确认可见性再删：直接 delete 只能靠返回的 solution_path 是否为空
    来判断"有没有删到"，而且会真的去删别人的文件目录。
    """
    if _get_visible_task(task_id, user) is None:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    solution_path = task_manager.delete_task(task_id, user_id=_visible_user_id(user))
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
async def list_tasks(limit: int = 100, user: User = Depends(get_current_user)):
    """获取最近的任务列表。

    普通用户只能看到自己的任务；管理员（user_id=None）可以看到全部。
    """
    tasks = task_manager.get_recent_tasks(limit=limit, user_id=_visible_user_id(user))
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
async def system_status(user: User = Depends(get_current_user)):
    """系统运行状态：自动截图监控是否在跑、监控目录、磁盘占用、远程连接。

    需要身份：磁盘占用与监控目录属于部署内部信息，不该对匿名请求开放。
    """
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
async def aggregate_stats(limit: int = 50, user: User = Depends(get_current_user)):
    """最近若干任务的阶段耗时与缓存命中统计，用于回答"到底慢在哪"。

    普通用户只统计自己的任务，避免从聚合结果里反推出别人的题量与耗时。
    """
    rows = task_manager.list_timings(
        limit=max(1, min(limit, 200)), user_id=_visible_user_id(user)
    )
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
