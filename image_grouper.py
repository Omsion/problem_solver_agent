# -*- coding: utf-8 -*-
"""
image_grouper.py - 图片分组处理器模块 (智能分流版)

本文件是整个自动化Agent的核心调度器（Orchestrator）。它的主要职责包括：

1.  **时间窗口分组**: 监听来自 file_monitor.py 的新图片事件，并将短时间内连续到达的图片智能地组合成一个“待处理组”。
2.  **并发安全**: 使用线程锁（threading.Lock）确保在多线程环境下（文件事件和定时器事件）对共享数据（图片组列表）的访问是安全的。
3.  **防重复处理**: 实现了一个基于文件的“处理锁”机制，防止因外部因素（如IDE热重载）导致的多实例并发执行，确保同一组图片永远只被处理一次。
4.  **智能工作流**: 编排一个包含四个关键步骤的AI流水线：
    a. (Qwen-VL) 问题分类：判断题目是“通用”、“LeetCode”还是“ACM”类型。
    b. (Agent) 策略选择：根据分类结果，从配置文件中选取最优的提示词模板。
    c. (Qwen-VL) 图片转录：将图片内容精确地转换为文本。
    d. (DeepSeek) 问题求解：调用DeepSeek模型，使用策略性的提示词来解决转录后的问题。
5.  **健壮的文件操作**: 在处理完图片后，使用带延时重试的机制将其归档，有效应对因云同步、杀毒软件等导致的临时性文件锁定问题。
"""

