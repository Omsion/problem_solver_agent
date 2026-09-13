"""
image_grouper.py - 图片分组与处理核心调度器 (V2.2 - 统一客户端版)

本文件是整个自动化Agent的“核心调度器”（Orchestrator）。它的主要职责是：

1.  **时间窗口分组 (Time-based Grouping)**: 监听由 file_monitor.py 传入的新图片事件，
    并使用一个可重置的定时器，将短时间内连续产生的截图智能地归为同一组。这是解决
    “问题太长，一张截图截不完”这一核心痛点的关键。

2.  **并发任务处理 (Concurrent Processing)**: 采用经典的“生产者-消费者”设计模式。
    文件监控器是“生产者”，将图片组任务放入一个线程安全的队列中。多个后台工作线程是
    “消费者”，从队列中取出任务并行处理。这确保了即使同时处理多个问题，系统也能
    保持响应，不会阻塞。

3.  **工作流编排 (Workflow Orchestration)**: 当一个图片组准备好后，它会启动一个
    完整、健壮的处理流水线（_execute_pipeline），该流水线包含问题分类、文字转录、
    文本合并与润色、AI求解、标题生成、结果保存和文件归档等多个步骤。

4.  **健壮性设计 (Robustness)**: 内置了文件锁、失败日志记录和原子化的文件写入等多种
    机制，确保系统在面对文件临时占用、API故障或意外崩溃等情况时，能最大程度地
    保证稳定运行和数据安全。
"""

import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from queue import Queue
from threading import Lock, Thread, Timer, current_thread

# 导入项目模块
from . import config
from .core_pipeline import SolutionPipeline
from .utils import setup_logger

# 初始化全局日志记录器
logger = setup_logger()


