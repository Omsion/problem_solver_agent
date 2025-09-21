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
            logger.info(f"Image added to group: {image_path.name} ({len(self.current_group)} total)")
            self.timer = Timer(config.GROUP_TIMEOUT, self._process_group)
            self.timer.start()

    def _process_group(self):
        with self.lock:
            if not self.current_group:
                return
            group_to_process = self.current_group.copy()
            self.current_group.clear()
            logger.info(f"Timeout! Processing group with {len(group_to_process)} image(s)...")

        # --- NEW INTELLIGENT WORKFLOW ---

        # Step 1: Classify the problem type
        problem_type = qwen_client.classify_problem_type(group_to_process)

        # Step 2: Select the appropriate prompt template
        prompt_template = config.PROMPT_TEMPLATES.get(problem_type, config.PROMPT_TEMPLATES["GENERAL"])
        logger.info(f"Step 2: Strategy selected. Using '{problem_type}' prompt.")

        # Step 3: Transcribe the images to text
        transcribed_text = qwen_client.transcribe_images(group_to_process)
        if not transcribed_text:
            logger.error("Transcription failed. Aborting workflow.")
            return

        # Step 4: Solve the problem using the transcribed text and the selected prompt
        final_answer = deepseek_client.ask_deepseek_for_analysis(transcribed_text, prompt_template)
        if not final_answer:
            logger.error("Solving failed. Aborting workflow.")
            return

        # --- Archiving the result ---
        title = parse_title_from_response(final_answer)
        timestamp = time.strftime("%Y%m%d-%H%M%S")

        if title:
            filename = f"{timestamp}_{sanitize_filename(title)}.txt"
        else:
            logger.warning("No title found in response, using default filename.")
            base_name = group_to_process[0].stem
            filename = f"{timestamp}_{base_name}_result.txt"

        solution_path = config.SOLUTION_DIR / filename

        try:
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
            logger.info(f"Solution successfully saved to: {solution_path}")
        except IOError as e:
            logger.error(f"Failed to save solution file: {e}")
            return

        # Move processed images
        logger.info(f"Moving processed images to '{config.PROCESSED_DIR}'...")
        for img_path in group_to_process:
            try:
                # Use a unique name to avoid conflicts
                destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"
                shutil.move(str(img_path), str(destination))
            except (IOError, FileNotFoundError) as e:
                logger.error(f"Failed to move image '{img_path.name}': {e}")