import shutil
import time
from pathlib import Path
from threading import Timer, Lock
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
    """

    def __init__(self):
        # self.current_group: 临时的图片路径列表，用于存放当前正在收集的图片组。
        self.current_group: List[Path] = []
        # self.timer: 一个定时器对象，用于实现“超时触发处理”的逻辑。
        self.timer: Timer | None = None
        # self.lock: 线程锁，用于保护 self.current_group 和 self.timer 的线程安全。
        self.lock = Lock()

    def add_image(self, image_path: Path):
        """
        公开的入口方法，由文件监控器(file_monitor.py)在检测到新图片时调用。

        Args:
            image_path (Path): 新创建的图片文件的路径。
        """
        # 使用'with self.lock:'确保在任何时刻只有一个线程可以修改图片组或定时器，
        # 从而避免了在处理文件事件时可能发生的竞态条件。
        with self.lock:
            # 如果已有定时器在运行，说明当前正在一个分组窗口期内，取消旧的定时器。
            if self.timer:
                self.timer.cancel()

            # 将新图片的路径添加到当前分组中。
            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")

            # 创建并启动一个新的定时器。如果在GROUP_TIMEOUT秒内没有新图片加入（即本方法没有被再次调用），
            # 定时器将自动在后台线程中触发 self._process_group 方法。
            self.timer = Timer(config.GROUP_TIMEOUT, self._process_group)
            self.timer.start()

    def _move_file_with_retry(self, src: Path, dest: Path, retries=3, delay=0.5):
        """
        一个健壮的私有辅助方法，用于移动文件，并在失败时进行重试。
        这对于处理由外部程序（如OneDrive, Dropbox, 杀毒软件）造成的临时文件锁至关重要。

        Args:
            src (Path): 源文件路径。
            dest (Path): 目标文件路径。
            retries (int): 最大重试次数。
            delay (float): 每次重试前的等待时间（秒）。
        """
        for i in range(retries):
            try:
                shutil.move(str(src), str(dest))
                logger.info(f"成功移动 '{src.name}' 到 '{dest.parent.name}' 文件夹。")
                return True
            except FileNotFoundError:
                # 这是一个“良性”错误，意味着用户可能在Agent处理期间手动重命名或移动了文件。
                # 既然文件已不在原处，我们的清理目标已经达成，因此可以将其视为成功。
                logger.warning(f"无法找到 '{src.name}' 进行移动。可能已被用户手动处理，此为正常情况。")
                return True
            except Exception as e:
                # 捕获其他所有IO异常，最常见的是'PermissionError'（文件被锁定）。
                if i < retries - 1:
                    logger.warning(
                        f"移动 '{src.name}' 失败 (尝试 {i + 1}/{retries})，可能是文件被锁定。将在 {delay}s 后重试... 错误: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"在 {retries} 次尝试后，移动 '{src.name}' 仍然失败。错误: {e}")
                    return False

    def _process_group(self):
        """
        核心处理方法，由定时器超时后在后台线程中触发。
        负责执行完整的AI流水线和文件归档。
        """
        # 再次使用锁，以确保在复制和清空列表时是原子操作。
        with self.lock:
            if not self.current_group:
                return
            # 关键操作：创建一个当前组的副本进行处理，然后立即清空原始组。
            # 这使得在当前组被送去进行耗时的API调用时，新的截图可以立刻开始形成一个全新的组，互不干扰。
            group_to_process = self.current_group.copy()
            self.current_group.clear()
            logger.info(f"超时! 开始处理包含 {len(group_to_process)} 张图片的组...")

        # --- 防并发处理锁机制 ---
        # 为了防止IDE热重载等外部因素导致多个Agent实例同时处理同一组滞留文件，我们引入文件锁。
        # 锁文件以第一张图片的干文件名命名，存放在solutions目录中。
        lock_file_name = f".{group_to_process[0].stem}.lock"
        lock_file_path = config.SOLUTION_DIR / lock_file_name

        if lock_file_path.exists():
            logger.warning(f"发现针对组 '{group_to_process[0].name}' 的锁文件。另一个实例可能正在处理，本次跳过。")
            return

        try:
            # 创建锁文件，标志着处理开始。
            lock_file_path.touch()

            # --- 智能工作流开始 ---
            # 步骤 1: 调用Qwen-VL对图片内容进行分类。
            problem_type = qwen_client.classify_problem_type(group_to_process)

            # 步骤 2: 根据分类结果，从配置中选择最合适的提示词模板。
            prompt_template = config.PROMPT_TEMPLATES.get(problem_type, config.PROMPT_TEMPLATES["GENERAL"])
            logger.info(f"步骤 2: 策略选择完成。使用 '{problem_type}' 类型的提示词。")

            # 步骤 3: 再次调用Qwen-VL，这次执行高精度的文字转录。
            transcribed_text = qwen_client.transcribe_images(group_to_process)
            if not transcribed_text:
                logger.error("文字转录失败，中止本次工作流。")
                return  # finally块会确保锁文件被删除

            # 步骤 4: 将转录后的文本和策略性提示词发送给DeepSeek进行最终求解。
            final_answer = deepseek_client.ask_deepseek_for_analysis(transcribed_text, prompt_template)
            if not final_answer:
                logger.error("求解失败，中止本次工作流。")
                return  # finally块会确保锁文件被删除

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
                f.write(f"Detected Problem Type: {problem_type}\n")
                f.write("=" * 50 + "\n\n")
                f.write("Step 3: Text Transcribed by Qwen-VL:\n")
                f.write(transcribed_text)
                f.write("\n\n" + "=" * 50 + "\n\n")
                f.write("Step 4: Solution by DeepSeek-Reasoner:\n")
                f.write(final_answer)
            logger.info(f"解答已成功保存至: {solution_path}")

            # --- 原始图片归档 ---
            logger.info(f"开始归档已处理的图片至 '{config.PROCESSED_DIR}'...")
            for img_path in group_to_process:
                destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                self._move_file_with_retry(img_path, destination)

        finally:
            # 'finally'块确保无论处理流程是成功、失败还是中途退出，锁文件最终都会被尝试删除。
            # 这是保证系统不会被永久锁住的关键。
            if lock_file_path.exists():
                lock_file_path.unlink()
            logger.info(f"针对组 '{group_to_process[0].name}' 的处理流程结束，锁已释放。")