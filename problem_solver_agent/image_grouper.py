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
from queue import Queue
from threading import Lock, Thread, Timer, current_thread

# 导入项目模块
from . import config, solver_client, vision_client
from .utils import extract_question_numbers, format_number_prefix, sanitize_filename, setup_logger

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
        if not text or len(text) < 5:
            logger.error(f"转录/合并后文本质量检测失败: 文本为空或过短 (长度: {len(text)})。")
            return False
        logger.info("转录/合并后文本质量检测通过。")
        return True

    def _write_failure_log(self, group: list[Path], reason: str, transcribed_text: str = "N/A"):
        """当处理流程出现不可恢复的错误时，创建 Markdown 格式的失败日志。"""
        thread_name = current_thread().name
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        filename = f"{time.strftime('%Y%m%d-%H%M%S')}_{group[0].stem}_FAILED.md"
        failure_path = config.SOLUTION_DIR / filename

        image_lines = "\n".join(f"  - {p.name}" for p in group)
        with open(failure_path, 'w', encoding='utf-8') as f:
            f.write("---\n")
            f.write("status: FAILED\n")
            f.write(f"created: {timestamp}\n")
            f.write(f"thread: {thread_name}\n")
            f.write("images:\n")
            f.write(image_lines + "\n")
            f.write("---\n\n")
            f.write("# 处理失败\n\n")
            f.write(f"> **原因**: {reason}\n\n")
            if transcribed_text and transcribed_text != "N/A":
                f.write("## 已提取的文本\n\n")
                f.write(transcribed_text)
        logger.info(f"[{thread_name}] 失败日志已保存至: {failure_path}")

    def _write_solution_header(self, f, thread_name: str, group: list[Path], final_problem_type: str,
                                transcribed_text: str, solver_provider: str, solver_model: str):
        """
        写入 YAML frontmatter + 结构化 Markdown 文件头。
        frontmatter 确保在任何 markdown 渲染器（GitHub/VS Code/Obsidian）中
        元数据都能正确显示，不会出现纯文本粘连。
        """
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        image_lines = "\n".join(f"  - {p.name}" for p in group)

        # --- YAML frontmatter ---
        f.write("---\n")
        f.write(f"problem_type: {final_problem_type}\n")
        f.write(f"solver: {solver_provider} ({solver_model})\n")
        f.write(f"aux_model: {config.AUX_PROVIDER} ({config.AUX_MODEL_NAME})\n")
        f.write(f"thread: {thread_name}\n")
        if final_problem_type in ("LEETCODE", "ACM", "ML_CODING"):
            f.write(f"style: {config.SOLUTION_STYLE}\n")
        f.write(f"created: {timestamp}\n")
        f.write("images:\n")
        f.write(image_lines + "\n")
        f.write("---\n\n")

        # --- 题目文本 ---
        if transcribed_text and transcribed_text != "N/A":
            f.write("# 题目文本\n\n")
            f.write(transcribed_text.strip() + "\n\n")
            f.write("---\n\n")

        # --- 解答 ---
        f.write("# 解答\n\n")
        f.flush()

    def _textualize_problem(self, group: list[Path]) -> str:
        """执行OCR和文本合并润色，返回最终的文本化结果。"""
        raw_transcriptions = vision_client.transcribe_images_raw(group)
        if not raw_transcriptions:
            raise ValueError("独立文字转录步骤返回了空结果。")

        raw_texts_joined = "\n---[NEXT]---\n".join(raw_transcriptions)
        merge_polish_prompt = config.TEXT_MERGE_AND_POLISH_PROMPT.format(raw_texts=raw_texts_joined)

        polished_text = solver_client.ask_for_analysis(
            merge_polish_prompt,
            provider=config.AUX_PROVIDER,
            model=config.AUX_MODEL_NAME
        )
        if not polished_text:
            raise ValueError("LLM合并与润色步骤失败。")

        if not self._is_transcription_valid(polished_text):
            raise ValueError("合并后的文本质量检查未通过。")

        return polished_text

    # _determine_solver 已替换为 config.determine_solver()

    def _generate_final_filename(self, transcribed_text: str, final_problem_type: str, timestamp: str) -> Path:
        """调用LLM生成文件名主体，并处理回退逻辑。"""
        logger.info("开始通过LLM生成智能文件名...")
        filename_gen_prompt = config.FILENAME_GENERATION_PROMPT.format(transcribed_text=transcribed_text)

        filename_body = solver_client.ask_for_analysis(
            filename_gen_prompt,
            provider=config.AUX_PROVIDER,
            model=config.AUX_MODEL_NAME
        )

        if not filename_body:
            logger.warning("LLM未能生成文件名，将使用回退机制。")
            numbers = extract_question_numbers(transcribed_text)
            number_prefix = format_number_prefix(numbers)
            fallback_topic = f"{final_problem_type}_Solution"
            filename_body = f"{number_prefix}_{fallback_topic}" if number_prefix else fallback_topic

        logger.info(f"最终生成文件名主体: '{filename_body}'")
        final_filename = f"{timestamp}_{sanitize_filename(filename_body)}.md"
        return config.SOLUTION_DIR / final_filename

    def _execute_pipeline(self, group_to_process: list[Path]):
        """
        处理单个任务（图片组）的完整工作流。
        (V2.5 - 增加了对ML/DL编程题的智能分流逻辑)
        """
        # --- 0. 初始化 ---
        thread_name = current_thread().name
        lock_file_path = config.SOLUTION_DIR / f".{group_to_process[0].stem}.lock"
        transcribed_text = "N/A"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        temp_solution_path = config.SOLUTION_DIR / f"{timestamp}_{group_to_process[0].stem}_inprogress.md"

        if lock_file_path.exists():
            logger.warning(f"[{thread_name}] 发现锁文件，跳过处理以防重复: {lock_file_path.name}")
            return

        try:
            lock_file_path.touch()

            # --- 步骤 1: 初步视觉分类 ---
            # 使用多模态模型对图片内容进行快速的初步分类。
            problem_type = vision_client.classify_problem_type(group_to_process)
            logger.info(f"[{thread_name}] 初步视觉分类结果: {problem_type}")

            # --- 步骤 2: 文本化 ---
            # 对于所有非纯视觉推理的任务，都进行高精度OCR。
            if problem_type != "VISUAL_REASONING":
                transcribed_text = self._textualize_problem(group_to_process)

            # --- 步骤 2.5: 智能重分类 (使用 config 共享逻辑) ---
            if problem_type != "VISUAL_REASONING" and transcribed_text != "N/A":
                problem_type = config.reclassify_problem_type(problem_type, transcribed_text)

            # --- 步骤 3: 核心求解 ---
            with open(temp_solution_path, 'w', encoding='utf-8') as f:
                if problem_type == "VISUAL_REASONING":
                    final_problem_type = "VISUAL_REASONING"
                    self._write_solution_header(f, thread_name, group_to_process,
                                                final_problem_type, transcribed_text,
                                                solver_provider=config.VISION_PROVIDER_NAME,
                                                solver_model=config.VISION_REASONING_MODEL)
                    response_stream = vision_client.solve_visual_reasoning_problem(group_to_process)
                else:
                    # 根据最终确定的 problem_type，调用 config 共享函数确定类型和求解器
                    final_problem_type = config.map_final_type(problem_type, transcribed_text)
                    solver_provider, solver_model = config.determine_solver(final_problem_type)

                    # 获取对应的Prompt模板
                    prompt_template = config.PROMPT_TEMPLATES.get(final_problem_type)
                    if final_problem_type in ["LEETCODE", "ACM", "ML_CODING"]:  # 将ML_CODING加入此列表
                        prompt_template = prompt_template[config.SOLUTION_STYLE]

                    if not prompt_template:
                        raise ValueError(f"缺少 '{final_problem_type}' 的提示词模板。")

                    # 写入文件头
                    self._write_solution_header(f, thread_name, group_to_process,
                                                final_problem_type, transcribed_text,
                                                solver_provider, solver_model)

                    # 发起求解请求
                    final_solve_prompt = prompt_template.format(transcribed_text=transcribed_text)
                    response_stream = solver_client.stream_solve_text_only(final_solve_prompt,
                                                                                     provider=solver_provider,
                                                                                     model=solver_model)

                # 将流式响应写入临时文件
                final_answer_chunks = [chunk for chunk in response_stream]
                final_answer = "".join(final_answer_chunks)
                f.write(final_answer)

            # 检查求解是否成功
            if not final_answer.strip() or "--- ERROR ---" in final_answer:
                raise ValueError(f"从核心求解器收到空响应或错误信息: {final_answer}")

            # --- 步骤 4: 文件名生成与重命名 ---
            final_solution_path = self._generate_final_filename(transcribed_text, final_problem_type, timestamp)
            temp_solution_path.rename(final_solution_path)
            logger.info(f"[{thread_name}] 解答已成功保存至: {final_solution_path}")

        except Exception as e:
            # --- 异常处理 ---
            logger.error(f"[{thread_name}] 处理流水线时发生错误: {e}", exc_info=True)
            self._write_failure_log(group_to_process, str(e), transcribed_text)
            if temp_solution_path.exists():
                temp_solution_path.unlink()
        finally:
            # --- 步骤 5: 清理与归档 ---
            if lock_file_path.exists():
                lock_file_path.unlink()
            logger.info(f"[{thread_name}] 开始归档已处理的图片...")
            for img_path in group_to_process:
                if img_path.exists():
                    destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                    self._move_file_with_retry(img_path, destination)
            logger.info(f"[{thread_name}] 针对组 '{group_to_process[0].name}' 的处理流程结束。")
