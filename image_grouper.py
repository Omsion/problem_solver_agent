# -*- coding: utf-8 -*-
"""
image_grouper.py - 图片分组处理器模块 (最终健壮版)

本文件是整个自动化Agent的核心调度器（Orchestrator）。它的主要职责包括：

1.  **时间窗口分组**: 监听新图片事件并分组。
2.  **并发安全**: 使用线程锁保证数据安全。
3.  **防重复处理**: 使用锁文件机制。
4.  **智能工作流**: 包含多层防御机制：
    a. (Qwen-VL) 问题分类。
    b. (Agent) 转录质量检测：在调用昂贵的求解模型前，对OCR结果进行基本合理性检查。
    c. (AI Models) 问题求解：采用“单次调用链式思考”策略，并引导模型容忍OCR小错误。
5.  **健壮的文件操作与错误记录**: 带重试的文件移动，并在失败时生成详细的日志文件。
6.  **并行任务处理**: 使用命名工作线程和任务队列，日志清晰可追溯。
"""

import shutil
import time
from pathlib import Path
# 导入threading以获取当前线程信息，用于增强日志
from threading import Timer, Lock, Thread, current_thread
from queue import Queue
from typing import List

import config
import qwen_client
import deepseek_client
from utils import setup_logger, parse_title_from_response, sanitize_filename

logger = setup_logger()


