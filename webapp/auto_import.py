"""
自动导入监控模块 - 桥接 CLI ImageGrouper 与 Web Pipeline

职责:
1. 复用现有 file_monitor.py 的 watchdog 机制
2. 检测到新截图后，通过 ImageGrouper 时间窗口分组
3. 分组完成后，自动创建 Web Task 并触发流水线
4. 通过 SSE 事件总线推送状态到前端
"""

import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from problem_solver_agent import config as core_config
from problem_solver_agent.file_monitor import start_monitoring
from problem_solver_agent.image_grouper import ImageGrouper
from problem_solver_agent.utils import setup_logger

from . import config as web_config
from .routes import event_bus

logger = setup_logger()

# 全局单例 - 避免多次启动监控
_auto_importer_instance = None
_instance_lock = threading.Lock()


class WebAutoImporter:
    """自动截图导入器 - 连接 CLI 监控与 Web 流水线"""

    def __init__(self, task_manager, pipeline_service):
        self.task_manager = task_manager
        self.pipeline_service = pipeline_service
        self.image_grouper = ImageGrouper(num_workers=1)
        self._observer = None
        self._running = False
        self._lock = threading.Lock()
        self._processing_tasks = set()  # 防重复
        self._started_at: float | None = None
        self._last_group_at: float | None = None
        self._groups_handled = 0

        # 检查是否启用自动导入（通过环境变量控制）
        self._enabled = os.getenv("AUTO_IMPORT_ENABLED", "true").lower() in ("true", "1", "yes")

        # 覆盖 ImageGrouper 的 _execute_pipeline，改为调用 Web 流水线
        self._original_execute = self.image_grouper._execute_pipeline
        self.image_grouper._execute_pipeline = self._web_pipeline_wrapper

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict:
        """对外暴露的监控状态，供 /api/status 与设置页展示。"""
        return {
            "enabled": self._enabled,
            "running": self._running,
            "monitor_dir": str(core_config.MONITOR_DIR),
            "group_timeout": core_config.GROUP_TIMEOUT,
            "started_at": self._started_at,
            "last_group_at": self._last_group_at,
            "groups_handled": self._groups_handled,
            "processing": len(self._processing_tasks),
        }

    def start(self):
        """启动监控"""
        if not self._enabled:
            logger.info("自动导入功能已通过环境变量禁用 (AUTO_IMPORT_ENABLED=false)")
            return

        if self._running:
            return

        # 确保监控目录存在
        core_config.MONITOR_DIR.mkdir(parents=True, exist_ok=True)

        # on_group：补偿扫描（漏事件 / 停机期间到达的文件）整组补投
        self._observer = start_monitoring(
            core_config.MONITOR_DIR,
            self.image_grouper.add_image,
            on_group=self.image_grouper.submit_group,
        )
        self._running = True
        self._started_at = time.time()
        logger.info("Web 自动截图导入已启动，监控目录: %s", core_config.MONITOR_DIR)

    def stop(self):
        """停止监控"""
        if self._observer:
            self._observer.stop()
            self._observer.join()
        self._running = False

    def _web_pipeline_wrapper(self, image_group: list[Path]):
        """包装 ImageGrouper 的执行，调用 Web 流水线"""
        task_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        self._last_group_at = time.time()
        self._groups_handled += 1

        # 防重复检查
        group_key = tuple(sorted(p.name for p in image_group))
        with self._lock:
            if group_key in self._processing_tasks:
                logger.warning("检测到重复任务，跳过: %s", group_key)
                return
            self._processing_tasks.add(group_key)

        try:
            logger.info("自动导入新任务 task=%s, %d 张图片", task_id, len(image_group))

            # 1. 复制图片到 Web 上传目录
            task_dir = web_config.UPLOAD_DIR / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            web_image_paths = []
            for img_path in image_group:
                dest = task_dir / img_path.name
                shutil.copy2(img_path, dest)
                web_image_paths.append(dest)
                logger.info("  已复制: %s -> %s", img_path.name, task_dir.name)

            # 2. 创建 Web 任务
            self.task_manager.create_task(task_id, len(web_image_paths))

            # 3. 推送 SSE 事件通知前端有新任务
            # 先发布到全局事件总线，让前端立即知道有新任务
            event_bus.publish_global({
                "type": "auto_imported",
                "task_id": task_id,
                "num_images": len(web_image_paths),
                "source": "monitor"
            })
            # 同时也发布到该任务的事件总线（用于向后兼容）
            event_bus.publish(task_id, {
                "type": "auto_imported",
                "task_id": task_id,
                "num_images": len(web_image_paths),
                "source": "monitor"
            })
            logger.info("  已推送 auto_imported 事件给前端")

            # 4. 执行 Web 流水线
            def on_progress(event: dict):
                event_bus.publish(task_id, event)

            # 不显式传 enable_thinking：走配置默认（默认不开思考，答案不合格时
            # 由流水线自动升级到"开思考 + 大配额"），避免每次都为思考白等几十秒
            self.pipeline_service.run(
                task_id,
                web_image_paths,
                on_progress,
            )

        except Exception as e:
            logger.error("自动导入任务失败 task=%s: %s", task_id, e, exc_info=True)
            event_bus.publish(task_id, {"type": "error", "message": str(e)})
        finally:
            with self._lock:
                self._processing_tasks.discard(group_key)


def get_auto_importer(task_manager=None, pipeline_service=None) -> WebAutoImporter | None:
    """获取或创建全局单例的自动导入器"""
    global _auto_importer_instance

    with _instance_lock:
        if _auto_importer_instance is None:
            if task_manager is None or pipeline_service is None:
                return None
            _auto_importer_instance = WebAutoImporter(task_manager, pipeline_service)
        return _auto_importer_instance


def start_auto_import(task_manager, pipeline_service) -> WebAutoImporter | None:
    """便捷函数：创建并启动自动导入器"""
    importer = get_auto_importer(task_manager, pipeline_service)
    if importer:
        importer.start()
    return importer


def stop_auto_import() -> None:
    """停止全局自动导入器（应用关闭时调用，避免观察者线程悬挂）。"""
    global _auto_importer_instance
    with _instance_lock:
        importer = _auto_importer_instance
    if importer is not None:
        importer.stop()
