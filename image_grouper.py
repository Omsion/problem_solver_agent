# -*- coding: utf-8 -*-
"""
image_grouper.py - 图片分组处理器模块 (智能分流版)

本文件是整个自动化Agent的核心调度器（Orchestrator）。它的主要职责包括：

1.  **时间窗口分组**: 监听来自 file_monitor.py 的新图片事件，并将短时间内连续到达的图片智能地组合成一个“待处理组”。
2.  **并发安全**: 使用线程锁（threading.Lock）确保在多线程环境下对共享数据（图片组列表）的访问是安全的。
3.  **防重复处理**: 实现了一个基于文件的“处理锁”机制，防止因外部因素（如IDE热重载）导致的多实例并发执行，确保同一组图片永远只被处理一次。
4.  **智能工作流**: 编排一个多分支的AI流水线：
    a. (Qwen-VL) 问题分类：判断题目是“编程”、“视觉”还是“通用”类型。
    b. (Agent) 智能分流与策略选择：根据分类结果和代码规则，决定最终的处理路径和提示词模板，包括选择“最优”还是“次优”解法。
    c. (AI Models) 问题求解：调用相应的AI模型（Qwen或DeepSeek）来解决问题。
5.  **健壮的文件操作**: 在处理完图片后，使用带延时重试的机制将其归档，有效应对因云同步、杀毒软件等导致的临时性文件锁定问题。
6.  **并行任务处理**: 采用生产者-消费者模式，将每个待处理的图片组作为一个独立任务放入线程安全的队列中，
    由一个或多个后台工作线程并行处理，解决了“新任务中断旧任务”的并发缺陷。
"""

import shutil
import time
from pathlib import Path
from threading import Timer, Lock, Thread
from queue import Queue
from typing import List

# 从项目其他模块导入必要的配置和功能
import config
import qwen_client
import deepseek_client
from utils import setup_logger, parse_title_from_response, sanitize_filename

# 初始化日志记录器
logger = setup_logger()


