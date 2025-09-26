# -*- coding: utf-8 -*-
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
from pathlib import Path
from threading import Timer, Lock, Thread, current_thread
from queue import Queue
from typing import List

# 导入项目模块
import config
import qwen_client
import solver_client  # 统一的、可切换的流式与非流式求解器
from utils import setup_logger, sanitize_filename, extract_question_numbers, format_number_prefix

# 初始化全局日志记录器
logger = setup_logger()


class ImageGrouper:
    """
    一个状态化的核心类，用于管理图片的分组、AI处理流水线以及后续的归档工作。
    """

    def __init__(self, num_workers: int = 4):
        """
        初始化ImageGrouper实例。

        Args:
            num_workers (int): 要启动的后台工作线程（消费者）的数量。默认为2。
        """
        self.current_group: List[Path] = []
        self.timer: Timer | None = None
        self.lock = Lock()
        self.task_queue = Queue()
        self.num_workers = num_workers
        self.workers: List[Thread] = []
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
            if self.timer: self.timer.cancel()
            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")
            self.timer = Timer(config.GROUP_TIMEOUT, self._submit_group_to_queue)
            self.timer.start()

    def _submit_group_to_queue(self):
        """
        当分组定时器超时后，此方法被调用。它会将收集好的图片组作为一个任务
        放入队列，并清空当前组以便开始下一轮收集。
        """
        with self.lock:
            if not self.current_group: return
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
        对转录和合并后的文本进行基本合理性检查。
        """
        if not text or len(text) < 50:
            logger.error(f"转录/合并后文本质量检测失败: 文本为空或过短 (长度: {len(text)})。")
            return False
        logger.info("转录/合并后文本质量检测通过。")
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
        f.write(f"Auxiliary Model: {config.AUX_PROVIDER} ({config.AUX_MODEL_NAME})\n")
        f.write("=" * 50 + "\n\n")
        f.write("Transcribed & Polished Text:\n" + transcribed_text + "\n\n")
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
        transcribed_text = "N/A"
        final_answer = ""
        # 初始化一个临时路径，以便在异常处理中也能访问到
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        temp_solution_path = config.SOLUTION_DIR / f"{timestamp}_{group_to_process[0].stem}_inprogress.txt"

        if lock_file_path.exists():
            logger.warning(f"[{thread_name}] 发现锁文件，跳过处理以防重复: {lock_file_path.name}")
            return

        try:
            lock_file_path.touch()  # 创建锁文件，表示处理开始

            # --- 步骤 1: 问题分类 ---
            problem_type = qwen_client.classify_problem_type(group_to_process)
            logger.info(f"[{thread_name}] 初步分类结果: {problem_type}")

            # --- 步骤 2: 文本化 (Textualization) ---
            # 对于所有需要文本理解的任务，都需要先将图片内容转化为高质量文本。
            if problem_type in ["CODING", "GENERAL", "QUESTION_ANSWERING", "VISUAL_REASONING"]:
                # 2.1 并行、独立地转录每张图片
                raw_transcriptions = qwen_client.transcribe_images_raw(group_to_process)
                if not raw_transcriptions: raise ValueError("独立文字转录步骤返回了空结果。")

                # 2.2 使用强大的LLM进行智能合并与润色
                raw_texts_joined = "\n---[NEXT]---\n".join(raw_transcriptions)
                merge_polish_prompt = config.TEXT_MERGE_AND_POLISH_PROMPT.format(raw_texts=raw_texts_joined)
                polished_text = solver_client.ask_for_analysis(
                    merge_polish_prompt,
                    provider=config.AUX_PROVIDER,
                    model=config.AUX_MODEL_NAME
                )
                if not polished_text: raise ValueError("LLM合并与润色步骤失败。")
                transcribed_text = polished_text

                if not self._is_transcription_valid(transcribed_text): raise ValueError("合并后的文本质量检查未通过。")

            # --- 步骤 3: 核心求解 (Core Solving) ---
            # 使用原子化写入模式：先写入临时文件
            with open(temp_solution_path, 'w', encoding='utf-8') as f:
                # 统一处理所有任务的流式响应
                if problem_type == "VISUAL_REASONING":
                    # 调用专用的视觉推理函数
                    final_problem_type = problem_type
                    self._write_solution_header(f, thread_name, group_to_process, final_problem_type, transcribed_text)
                    response_stream = qwen_client.solve_visual_reasoning_problem(group_to_process)

                else:  # CODING, GENERAL, QUESTION_ANSWERING, MULTIPLE_CHOICE
                    if problem_type == "CODING":
                        final_problem_type = "LEETCODE" if "leetcode" in transcribed_text.lower() else "ACM"
                        prompt_template = config.PROMPT_TEMPLATES[final_problem_type][config.SOLUTION_STYLE]

                    elif problem_type == "MULTIPLE_CHOICE":
                        final_problem_type = problem_type
                        prompt_template = config.PROMPT_TEMPLATES.get(problem_type)

                    else: # GENERAL, QUESTION_ANSWERING
                        final_problem_type = problem_type
                        prompt_template = config.PROMPT_TEMPLATES.get(problem_type)

                    if not prompt_template: raise ValueError(f"缺少 '{final_problem_type}' 的提示词模板。")

                    self._write_solution_header(f, thread_name, group_to_process, final_problem_type, transcribed_text)

                    final_solve_prompt = prompt_template.format(transcribed_text=transcribed_text)
                    response_stream = solver_client.stream_solve(final_solve_prompt)

                if not response_stream:
                    raise ValueError(f"未能从 {problem_type} 求解器获取响应流。")

                final_answer_chunks = [chunk for chunk in response_stream]
                final_answer = "".join(final_answer_chunks)
                f.write(final_answer)

            if not final_answer.strip() or "--- ERROR ---" in final_answer:
                raise ValueError(f"从核心求解器收到空响应或错误信息: {final_answer}")

            #  采用由LLM直接驱动的智能文件名生成流程
            logger.info("开始通过LLM生成智能文件名...")
            filename_gen_prompt = config.FILENAME_GENERATION_PROMPT.format(transcribed_text=transcribed_text)

            # 调用辅助模型来生成文件名
            filename_body = solver_client.ask_for_analysis(
                filename_gen_prompt,
                provider=config.AUX_PROVIDER,
                model=config.AUX_MODEL_NAME
            )

            # 提供回退机制，确保总能生成一个有效的文件名
            if not filename_body:
                logger.warning("LLM未能生成文件名，将使用回退机制。")
                # 尝试使用旧的、基于正则表达式的简单方法作为最后的防线
                numbers = extract_question_numbers(transcribed_text)
                number_prefix = format_number_prefix(numbers)
                fallback_topic = f"{final_problem_type}_Solution"
                filename_body = f"{number_prefix}_{fallback_topic}" if number_prefix else fallback_topic

            logger.info(f"最终生成文件名主体: '{filename_body}'")

            final_filename = f"{timestamp}_{sanitize_filename(filename_body)}.txt"
            final_solution_path = config.SOLUTION_DIR / final_filename
            temp_solution_path.rename(final_solution_path)
            logger.info(f"[{thread_name}] 解答已成功保存至: {final_solution_path}")

        except Exception as e:
            logger.error(f"[{thread_name}] 处理流水线时发生错误: {e}", exc_info=True)
            self._write_failure_log(group_to_process, str(e), transcribed_text)
            # 清理可能残留的临时文件，防止留下垃圾文件
            if temp_solution_path.exists():
                try:
                    temp_solution_path.unlink()
                except OSError:
                    pass
        finally:
            # --- 步骤 6: 清理 ---
            # 无论成功或失败，都移除文件锁并归档图片
            if lock_file_path.exists(): lock_file_path.unlink()

            logger.info(f"[{thread_name}] 开始归档已处理的图片...")
            for img_path in group_to_process:
                if img_path.exists():
                    destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                    self._move_file_with_retry(img_path, destination)
            logger.info(f"[{thread_name}] 针对组 '{group_to_process[0].name}' 的处理流程结束。")