# -*- coding: utf-8 -*-
"""
image_grouper.py - 图片分组与处理核心调度器 (V2.0)

本文件是整个自动化Agent的“核心调度器”（Orchestrator）。它的主要职责是：

1.  **时间窗口分组 (Time-based Grouping)**: 监听由 file_monitor.py 传入的新图片事件，
    并使用一个可重置的定时器，将短时间内连续产生的截图智能地归为同一组。这是解决
    “问题太长，一张截图截不完”这一核心痛点的关键。

2.  **并发任务处理 (Concurrent Processing)**: 采用经典的“生产者-消费者”设计模式。
    文件监控器是“生产者”，将图片路径放入一个线程安全的队列中。多个后台工作线程是
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
import deepseek_client  # 仍用于非流式的辅助任务，如文本润色和分步求解的第一步
import solver_client  # 导入新的、统一的流式求解器
from utils import setup_logger, parse_title_from_response, sanitize_filename

# 初始化全局日志记录器
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
            # 如果已有定时器在运行，取消它。因为新图片的到来意味着用户还在
            # 连续截图中，分组的时间窗口需要重置。
            if self.timer:
                self.timer.cancel()

            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")

            # 创建并启动一个新的定时器。如果在 config.GROUP_TIMEOUT 秒内没有新图片
            # 加入，定时器将触发 _submit_group_to_queue 方法。
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

            # 复制当前组列表。这是一个关键步骤，可以防止在将任务放入队列后，
            # self.current_group 被新的 add_image 调用修改，从而导致数据竞争。
            group_to_submit = self.current_group.copy()
            self.current_group.clear()

        # 将完整的图片组放入任务队列，等待后台的工作线程来处理。
        self.task_queue.put(group_to_submit)
        logger.info(f"超时! 包含 {len(group_to_submit)} 张图片的组已提交到处理队列。")

    def _move_file_with_retry(self, src: Path, dest: Path, retries: int = 3, delay: float = 0.5) -> bool:
        """
        一个健壮的文件移动函数，内置重试逻辑。
        这能有效应对因云同步、杀毒软件等导致的临时性文件锁定问题。
        """
        thread_name = current_thread().name
        for i in range(retries):
            try:
                shutil.move(str(src), str(dest))
                logger.info(f"[{thread_name}] 成功移动 '{src.name}' 到 '{dest.parent.name}' 文件夹。")
                return True
            except FileNotFoundError:
                logger.warning(f"[{thread_name}] 尝试移动时未找到 '{src.name}'。可能已被手动处理。")
                return True  # 如果文件已不存在，也视为成功
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
        对转录的文本进行基本的合理性检查（防火墙），防止将明显错误的OCR结果
        （如空文本或过短的无意义文本）传递给下游昂贵的LLM进行分析。
        """
        # 这是一个经验阈值，通常一张有效截图的文本量会远超100字符。
        if not text or len(text) < 100:
            logger.error(
                f"转录质量检测失败: 文本为空或过短 (长度: {len(text)})。这可能表明OCR失败。")
            return False
        logger.info("转录质量检测通过。")
        return True

    def _write_failure_log(self, group: List[Path], reason: str, transcribed_text: str = "N/A"):
        """
        当处理流程中出现不可恢复的错误时，此函数会创建一个详细的失败日志文件，
        方便用户进行事后分析和调试。
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

    def _write_solution_header(self, f, thread_name: str, group: List[Path], final_problem_type: str,
                               transcribed_text: str):
        """
        一个DRY (Don't Repeat Yourself) 辅助函数，用于写入标准的解决方案文件头信息。
        """
        f.write(f"Processed Image Group on {thread_name}:\n- " + "\n- ".join(p.name for p in group) + "\n\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Detected Problem Type (Final): {final_problem_type}\n")
        f.write(f"Selected Solver: {config.SOLVER_PROVIDER} ({config.SOLVER_MODEL_NAME})\n")
        f.write("=" * 50 + "\n\n")
        f.write("Transcribed Text (Polished):\n" + transcribed_text + "\n\n")
        f.write("=" * 50 + "\n\n")
        style_info = f"(Style: {config.SOLUTION_STYLE})" if final_problem_type in ["LEETCODE", "ACM"] else ""
        f.write(f"Final Solution {style_info}:\n")
        f.flush()  # 确保文件头立即写入磁盘

    def _execute_pipeline(self, group_to_process: List[Path]):
        """
        封装了完整的AI处理和文件归档流水线，由每个工作线程独立调用。
        这是包含多层防御和分步求解策略的健壮工作流。
        """
        thread_name = current_thread().name

        # --- 步骤 1: 设置与预检 (Setup & Pre-flight Checks) ---
        lock_file_name = f".{group_to_process[0].stem}.lock"
        lock_file_path = config.SOLUTION_DIR / lock_file_name

        # 使用文件锁机制，防止因程序重启等原因导致同一组图片被重复处理。
        if lock_file_path.exists():
            logger.warning(f"[{thread_name}] 发现锁文件，跳过处理以防重复: {lock_file_name}")
            return

        try:
            lock_file_path.touch()  # 创建锁文件，表示处理开始

            # 在开始昂贵的AI调用前，先进行一次快速的API健康检查。
            # 此处仍使用deepseek作为探针，因为它通常是流程的最后一步。
            if not deepseek_client.check_deepseek_health():
                raise ConnectionError("DeepSeek API health check failed.")

            # --- 步骤 2: 问题分类 (Problem Classification) ---
            problem_type = qwen_client.classify_problem_type(group_to_process)
            logger.info(f"[{thread_name}] 初步分类结果: {problem_type}")

            final_problem_type = problem_type
            transcribed_text = "N/A"
            solution_saved = False

            # --- 步骤 3: 根据类型执行不同流程 (Branching based on Type) ---
            if problem_type == "VISUAL_REASONING":
                prompt_template = config.PROMPT_TEMPLATES.get(problem_type)
                if not prompt_template:
                    raise ValueError(f"Missing prompt template for {problem_type}")

                final_answer = qwen_client.solve_visual_problem(group_to_process, prompt_template)
                if final_answer:
                    title = parse_title_from_response(final_answer)
                    timestamp = time.strftime("%Y%m%d-%H%M%S")
                    filename = f"{timestamp}_{sanitize_filename(title) if title else group_to_process[0].stem + '_result'}.txt"
                    solution_path = config.SOLUTION_DIR / filename
                    with open(solution_path, 'w', encoding='utf-8') as f:
                        self._write_solution_header(f, thread_name, group_to_process, final_problem_type,
                                                    transcribed_text)
                        f.write(final_answer)
                    logger.info(f"[{thread_name}] 解答已成功保存至: {solution_path}")
                    solution_saved = True

            elif problem_type in ["CODING", "GENERAL", "QUESTION_ANSWERING"]:
                # --- 3.1: 文字转录与润色 ---
                transcribed_text = qwen_client.transcribe_images(group_to_process)
                if transcribed_text is None:
                    raise ValueError("The transcription/merge step failed and returned None.")

                polished_text = deepseek_client.ask_deepseek_for_analysis(
                    config.TEXT_POLISHING_PROMPT.format(merged_text=transcribed_text),
                    model_override=config.POLISHING_MODEL_NAME
                )
                if polished_text:
                    transcribed_text = polished_text
                    logger.info(f"[{thread_name}] 文本润色成功。")
                else:
                    logger.warning(f"[{thread_name}] 文本润色失败，将使用原始合并文本。")

                if not self._is_transcription_valid(transcribed_text):
                    raise ValueError("Transcription quality check failed.")

                # --- 3.2: 流式求解与原子化文件写入 ---
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                temp_filename = f"{timestamp}_{group_to_process[0].stem}_inprogress.txt"
                temp_solution_path = config.SOLUTION_DIR / temp_filename

                final_answer_chunks = []
                with open(temp_solution_path, 'w', encoding='utf-8') as f:
                    final_prompt = ""
                    # 对于编程题，采用“先分析，后编码”的两步策略
                    if problem_type == "CODING":
                        final_problem_type = "LEETCODE" if "leetcode" in transcribed_text.lower() else "ACM"
                        logger.info(f"[{thread_name}] 精细化分类: '{final_problem_type}' 模式。")

                        # 步骤A: 获取分析思路 (非流式)
                        prompt_template = config.PROMPT_TEMPLATES[final_problem_type][config.SOLUTION_STYLE]
                        analysis_prompt = prompt_template.format(
                            transcribed_text=transcribed_text) + "\n\n**任务:** 请现在只提供“### 1. 题目分析与核心思路”部分的完整内容，不要提供任何代码。"
                        analysis_text = deepseek_client.ask_deepseek_for_analysis(analysis_prompt)

                        if not analysis_text or "题目分析" not in analysis_text:
                            raise ValueError("未能从AI获取有效的分析思路。")

                        final_answer_chunks.append(analysis_text)
                        logger.info(f"[{thread_name}] 分步求解 - 步骤 1: 成功获取分析。")

                        # 步骤B: 构造代码生成提示词
                        self._write_solution_header(f, thread_name, group_to_process, final_problem_type,
                                                    transcribed_text)
                        f.write(analysis_text)
                        f.write("\n\n### 2. 代码实现\n")  # 写入固定的代码部分标题
                        f.flush()

                        final_prompt = f"**问题文本:**\n---\n{transcribed_text}\n---\n\n**已经确认的分析思路如下:**\n{analysis_text}\n\n**任务:** 基于以上思路，请现在只提供“### 2. 代码实现”部分的完整、可直接运行的Python代码。确保严格遵守题目对输入输出格式的所有要求。"
                    else:
                        # 对于通用问题，直接使用单步提示词
                        prompt_template = config.PROMPT_TEMPLATES.get(problem_type)
                        if not prompt_template:
                            raise ValueError(f"缺少 {problem_type} 的提示词模板")
                        final_prompt = prompt_template.format(transcribed_text=transcribed_text)
                        self._write_solution_header(f, thread_name, group_to_process, final_problem_type,
                                                    transcribed_text)

                    # 【统一流式调用】
                    response_stream = solver_client.stream_solve(final_prompt)
                    for chunk in response_stream:
                        f.write(chunk)
                        f.flush()  # 确保每个数据块都立即写入磁盘
                        final_answer_chunks.append(chunk)

                final_answer = "".join(final_answer_chunks)
                if not final_answer.strip() or "--- ERROR ---" in final_answer:
                    raise ValueError("从流式API收到空响应或错误信息。")

                # --- 3.3: 完成后重命名文件 ---
                title = parse_title_from_response(final_answer)
                final_filename = f"{timestamp}_{sanitize_filename(title) if title else group_to_process[0].stem + '_result'}.txt"
                final_solution_path = config.SOLUTION_DIR / final_filename
                temp_solution_path.rename(final_solution_path)
                logger.info(f"[{thread_name}] 解答已成功流式保存至: {final_solution_path}")
                solution_saved = True

            else:
                raise ValueError(f"未知的问题类型: {problem_type}")

            # --- 步骤 4: 备用方案 (Fallback) ---
            if not solution_saved:
                # 只有在上述所有流程都失败后才会触发
                logger.error(f"[{thread_name}] 主求解步骤失败，任务完全中止。")
                raise RuntimeError("Main solving pipeline failed to produce a solution.")

        except Exception as e:
            # 统一的异常处理中心。捕获流水线中所有可预见的失败。
            logger.error(f"[{thread_name}] 处理流水线时发生错误: {e}", exc_info=True)
            self._write_failure_log(group_to_process, str(e), transcribed_text)
            # 如果存在未完成的临时文件，则删除它
            temp_inprogress_files = list(config.SOLUTION_DIR.glob("*_inprogress.txt"))
            for f in temp_inprogress_files:
                try:
                    f.unlink()
                    logger.info(f"[{thread_name}] 已清理临时文件: {f.name}")
                except OSError:
                    pass

        finally:
            # --- 步骤 5: 清理 (Cleanup) ---
            # 无论成功或失败，此块代码都保证执行。
            if lock_file_path.exists():
                lock_file_path.unlink()  # 移除文件锁

            logger.info(f"[{thread_name}] 开始归档已处理的图片至 '{config.PROCESSED_DIR}'...")
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            for img_path in group_to_process:
                if img_path.exists():
                    destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                    self._move_file_with_retry(img_path, destination)
            logger.info(f"[{thread_name}] 针对组 '{group_to_process[0].name}' 的处理流程结束。")