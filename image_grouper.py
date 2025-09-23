# -*- coding: utf-8 -*-
"""
image_grouper.py - 图片分组处理器模块 (最终优化版)

本文件是整个自动化Agent的核心调度器（Orchestrator）。它的主要职责包括：

1.  **时间窗口分组**: 监听新图片事件，并使用定时器将短时间内连续产生的截图智能地归为一组。
2.  **并发安全与高效处理**: 采用“生产者-消费者”模型，文件监控器作为生产者将任务放入队列，
    多个工作线程作为消费者并行处理任务，确保高吞吐量和UI无阻塞。
3.  **“分步求解”智能策略**: 对于复杂的编程题，采用两阶段API调用（先请求分析思路，再根据思路请求代码），
    显著提高了AI响应速度和代码生成的完整性与准确性。
4.  **健壮性设计**: 内置API健康检查、文件操作重试、锁文件防重复处理以及详尽的失败日志记录，
    确保了整个系统的稳定可靠。
"""

import shutil
import time
from pathlib import Path
from threading import Timer, Lock, Thread, current_thread
from queue import Queue
from typing import List

import config
import qwen_client
import deepseek_client
from utils import setup_logger, parse_title_from_response, sanitize_filename

# 初始化日志记录器
logger = setup_logger()


class ImageGrouper:
    """
    一个状态化的核心类，用于管理图片的分组、AI处理流水线以及后续的归档工作。
    它通过一个任务队列和多个工作线程来实现高效、并行的多任务处理。
    """

    def __init__(self, num_workers: int = 2):
        """
        初始化ImageGrouper实例。

        Args:
            num_workers (int): 要启动的后台工作线程数量。默认为2。
        """
        self.current_group: List[Path] = []  # 临时存储当前正在收集的图片组
        self.timer: Timer | None = None  # 用于实现分组超时的定时器
        self.lock = Lock()  # 线程锁，保护对 self.current_group 的并发访问
        self.task_queue = Queue()  # 任务队列，用于解耦文件监控和任务处理
        self.num_workers = num_workers  # 工作线程的数量
        self.workers: List[Thread] = []  # 存储工作线程对象的列表
        self._start_workers()  # 启动工作线程

    def _start_workers(self):
        """
        私有辅助方法，在类实例化时，初始化并启动所有后台工作线程。
        为每个线程命名，极大地方便了并发场景下的日志追溯和调试。
        """
        logger.info(f"正在启动 {self.num_workers} 个后台工作线程...")
        for i in range(self.num_workers):
            # 将工作线程设置为守护线程（daemon=True），这样主程序退出时它们也会自动退出
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
                # 捕获所有未预期的异常，防止单个任务失败导致整个线程崩溃
                logger.error(f"[{thread_name}] 处理任务时发生致命的意外错误: {e}", exc_info=True)
            finally:
                # 确保即使发生异常，任务也能被标记为完成，防止队列阻塞
                self.task_queue.task_done()

    def add_image(self, image_path: Path):
        """
        公开的入口方法（“生产者”），由文件监控器在检测到新图片时调用。
        该方法是线程安全的。

        Args:
            image_path (Path): 新检测到的图片的路径。
        """
        with self.lock:
            # 如果已有定时器在运行，取消它，因为新的图片意味着用户还在连续截图中
            if self.timer:
                self.timer.cancel()

            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")

            # 重置定时器。如果在GROUP_TIMEOUT秒内没有新图片加入，定时器将触发 _submit_group_to_queue 方法
            self.timer = Timer(config.GROUP_TIMEOUT, self._submit_group_to_queue)
            self.timer.start()

    def _submit_group_to_queue(self):
        """
        当分组定时器超时后，此方法被调用。它会将收集好的图片组作为一个任务放入队列，
        并清空当前组以便开始下一轮收集。该方法是线程安全的。
        """
        with self.lock:
            if not self.current_group:
                return

            # 复制当前组，避免在多线程环境下出现问题
            group_to_submit = self.current_group.copy()
            self.current_group.clear()

        # 将完整的图片组放入任务队列，供工作线程消费
        self.task_queue.put(group_to_submit)
        logger.info(f"超时! 包含 {len(group_to_submit)} 张图片的组已提交到处理队列。")

    def _move_file_with_retry(self, src: Path, dest: Path, retries: int = 3, delay: float = 0.5) -> bool:
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
        return False

    def _is_transcription_valid(self, text: str) -> bool:
        """
        对转录的文本进行基本的合理性检查（防火墙），防止将明显错误的OCR结果传递给下游。
        """
        thread_name = current_thread().name
        # 编程题的单张截图内容通常远超100字符，这是一个宽松的经验阈值
        if not text or len(text) < 100:
            logger.error(
                f"[{thread_name}] 转录质量检测失败: 文本为空或过短 (长度: {len(text)})。这表明OCR可能已完全失败。")
            return False
        logger.info(f"[{thread_name}] 转录质量检测通过。")
        return True

    def _write_failure_log(self, group: List[Path], reason: str, transcribed_text: str = "N/A"):
        """
        当流程中出现不可恢复的错误时，写入一个包含错误信息的日志文件，方便用户进行事后分析。
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
        这是包含多层防御和分步求解策略的健壮工作流。
        """
        thread_name = current_thread().name

        # --- 1. 设置与预检 ---
        lock_file_name = f".{group_to_process[0].stem}.lock"
        lock_file_path = config.SOLUTION_DIR / lock_file_name
        if lock_file_path.exists():
            logger.warning(f"[{thread_name}] 发现锁文件，跳过处理以防重复: {lock_file_name}")
            return

        try:
            lock_file_path.touch()  # 创建锁文件

            if not deepseek_client.check_deepseek_health():
                logger.error(f"[{thread_name}] DeepSeek API健康检查失败，中止任务。")
                self._write_failure_log(group_to_process, "DeepSeek API health check failed.")
                return

            # --- 2. 问题分类 ---
            problem_type = qwen_client.classify_problem_type(group_to_process)
            logger.info(f"[{thread_name}] 初步分类结果: {problem_type}")

            final_answer = None
            transcribed_text = "N/A"
            final_problem_type = problem_type

            # --- 3. 根据类型执行不同流程 ---
            if problem_type == "VISUAL_REASONING":
                prompt_template = config.PROMPT_TEMPLATES.get(problem_type)
                if not prompt_template:
                    self._write_failure_log(group_to_process, f"Missing prompt template for {problem_type}")
                    return
                final_answer = qwen_client.solve_visual_problem(group_to_process, prompt_template)

            elif problem_type in ["CODING", "GENERAL", "QUESTION_ANSWERING"]:
                # --- 3.1. 文字转录与润色 ---
                transcribed_text = qwen_client.transcribe_images(group_to_process)
                if transcribed_text is None:
                    self._write_failure_log(group_to_process, "The transcription/merge step failed and returned None.")
                    return

                logger.info(f"[{thread_name}] 正在使用快速模型({config.POLISHING_MODEL_NAME})对合并后的文本进行润色...")
                polishing_prompt = config.TEXT_POLISHING_PROMPT.format(merged_text=transcribed_text)
                polished_text = deepseek_client.ask_deepseek_for_analysis(polishing_prompt,
                                                                          model_override=config.POLISHING_MODEL_NAME)

                if polished_text:
                    transcribed_text = polished_text
                    logger.info(f"[{thread_name}] 文本润色成功。")
                else:
                    logger.warning(f"[{thread_name}] 文本润色失败，将使用原始合并文本。")

                if not self._is_transcription_valid(transcribed_text):
                    self._write_failure_log(group_to_process, "Transcription quality check failed.", transcribed_text)
                    return

                logger.info(f"[{thread_name}] 文字转录成功，长度: {len(transcribed_text)} 字符,"
                            f"\ntranscribed_text:'{transcribed_text}'")

                # --- 3.2. 分步求解 ---
                if problem_type == "CODING":
                    final_problem_type = "LEETCODE" if "leetcode" in transcribed_text.lower() else "ACM"
                    logger.info(f"[{thread_name}] 精细化分类: '{final_problem_type}' 模式。")

                    # 步骤 1: 请求题目分析和核心思路
                    logger.info(f"[{thread_name}] 分步求解 - 步骤 1: 请求题目分析...")
                    prompt_template = config.PROMPT_TEMPLATES[final_problem_type][config.SOLUTION_STYLE]
                    analysis_prompt = prompt_template.format(
                        transcribed_text=transcribed_text) + "\n\n**任务:** 请现在只提供“### 1. 题目分析与核心思路”部分的完整内容，不要提供任何代码。"
                    analysis_text = deepseek_client.ask_deepseek_for_analysis(analysis_prompt)

                    if not analysis_text or "题目分析" not in analysis_text:
                        self._write_failure_log(group_to_process, "Failed to get a valid analysis from AI.",
                                                transcribed_text)
                        return
                    logger.info(f"[{thread_name}] 分步求解 - 步骤 1: 成功获取分析。")

                    # 步骤 2: 基于已确认的分析，请求代码实现
                    logger.info(f"[{thread_name}] 分步求解 - 步骤 2: 请求代码实现...")
                    code_prompt = (
                        f"**问题文本:**\n---\n{transcribed_text}\n---\n\n"
                        f"**已经确认的分析思路如下:**\n{analysis_text}\n\n"
                        f"**任务:** 基于以上思路，请现在只提供“### 2. 最优解Python代码实现”部分的完整、可直接运行的Python代码。确保严格遵守题目对输入输出格式的所有要求。"
                    )
                    code_text = deepseek_client.ask_deepseek_for_analysis(code_prompt)

                    if not code_text or "import" not in code_text.lower():
                        self._write_failure_log(group_to_process, "Failed to get valid code from AI.", transcribed_text)
                        return
                    logger.info(f"[{thread_name}] 分步求解 - 步骤 2: 成功获取代码。")

                    # 组合成最终的、结构化的答案
                    chars_to_strip = '`python\n '
                    stripped_code = code_text.strip(chars_to_strip)
                    final_answer = f"{analysis_text}\n\n### 2. 最优解Python代码实现\n\n```python\n{stripped_code}\n```"

                else:  # 对于非编程题，使用传统的单步请求
                    prompt_template = config.PROMPT_TEMPLATES.get(problem_type)
                    if not prompt_template:
                        self._write_failure_log(group_to_process, f"Missing prompt template for {problem_type}",
                                                transcribed_text)
                        return
                    final_prompt = prompt_template.format(transcribed_text=transcribed_text)
                    final_answer = deepseek_client.ask_deepseek_for_analysis(final_prompt)
            else:
                self._write_failure_log(group_to_process, f"Unknown problem type: {problem_type}")
                return

            # 求解失败后的备用方案
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
                f.write("Transcribed Text (Polished):\n" + transcribed_text + "\n\n")
                f.write("=" * 50 + "\n\n")
                style_info = f"(Style: {config.SOLUTION_STYLE})" if final_problem_type in ["LEETCODE", "ACM"] else ""
                f.write(f"Final Solution {style_info}:\n" + final_answer)
            logger.info(f"[{thread_name}] 解答已成功保存至: {solution_path}")

        except Exception as e:
            logger.error(f"[{thread_name}] 处理流水线发生未预期错误: {e}", exc_info=True)
            self._write_failure_log(group_to_process, f"An unexpected error occurred: {e}")
        finally:
            # --- 5. 清理 ---
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