class ImageGrouper:
    """
    一个状态化的核心类，用于管理图片的分组、AI处理流水线以及后续的归档工作。
    本版本采用任务队列和命名工作线程，以支持稳定、并行的多任务处理。
    """

    def __init__(self, num_workers=2):
        self.current_group: List[Path] = []
        self.timer: Timer | None = None
        self.lock = Lock()
        self.task_queue = Queue()
        self.num_workers = num_workers
        self.workers: List[Thread] = []
        self._start_workers()

    def _start_workers(self):
        """
        私有辅助方法，在类实例化时，初始化并启动所有后台工作线程。
        为每个线程命名，极大地方便了并发场景下的日志追溯和调试。
        """
        logger.info(f"正在启动 {self.num_workers} 个后台工作线程...")
        for i in range(self.num_workers):
            worker = Thread(target=self._worker_loop, daemon=True, name=f"Worker-{i + 1}")
            worker.start()
            self.workers.append(worker)
        logger.info("后台工作线程已全部启动，等待任务中...")

    def _worker_loop(self):
        """
        每个工作线程（“消费者”）的主循环。
        无限循环地从任务队列中获取任务并执行处理流程。
        """
        while True:
            group_to_process = self.task_queue.get()
            # 获取当前线程名，用于日志记录
            thread_name = current_thread().name
            logger.info(f"\n **********************************"
                        f"[{thread_name}] 领取了一个新任务，包含 {len(group_to_process)} 张图片。"
                        f" **********************************")
            try:
                self._execute_pipeline(group_to_process)
            except Exception as e:
                # 捕获所有未预期的异常，防止单个任务失败导致整个线程崩溃
                logger.error(f"[{thread_name}] 处理任务时发生致命的意外错误: {e}", exc_info=True)
            finally:
                # 确保即使发生异常，任务也能被标记为完成，防止队列阻塞
                self.task_queue.task_done()

    def add_image(self, image_path: Path):
        """
        公开的入口方法（“生产者”），由文件监控器在检测到新图片时调用。
        使用线程锁确保对共享资源 self.current_group 的访问是原子性的。
        """
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")
            self.timer = Timer(config.GROUP_TIMEOUT, self._submit_group_to_queue)
            self.timer.start()

    def _submit_group_to_queue(self):
        """
        当分组定时器超时后，此方法被调用，将收集好的图片组作为一个任务放入队列。
        """
        with self.lock:
            if not self.current_group:
                return
            group_to_submit = self.current_group.copy()
            self.current_group.clear()
        self.task_queue.put(group_to_submit)
        logger.info(f"超时! 包含 {len(group_to_submit)} 张图片的组已提交到处理队列。")

    def _move_file_with_retry(self, src: Path, dest: Path, retries=3, delay=0.5):
        """
        一个健壮的私有辅助方法，用于移动文件，并在失败时进行重试。
        这能有效应对因云同步、杀毒软件等导致的临时性文件锁定问题。
        """
        thread_name = current_thread().name
        for i in range(retries):
            try:
                shutil.move(str(src), str(dest))
                logger.info(f"[{thread_name}] 成功移动 '{src.name}' 到 '{dest.parent.name}' 文件夹。")
                return True
            except FileNotFoundError:
                logger.warning(f"[{thread_name}] 无法找到 '{src.name}'。可能已被手动处理。")
                return True
            except Exception as e:
                if i < retries - 1:
                    logger.warning(
                        f"[{thread_name}] 移动 '{src.name}' 失败 (尝试 {i + 1}/{retries})。将在 {delay}s 后重试... 错误: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"[{thread_name}] 在 {retries} 次尝试后，移动 '{src.name}' 仍然失败。错误: {e}")
                    return False

    def _is_transcription_valid(self, text: str) -> bool:
        """
        对转录的文本进行基本的合理性检查（防火墙）。
        此检查被设计得相对宽松，只拦截灾难性的OCR失败（如完全为空或过短），
        因为下游的DeepSeek模型有很强的上下文推理和纠错能力，可以修复小瑕疵。
        """
        thread_name = current_thread().name
        if not text or len(text) < 100:  # 编程题的单张图片内容完整描述通常远超100字符
            logger.error(
                f"[{thread_name}] 转录质量检测失败: 文本为空或过短 (长度: {len(text)})。这表明OCR可能已完全失败。")
            return False

        logger.info(f"[{thread_name}] 转录质量检测通过。")
        return True

    def _write_failure_log(self, group: List[Path], reason: str, transcribed_text: str = "N/A"):
        """
        当流程中出现不可恢复的错误时，写入一个包含错误信息的日志文件，
        方便用户进行事后分析。
        """
        thread_name = current_thread().name
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        base_name = group[0].stem
        filename = f"{timestamp}_{base_name}_FAILED.txt"
        failure_path = config.SOLUTION_DIR / filename

        with open(failure_path, 'w', encoding='utf-8') as f:
            f.write(f"Processed Image Group on {thread_name}:\n- " + "\n- ".join(p.name for p in group) + "\n\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"ERROR: Processing failed.\n")
            f.write(f"Reason: {reason}\n\n")
            f.write("=" * 50 + "\n\n")
            f.write("Transcribed Text (at the point of failure):\n")
            f.write(transcribed_text)

        logger.info(f"[{thread_name}] 失败日志已保存至: {failure_path}")

    def _execute_pipeline(self, group_to_process: List[Path]):
        """
        封装了完整的AI处理和归档流水线，由每个工作线程独立调用。
        这是一个包含多层防御的健壮工作流。
        """
        thread_name = current_thread().name
        lock_file_name = f".{group_to_process[0].stem}.lock"
        lock_file_path = config.SOLUTION_DIR / lock_file_name
        if lock_file_path.exists():
            logger.warning(f"[{thread_name}] 发现锁文件，跳过处理: {lock_file_name}")
            return

        try:
            lock_file_path.touch()

            # 防御 1: API健康检查
            if not deepseek_client.check_deepseek_health():
                logger.error(f"[{thread_name}] DeepSeek API健康检查失败，中止任务。")
                self._write_failure_log(group_to_process, "DeepSeek API health check failed.")
                return

            # --- 智能工作流开始 ---
            problem_type = qwen_client.classify_problem_type(group_to_process)
            logger.info(f"[{thread_name}] 初步分类结果: {problem_type}")

            final_answer = None
            transcribed_text = "N/A"
            final_problem_type = problem_type

            if problem_type == "VISUAL_REASONING":
                prompt_template = config.PROMPT_TEMPLATES.get(problem_type)
                if not prompt_template:
                    self._write_failure_log(group_to_process, f"Missing prompt template for {problem_type}")
                    return
                logger.info(f"[{thread_name}] 策略选择完成: '{problem_type}'。")
                final_answer = qwen_client.solve_visual_problem(group_to_process, prompt_template)

            elif problem_type in ["CODING", "GENERAL", "QUESTION_ANSWERING"]:
                transcribed_text = qwen_client.transcribe_images(group_to_process)

                # 在调用质量检查之前，先确认转录步骤本身是否成功
                if transcribed_text is None:
                    logger.error(f"[{thread_name}] 文字转录（智能合并）步骤本身失败，返回了None值，任务中止。")
                    self._write_failure_log(group_to_process, "The transcription/merge step failed and returned None.")
                    return

                # 防御 2: OCR质量检查防火墙
                if not self._is_transcription_valid(transcribed_text):
                    self._write_failure_log(group_to_process, "Transcription quality check failed.", transcribed_text)
                    return

                logger.info(f"[{thread_name}] 文字转录成功，长度: {len(transcribed_text)} 字符,"
                            f"\ntranscribed_text:'{transcribed_text}'")

                # 细化分类
                if problem_type == "CODING":
                    final_problem_type = "LEETCODE" if "leetcode" in transcribed_text.lower() else "ACM"
                    logger.info(f"[{thread_name}] 精细化分类: '{final_problem_type}' 模式。")
                    prompt_template = config.PROMPT_TEMPLATES[final_problem_type][config.SOLUTION_STYLE]
                else:
                    prompt_template = config.PROMPT_TEMPLATES.get(problem_type)

                if not prompt_template:
                    self._write_failure_log(group_to_process, f"Missing prompt template for {final_problem_type}",
                                            transcribed_text)
                    return

                logger.info(f"[{thread_name}] 正在构建最终提示词并请求AI进行单次求解...")
                final_prompt = prompt_template.format(transcribed_text=transcribed_text)
                final_answer = deepseek_client.ask_deepseek_for_analysis(final_prompt)

            else:
                self._write_failure_log(group_to_process, f"Unknown problem type: {problem_type}")
                return

            # 防御 3: 求解失败后的备用方案
            if not final_answer:
                logger.error(f"[{thread_name}] 主求解步骤失败，尝试备用方案...")
                backup_prompt = "请仔细阅读并解决以下问题：\n" + transcribed_text
                final_answer = deepseek_client.ask_deepseek_for_analysis(backup_prompt)
                if not final_answer:
                    logger.error(f"[{thread_name}] 备用方案也失败，任务完全中止。")
                    self._write_failure_log(group_to_process, "Main and backup solving steps both failed.",
                                            transcribed_text)
                    return

            # --- 结果归档 ---
            title = parse_title_from_response(final_answer)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            filename = f"{timestamp}_{sanitize_filename(title) if title else group_to_process[0].stem + '_result'}.txt"
            solution_path = config.SOLUTION_DIR / filename

            with open(solution_path, 'w', encoding='utf-8') as f:
                f.write(f"Processed Image Group on {thread_name}:\n- " + "\n- ".join(
                    p.name for p in group_to_process) + "\n\n")
                f.write("=" * 50 + "\n\n")
                f.write(f"Detected Problem Type (Final): {final_problem_type}\n")
                f.write("=" * 50 + "\n\n")
                f.write("Transcribed Text by Qwen-VL:\n" + transcribed_text + "\n\n")
                f.write("=" * 50 + "\n\n")
                style_info = f"(Style: {config.SOLUTION_STYLE})" if final_problem_type in ["LEETCODE", "ACM"] else ""
                f.write(f"Final Solution {style_info}:\n" + final_answer)
            logger.info(f"[{thread_name}] 解答已成功保存至: {solution_path}")

        except Exception as e:
            logger.error(f"[{thread_name}] 处理流水线发生未预期错误: {e}", exc_info=True)
            self._write_failure_log(group_to_process, f"An unexpected error occurred: {e}")
        finally:
            # 无论成功或失败，都清理锁文件并归档图片，防止重复处理
            if lock_file_path.exists():
                lock_file_path.unlink()

            logger.info(f"[{thread_name}] 开始归档已处理的图片至 '{config.PROCESSED_DIR}'...")
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            for img_path in group_to_process:
                if img_path.exists():  # 再次检查文件是否存在
                    destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                    self._move_file_with_retry(img_path, destination)

            logger.info(f"[{thread_name}] 针对组 '{group_to_process[0].name}' 的处理流程结束。")