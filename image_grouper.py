# -*- coding: utf-8 -*-
"""
image_grouper.py - 图片分组与处理核心调度器 (V2.1 - 最终版)

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
    AI求解、结果保存和文件归档等多个步骤。

4.  **健壮性设计 (Robustness)**: 内置了文件锁、API健康检查、失败日志记录和原子化的
    文件写入等多种机制，确保系统在面对文件临时占用、API故障或意外崩溃等情况时，
    能最大程度地保证稳定运行和数据安全。
"""

import shutil
import time
from pathlib import Path
from threading import Timer, Lock, Thread, current_thread
from queue import Queue
from typing import List

# 导入项目模块
import config
import qwen_client
import deepseek_client  # 用于非流式的辅助任务，如文本润色
import solver_client  # 统一的、可切换的流式求解器
from utils import setup_logger, parse_title_from_response, sanitize_filename

# 初始化全局日志记录器
logger = setup_logger()


class ImageGrouper:
    """
    一个状态化的核心类，用于管理图片的分组、AI处理流水线以及后续的归档工作。
    """

    def __init__(self, num_workers: int = 2):
        """
        初始化ImageGrouper实例。

        Args:
            num_workers (int): 要启动的后台工作线程（消费者）的数量。默认为2，
                               可以根据CPU核心数和IO/网络延迟进行调整。
        """
        self.current_group: List[Path] = []  # 临时存储当前正在收集的图片组
        self.timer: Timer | None = None  # 用于实现分组超时的定时器
        self.lock = Lock()  # 线程锁，保护对 self.current_group 的并发访问
        self.task_queue = Queue()  # 核心任务队列，用于解耦文件监控和任务处理
        self.num_workers = num_workers  # 工作线程的数量
        self.workers: List[Thread] = []  # 存储工作线程对象的列表
        self._start_workers()  # 在初始化时直接启动工作线程

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
                # 核心处理逻辑被封装在 _execute_pipeline 方法中
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
        该方法是线程安全的。

        Args:
            image_path (Path): 新检测到的图片的路径。
        """
        with self.lock:
            if self.timer:
                self.timer.cancel()  # 取消旧定时器
            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")
            # 重置一个新的定时器
            self.timer = Timer(config.GROUP_TIMEOUT, self._submit_group_to_queue)
            self.timer.start()

    def _submit_group_to_queue(self):
        """
        当分组定时器超时后，此方法被调用。它会将收集好的图片组作为一个任务
        放入队列，并清空当前组以便开始下一轮收集。该方法是线程安全的。
        """
        with self.lock:
            if not self.current_group:
                return
            group_to_submit = self.current_group.copy()
            self.current_group.clear()
        self.task_queue.put(group_to_submit)
        logger.info(f"超时! 包含 {len(group_to_submit)} 张图片的组已提交到处理队列。")

    def _move_file_with_retry(self, src: Path, dest: Path, retries: int = 3, delay: float = 0.5) -> bool:
        """
        一个健壮的文件移动函数，内置重试逻辑，以应对临时文件锁定问题。
        """
        thread_name = current_thread().name
        for i in range(retries):
            try:
                shutil.move(str(src), str(dest))
                logger.info(f"[{thread_name}] 成功移动 '{src.name}' -> '{dest.parent.name}'")
                return True
            except FileNotFoundError:
                logger.warning(f"[{thread_name}] 移动时未找到 '{src.name}'，可能已被手动处理。")
                return True
            except Exception as e:
                if i < retries - 1:
                    logger.warning(
                        f"[{thread_name}] 移动 '{src.name}' 失败 (尝试 {i + 1}/{retries})，将在 {delay}s 后重试... 错误: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"[{thread_name}] {retries} 次尝试后，移动 '{src.name}' 仍失败。错误: {e}")
                    return False
        return False

    def _is_transcription_valid(self, text: str) -> bool:
        """
        对转录文本进行基本合理性检查，防止将明显错误的OCR结果传递给下游。
        """
        if not text or len(text) < 50:  # 稍微放宽阈值
            logger.error(f"转录质量检测失败: 文本为空或过短 (长度: {len(text)})。")
            return False
        logger.info("转录质量检测通过。")
        return True

    def _write_failure_log(self, group: List[Path], reason: str, transcribed_text: str = "N/A"):
        """
        当处理流程中出现不可恢复的错误时，创建一个详细的失败日志文件。
        """
        thread_name = current_thread().name
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{timestamp}_{group[0].stem}_FAILED.txt"
        failure_path = config.SOLUTION_DIR / filename
        with open(failure_path, 'w', encoding='utf-8') as f:
            f.write(f"Processed on {thread_name}:\n- " + "\n- ".join(p.name for p in group) + "\n\n")
            f.write("=" * 50 + "\nERROR: Processing failed.\n")
            f.write(f"Reason: {reason}\n" + "=" * 50 + "\n\n")
            f.write("Transcribed Text (at point of failure):\n" + transcribed_text)
        logger.info(f"[{thread_name}] 失败日志已保存至: {failure_path}")

    def _write_solution_header(self, f, thread_name: str, group: List[Path], final_problem_type: str,
                               transcribed_text: str):
        """
        DRY (Don't Repeat Yourself) 辅助函数，用于写入标准的解决方案文件头。
        """
        f.write(f"Processed on {thread_name}:\n- " + "\n- ".join(p.name for p in group) + "\n\n")
        f.write("=" * 50 + "\n")
        f.write(f"Detected Problem Type: {final_problem_type}\n")
        f.write(f"Selected Solver: {config.SOLVER_PROVIDER} ({config.SOLVER_MODEL_NAME})\n")
        f.write("=" * 50 + "\n\n")
        f.write("Transcribed Text (Polished):\n" + transcribed_text + "\n\n")
        f.write("=" * 50 + "\n\n")
        style = f"(Style: {config.SOLUTION_STYLE})" if final_problem_type in ["LEETCODE", "ACM"] else ""
        f.write(f"Final Solution {style}:\n")
        f.flush()

    def _execute_pipeline(self, group_to_process: List[Path]):
        """
        封装了完整的AI处理和文件归档流水线，由每个工作线程独立调用。
        """
        thread_name = current_thread().name
        lock_file_path = config.SOLUTION_DIR / f".{group_to_process[0].stem}.lock"
        transcribed_text = "N/A"  # 初始化以备在异常处理中使用

        if lock_file_path.exists():
            logger.warning(f"[{thread_name}] 发现锁文件，跳过处理以防重复: {lock_file_path.name}")
            return

        try:
            lock_file_path.touch()

            # --- 步骤 1: 问题分类 ---
            problem_type = qwen_client.classify_problem_type(group_to_process)
            logger.info(f"[{thread_name}] 初步分类结果: {problem_type}")

            # --- 步骤 2: 根据类型执行不同流程 ---
            if problem_type == "VISUAL_REASONING":
                prompt_template = config.PROMPT_TEMPLATES.get(problem_type)
                if not prompt_template: raise ValueError(f"缺少 {problem_type} 的提示词模板")
                final_answer = qwen_client.solve_visual_problem(group_to_process, prompt_template)
                if not final_answer: raise ValueError("视觉推理模型未能返回有效答案。")

                # 对于非流式任务，直接保存
                title = parse_title_from_response(final_answer)
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                filename = f"{timestamp}_{sanitize_filename(title) if title else group_to_process[0].stem}_result.txt"
                solution_path = config.SOLUTION_DIR / filename
                with open(solution_path, 'w', encoding='utf-8') as f:
                    self._write_solution_header(f, thread_name, group_to_process, problem_type, "N/A (Visual Task)")
                    f.write(final_answer)
                logger.info(f"[{thread_name}] 视觉推理题解答已成功保存至: {solution_path}")

            elif problem_type in ["CODING", "GENERAL", "QUESTION_ANSWERING"]:
                # --- 2.1: 文字转录与润色 ---
                transcribed_text = qwen_client.transcribe_images(group_to_process)
                if not transcribed_text: raise ValueError("文字转录步骤失败。")

                polished_text = deepseek_client.ask_deepseek_for_analysis(
                    config.TEXT_POLISHING_PROMPT.format(merged_text=transcribed_text)
                )
                transcribed_text = polished_text if polished_text else transcribed_text

                if not self._is_transcription_valid(transcribed_text):
                    raise ValueError("转录文本质量检查未通过。")

                # --- 2.2: 流式求解与原子化文件写入 ---
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                temp_solution_path = config.SOLUTION_DIR / f"{timestamp}_{group_to_process[0].stem}_inprogress.txt"

                final_answer = ""
                with open(temp_solution_path, 'w', encoding='utf-8') as f:
                    if problem_type == "CODING":
                        final_problem_type = "LEETCODE" if "leetcode" in transcribed_text.lower() else "ACM"
                        prompt_template = config.PROMPT_TEMPLATES[final_problem_type][config.SOLUTION_STYLE]
                    else:
                        final_problem_type = problem_type
                        prompt_template = config.PROMPT_TEMPLATES.get(problem_type)

                    if not prompt_template: raise ValueError(f"缺少 '{final_problem_type}' 的提示词模板。")

                    final_prompt = prompt_template.format(transcribed_text=transcribed_text)
                    self._write_solution_header(f, thread_name, group_to_process, final_problem_type, transcribed_text)

                    response_stream = solver_client.stream_solve(final_prompt)
                    final_answer_chunks = [chunk for chunk in response_stream]
                    final_answer = "".join(final_answer_chunks)

                    f.write(final_answer)

                if not final_answer.strip() or "--- ERROR ---" in final_answer:
                    raise ValueError("从流式API收到空响应或错误信息。")

                # --- 2.3: 任务成功，重命名文件 ---
                title = parse_title_from_response(final_answer)
                final_filename = f"{timestamp}_{sanitize_filename(title) if title else group_to_process[0].stem}_result.txt"
                final_solution_path = config.SOLUTION_DIR / final_filename
                temp_solution_path.rename(final_solution_path)
                logger.info(f"[{thread_name}] 解答已成功流式保存至: {final_solution_path}")
            else:
                raise ValueError(f"未知的分类结果: {problem_type}")

        except Exception as e:
            logger.error(f"[{thread_name}] 处理流水线时发生错误: {e}", exc_info=True)
            self._write_failure_log(group_to_process, str(e), transcribed_text)
            # 清理可能残留的临时文件
            for f in config.SOLUTION_DIR.glob("*_inprogress.txt"):
                try:
                    f.unlink()
                except OSError:
                    pass
        finally:
            # --- 步骤 3: 清理 ---
            # 无论成功或失败，都移除文件锁并归档图片
            if lock_file_path.exists():
                lock_file_path.unlink()

            logger.info(f"[{thread_name}] 开始归档已处理的图片...")
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            for img_path in group_to_process:
                if img_path.exists():
                    destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                    self._move_file_with_retry(img_path, destination)
            logger.info(f"[{thread_name}] 针对组 '{group_to_process[0].name}' 的处理流程结束。")