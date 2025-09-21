# -*- coding: utf-8 -*-
"""
image_grouper.py - 图片分组处理器模块 (智能分流版)
"""

import shutil
import time
from pathlib import Path
from threading import Timer, Lock
from typing import List

import config
import qwen_client
import deepseek_client
from utils import setup_logger, parse_title_from_response, sanitize_filename

logger = setup_logger()


class ImageGrouper:
    def __init__(self):
        self.current_group: List[Path] = []
        self.timer: Timer | None = None
        self.lock = Lock()

    def add_image(self, image_path: Path):
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")
            self.timer = Timer(config.GROUP_TIMEOUT, self._process_group)
            self.timer.start()

    def _move_file_with_retry(self, src: Path, dest: Path, retries=3, delay=0.5):
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

    def _process_group(self):
        with self.lock:
            if not self.current_group:
                return
            group_to_process = self.current_group.copy()
            self.current_group.clear()
            logger.info(f"超时! 开始处理包含 {len(group_to_process)} 张图片的组...")

        lock_file_name = f".{group_to_process[0].stem}.lock"
        lock_file_path = config.SOLUTION_DIR / lock_file_name

        if lock_file_path.exists():
            logger.warning(f"发现针对组 '{group_to_process[0].name}' 的锁文件。另一个实例可能正在处理，本次跳过。")
            return

        try:
            lock_file_path.touch()

            # 实现了“粗分类-精细化”的智能工作流 ###
            # -------------------------------------------------------------------------------------
            # 步骤 1: AI进行粗分类
            broad_problem_type = qwen_client.classify_problem_type(group_to_process)

            # 步骤 2: 必须先进行文字转录，才能进行下一步的精细化判断
            transcribed_text = qwen_client.transcribe_images(group_to_process)
            if not transcribed_text:
                logger.error("文字转录失败，中止本次工作流。")
                return

            # 步骤 3: 基于代码规则进行精细化分类和策略选择
            final_problem_type = broad_problem_type
            if broad_problem_type == "CODING":
                # 规则：检查转录文本中是否包含'leetcode' (不区分大小写)
                if "leetcode" in transcribed_text.lower():
                    final_problem_type = "LEETCODE"
                    logger.info("精细化分类：在文本中检测到 'LeetCode' 关键词。")
                else:
                    final_problem_type = "ACM"
                    logger.info("精细化分类：未检测到 'LeetCode' 关键词，默认为 ACM 模式。")

            prompt_template = config.PROMPT_TEMPLATES.get(final_problem_type, config.PROMPT_TEMPLATES["GENERAL"])
            logger.info(f"步骤 3: 策略选择完成。最终使用 '{final_problem_type}' 类型的提示词。")

            # 步骤 4: 使用最终确定的策略进行求解
            final_answer = deepseek_client.ask_deepseek_for_analysis(transcribed_text, prompt_template)
            if not final_answer:
                logger.error("求解失败，中止本次工作流。")
                return
            # -------------------------------------------------------------------------------------

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
                f.write(f"Detected Problem Type (Final): {final_problem_type}\n")  # 使用最终类型
                f.write("=" * 50 + "\n\n")
                f.write("Transcribed Text by Qwen-VL:\n")  # 调整标题以反映真实步骤
                f.write(transcribed_text)
                f.write("\n\n" + "=" * 50 + "\n\n")
                f.write("Solution by DeepSeek-Reasoner:\n")
                f.write(final_answer)
            logger.info(f"解答已成功保存至: {solution_path}")

            # --- 原始图片归档 ---
            logger.info(f"开始归档已处理的图片至 '{config.PROCESSED_DIR}'...")
            for img_path in group_to_process:
                destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                self._move_file_with_retry(img_path, destination)

        finally:
            if lock_file_path.exists():
                lock_file_path.unlink()
            logger.info(f"针对组 '{group_to_process[0].name}' 的处理流程结束，锁已释放。")