class ImageGrouper:
    """
    一个状态化的核心类，用于管理图片的分组、AI处理流水线以及后续的归档工作。
    本版本采用任务队列和工作线程，以支持稳定、并行的多任务处理。
    """

    def __init__(self, num_workers=2):
        # self.current_group: 临时的图片路径列表，用于存放当前正在收集的图片组。
        self.current_group: List[Path] = []
        # self.timer: 一个定时器对象，用于实现“超时触发处理”的逻辑。
        self.timer: Timer | None = None
        # self.lock: 线程锁，用于保护 self.current_group 和 self.timer 的线程安全。
        self.lock = Lock()

        # --- 任务队列和工作线程 ---
        # 采用生产者-消费者模式，彻底解决“新任务中断旧任务”的并发问题。
        # self.task_queue: 一个线程安全的队列，用于存放待处理的图片组（任务），充当“生产者”和“消费者”之间的缓冲。
        self.task_queue = Queue()
        # self.num_workers: 同时执行任务的工作线程数量。默认为2，意味着可以同时处理两个不同的图片组。
        self.num_workers = num_workers
        self.workers: List[Thread] = []
        self._start_workers()

    def _start_workers(self):
        """
        私有辅助方法，在类实例化时，初始化并启动所有后台工作线程。
        """
        logger.info(f"正在启动 {self.num_workers} 个后台工作线程...")
        for i in range(self.num_workers):
            # 将工作线程设置为守护线程 (daemon=True)，意味着当主程序退出时，这些线程也会被自动、强制地终止，
            # 避免了程序关闭后仍有残留线程在运行的问题。
            worker = Thread(target=self._worker_loop, daemon=True, name=f"Worker-{i + 1}")
            worker.start()
            self.workers.append(worker)
        logger.info("后台工作线程已全部启动，等待任务中...")

    def _worker_loop(self):
        """
        每个工作线程（“消费者”）的主循环。
        它会永远地、高效地等待任务队列中的新任务。
        """
        while True:
            # task_queue.get() 是一个阻塞操作。如果队列为空，线程会在此处“睡眠”，不消耗CPU资源，
            # 直到队列中有新任务被放入，线程才会被唤醒。
            group_to_process = self.task_queue.get()
            thread_name = Path(__file__).name
            logger.info(f"\n工作线程 {thread_name} 领取了一个新任务，包含 {len(group_to_process)} 张图片。")
            try:
                # 调用我们已有的、健壮的流水线处理逻辑。
                self._execute_pipeline(group_to_process)
            except Exception as e:
                # 捕获流水线中可能出现的任何意外错误，记录日志，以防止单个任务的失败导致整个工作线程崩溃。
                logger.error(f"处理任务时发生意外错误: {e}", exc_info=True)
            finally:
                # 通知队列，刚才取出的那个任务已经处理完毕。这对于队列管理是必要的。
                self.task_queue.task_done()

    def add_image(self, image_path: Path):
        """
        公开的入口方法（“生产者”的一部分），由文件监控器在检测到新图片时调用。
        """
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")
            # 定时器现在的唯一职责是触发“提交任务到队列”这个轻量级动作。
            self.timer = Timer(config.GROUP_TIMEOUT, self._submit_group_to_queue)
            self.timer.start()

    def _submit_group_to_queue(self):
        """
        当定时器超时后，此方法被调用，负责将收集好的图片组作为一个任务放入队列。
        """
        with self.lock:
            if not self.current_group:
                return
            group_to_submit = self.current_group.copy()
            self.current_group.clear()

        # task_queue.put() 是一个线程安全的操作，它将任务放入队列，供后台的工作线程领取。
        self.task_queue.put(group_to_submit)
        logger.info(f"超时! 包含 {len(group_to_submit)} 张图片的组已提交到处理队列。")

    def _move_file_with_retry(self, src: Path, dest: Path, retries=3, delay=0.5):
        """
        一个健壮的私有辅助方法，用于移动文件，并在失败时进行重试。
        这对于处理由外部程序（如OneDrive, Dropbox, 杀毒软件）造成的临时文件锁至关重要。
        """
        for i in range(retries):
            try:
                shutil.move(str(src), str(dest))
                logger.info(f"成功移动 '{src.name}' 到 '{dest.parent.name}' 文件夹。")
                return True
            except FileNotFoundError:
                logger.warning(f"无法找到 '{src.name}' 进行移动。可能已被用户手动处理，此为正常情况。")
                return True
            except Exception as e:
                if i < retries - 1:
                    logger.warning(
                        f"移动 '{src.name}' 失败 (尝试 {i + 1}/{retries})，可能是文件被锁定。将在 {delay}s 后重试... 错误: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"在 {retries} 次尝试后，移动 '{src.name}' 仍然失败。错误: {e}")
                    return False

    def _execute_pipeline(self, group_to_process: List[Path]):
        """
        封装了完整的AI处理和归档流水线，现在由每个工作线程独立调用。
        """
        # --- 防并发处理锁机制 ---
        lock_file_name = f".{group_to_process[0].stem}.lock"
        lock_file_path = config.SOLUTION_DIR / lock_file_name

        if lock_file_path.exists():
            logger.warning(f"发现针对组 '{group_to_process[0].name}' 的锁文件。另一个实例可能正在处理，本次跳过。")
            return

        try:
            lock_file_path.touch()

            # --- 智能工作流开始 ---
            # 步骤 1: AI进行问题粗分类
            problem_type = qwen_client.classify_problem_type(group_to_process)

            # 初始化变量
            final_answer = None
            transcribed_text = "N/A (视觉推理任务，无此步骤)"
            final_problem_type = problem_type
            prompt_template = None

            # 步骤 2 & 3: 核心逻辑分流与策略选择
            if problem_type == "VISUAL_REASONING":
                # === 视觉推理路径 ===
                prompt_template = config.PROMPT_TEMPLATES.get(problem_type)
                logger.info(f"步骤 2: 策略选择完成。使用 '{problem_type}' 类型的提示词。")
                final_answer = qwen_client.solve_visual_problem(group_to_process, prompt_template)

            elif problem_type in ["CODING", "GENERAL"]:
                # === 文本处理路径 ===
                transcribed_text = qwen_client.transcribe_images(group_to_process)
                if transcribed_text:
                    if problem_type == "CODING":
                        # 步骤 2.1 (精细化): 基于代码规则进一步判断编程题类型
                        if "leetcode" in transcribed_text.lower():
                            final_problem_type = "LEETCODE"
                        else:
                            final_problem_type = "ACM"
                        logger.info(f"精细化分类：最终判断为 '{final_problem_type}' 模式。")
                        # 步骤 2.2 (风格选择): 根据配置选择最优或次优解法
                        strategy_package = config.PROMPT_TEMPLATES.get(final_problem_type)
                        prompt_template = strategy_package.get(config.SOLUTION_STYLE)
                        logger.info(f"风格选择：采用 '{config.SOLUTION_STYLE}' 风格的提示词。")
                    else:  # GENERAL类型
                        final_problem_type = "GENERAL"
                        prompt_template = config.PROMPT_TEMPLATES.get(final_problem_type)
                        logger.info(f"步骤 2: 策略选择完成。使用 '{final_problem_type}' 类型的提示词。")

                    # 步骤 4: 交由DeepSeek求解
                    final_answer = deepseek_client.ask_deepseek_for_analysis(transcribed_text, prompt_template)
                else:
                    logger.error("文字转录失败，中止本次工作流。")

            # 检查最终结果
            if not final_answer:
                logger.error("求解步骤失败，中止本次工作流。")
                return

            # --- 结果归档 ---
            title = parse_title_from_response(final_answer)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            if title:
                filename = f"{timestamp}_{sanitize_filename(title)}.txt"
            else:
                logger.warning("未能在回答中找到标题，将使用默认文件名。")
                base_name = group_to_process[0].stem
                filename = f"{timestamp}_{base_name}_result.txt"

            solution_path = config.SOLUTION_DIR / filename
            with open(solution_path, 'w', encoding='utf-8') as f:
                f.write(f"Processed Image Group:\n")
                for img_path in group_to_process:
                    f.write(f"- {img_path.name}\n")
                f.write("\n" + "=" * 50 + "\n\n")
                f.write(f"Detected Problem Type (Final): {final_problem_type}\n")
                f.write("=" * 50 + "\n\n")
                f.write("Transcribed Text by Qwen-VL:\n")
                f.write(transcribed_text)
                f.write("\n\n" + "=" * 50 + "\n\n")
                # 统一命名为"Final Solution"，并附加上风格信息
                style_info = f"(Style: {config.SOLUTION_STYLE})" if final_problem_type in ["LEETCODE", "ACM"] else ""
                f.write(f"Final Solution {style_info}:\n")
                f.write(final_answer)
            logger.info(f"解答已成功保存至: {solution_path}")

            # --- 原始图片归档 ---
            logger.info(f"开始归档已处理的图片至 '{config.PROCESSED_DIR}'...")
            for img_path in group_to_process:
                destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                self._move_file_with_retry(img_path, destination)

        finally:
            # 'finally'块确保无论处理流程是成功、失败还是中途退出，锁文件最终都会被尝试删除。
            if lock_file_path.exists():
                lock_file_path.unlink()
            logger.info(f"针对组 '{group_to_process[0].name}' 的处理流程结束，锁已释放。")