class ImageGrouper:
    """
    一个状态化的核心类，用于管理图片的分组、AI处理流水线以及后续的归档工作。
    """

    def __init__(self, num_workers: int | None = None):
        """
        初始化ImageGrouper实例。

        Args:
            num_workers: 后台工作线程数，默认从 config.NUM_WORKERS 读取。
        """
        self.current_group: list[Path] = []
        self.timer: Timer | None = None
        self.lock = Lock()
        self.task_queue = Queue()
        self.num_workers = num_workers if num_workers is not None else config.NUM_WORKERS
        self.workers: list[Thread] = []
        self._start_workers()

    def _start_workers(self):
        """
        私有辅助方法，用于创建并启动所有后台工作线程。
        """
        logger.info(f"正在启动 {self.num_workers} 个后台工作线程...")
        for i in range(self.num_workers):
            # 将工作线程设置为守护线程（daemon=True），这意味着当主程序退出时，
            # 这些线程会自动被终止，避免了程序无法正常退出的问题。
            worker = Thread(target=self._worker_loop, daemon=True, name=f"Worker-{i + 1}")
            worker.start()
            self.workers.append(worker)
        logger.info("后台工作线程已全部启动，等待任务中...")

    def _worker_loop(self):
        """
        每个工作线程（“消费者”）的主循环。
        它会无限循环地从任务队列中获取任务（图片组）并调用处理流程。
        """
        while True:
            group_to_process = self.task_queue.get()
            thread_name = current_thread().name
            logger.info(f"\n{'*' * 50}\n[{thread_name}] 领取新任务，包含 {len(group_to_process)} 张图片。\n{'*' * 50}")
            try:
                self._execute_pipeline(group_to_process)
            except Exception as e:
                # 这是一个终极捕获，防止单个任务的意外失败导致整个工作线程崩溃。
                logger.error(f"[{thread_name}] 处理任务时发生致命的意外错误: {e}", exc_info=True)
            finally:
                # 无论任务成功与否，都必须调用 task_done()。
                # 这会通知队列，该任务的处理已完成，对于队列的管理至关重要。
                self.task_queue.task_done()

    def add_image(self, image_path: Path):
        """
        公开的入口方法（“生产者”），由文件监控器在检测到新图片时调用。
        """
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")
            self.timer = Timer(config.GROUP_TIMEOUT, self._submit_group_to_queue)
            self.timer.start()
    def submit_group(self, image_paths: Sequence[Path]) -> None:
        """把一整组图片**立即**提交到处理队列（供补偿扫描使用）。

        这些文件通常已经躺在目录里很久了，不该再占用一个分组时间窗；同时要
        把它们从"正在收集的组"里剔除，避免同一张图被处理两次。
        """
        group = [Path(p) for p in image_paths]
        if not group:
            return
        with self.lock:
            self.current_group = [p for p in self.current_group if p not in group]
        self.task_queue.put(group)
        logger.info(
            f"补投图片组已直接进入处理队列: {', '.join(p.name for p in group)}"
        )

    def _submit_group_to_queue(self):
        """
        当分组定时器超时后，此方法被调用。它会将收集好的图片组作为一个任务
        放入队列，并清空当前组以便开始下一轮收集。
        """
        with self.lock:
            if not self.current_group:
                return
            group_to_submit = self.current_group.copy()
            self.current_group.clear()
        self.task_queue.put(group_to_submit)
        logger.info(f"超时! 包含 {len(group_to_submit)} 张图片的组已提交到处理队列。")

    # _determine_solver 已替换为 config.determine_solver()

    def _execute_pipeline(self, group_to_process: list[Path]):
        """处理一组图片。

        流水线本体在 `core_pipeline.SolutionPipeline`（与 Web 端共用），
        这里只负责：加锁防重、把事件打到日志、把原图归档到 PROCESSED_DIR。
        旧实现把整条流水线抄了一份，导致与 Web 端行为逐渐分叉。
        """
        thread_name = current_thread().name
        lock_file_path = config.SOLUTION_DIR / f".{group_to_process[0].stem}.lock"

        if lock_file_path.exists():
            logger.warning(f"[{thread_name}] 发现锁文件，跳过处理以防重复: {lock_file_path.name}")
            return

        try:
            lock_file_path.touch()
            logger.info(f"[{thread_name}] 开始处理 {len(group_to_process)} 张图片...")

            def _on_event(event: dict) -> None:
                event_type = event.get("type")
                if event_type == "status":
                    logger.info(f"[{thread_name}] {event.get('message', '')}")
                elif event_type == "done":
                    timings = event.get("timings") or {}
                    logger.info(f"[{thread_name}] 完成，总耗时 {timings.get('total', 0)} ms")
                elif event_type == "cancelled":
                    logger.info(f"[{thread_name}] 已取消")
                elif event_type == "error":
                    logger.error(f"[{thread_name}] 失败: {event.get('message', '')}")

            solution_pipeline = SolutionPipeline(
                solution_dir=config.SOLUTION_DIR,
                on_event=_on_event,
                archive_dir=config.PROCESSED_DIR,
                write_failure_log=True,
            )
            result = solution_pipeline.run(f"cli-{group_to_process[0].stem}", group_to_process)

            if result.get("status") == "completed" and result.get("path"):
                final_path = Path(result["path"])
                logger.info(f"[{thread_name}] 解答已成功保存至: {final_path}")
                self._sync_to_webapp_solutions(final_path)

        except Exception as e:
            # 失败日志与错误事件已由流水线处理，这里只记录线程级异常
            logger.error(f"[{thread_name}] 处理流水线时发生错误: {e}", exc_info=True)
        finally:
            if lock_file_path.exists():
                lock_file_path.unlink()
            logger.info(f"[{thread_name}] 针对组 '{group_to_process[0].name}' 的处理流程结束。")

    @staticmethod
    def _sync_to_webapp_solutions(final_path: Path) -> None:
        """把 CLI 生成的解答同步一份到 webapp/solutions，便于 Web 端历史查看。"""
        try:
            webapp_solution_dir = Path(__file__).resolve().parent.parent / "webapp" / "solutions"
            webapp_solution_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_path, webapp_solution_dir / final_path.name)
            logger.info("解答已同步到 webapp/solutions: %s", final_path.name)
        except Exception as e:
            logger.warning("同步解答到 webapp/solutions 失败: %s", e)
