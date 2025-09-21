# -*- coding: utf-8 -*-
"""
image_grouper.py - 图片分组处理器模块

实现项目的核心逻辑：
- 接收新图片事件。
- 使用定时器对在短时间内连续到达的图片进行分组。
- 定时器超时后，触发对整个图片组的处理流程。
- 负责调用API、解析结果、保存文件、移动图片等编排工作。
"""

import shutil
import time
from pathlib import Path
from threading import Timer, Lock
from typing import List

# 从项目中导入配置、API客户端和工具函数
import config
import deepseek_client
from utils import setup_logger, parse_title_from_response, sanitize_filename

# 初始化日志记录器
logger = setup_logger()


class ImageGrouper:
    """
    一个状态化的类，用于管理图片分组和处理。
    此类是线程安全的，使用锁来保护共享状态。
    """

    def __init__(self):
        self.current_group: List[Path] = []
        self.timer: Timer | None = None
        self.lock = Lock()  # 线程锁，用于保护 current_group 和 timer

    def add_image(self, image_path: Path):
        """
        向当前分组添加一张新图片，并重置超时定时器。
        这是由文件监控器调用的主要入口方法。
        """
        with self.lock:
            # 1. 如果存在旧的定时器，先取消它。这是分组逻辑的核心。
            if self.timer:
                self.timer.cancel()

            # 2. 将新图片路径添加到当前组
            self.current_group.append(image_path)
            logger.info(f"图片已添加到组: {image_path.name} (当前组共 {len(self.current_group)} 张)")

            # 3. 启动一个新的定时器。如果在超时时间内没有新图片加入，
            #    定时器将触发 _process_group 方法。
            self.timer = Timer(config.GROUP_TIMEOUT, self._process_group)
            self.timer.start()

    def _process_group(self):
        """
        处理当前图片组。此方法由定时器在后台线程中自动调用。
        """
        with self.lock:
            # 如果组为空（可能在某些边缘情况下发生），则直接返回
            if not self.current_group:
                return

            # 关键：创建一个当前组的副本进行处理，然后立即清空原始组。
            # 这使得在处理当前组（一个耗时操作）的同时，新的图片可以开始形成一个新组。
            group_to_process = self.current_group.copy()
            self.current_group.clear()
            logger.info(f"超时! 正在处理包含 {len(group_to_process)} 张图片的组...")

        # --- 以下操作在锁之外执行，因为API调用和文件IO是耗时的 ---

        # 1. 调用API客户端获取解答
        response_text = deepseek_client.ask_deepseek_with_images(group_to_process)

        if not response_text:
            logger.error("未能从API获取有效回答。图片将不会被移动，以便后续处理。")
            # 可选：在这里可以添加将图片移动到 "failed" 文件夹的逻辑
            return

        # 2. 解析标题并生成文件名
        title = parse_title_from_response(response_text)
        timestamp = time.strftime("%Y%m%d-%H%M%S")

        if title:
            clean_title = sanitize_filename(title)
            filename = f"{timestamp}_{clean_title}.txt"
        else:
            logger.warning("未能在响应中找到标题，将使用默认文件名。")
            # 使用第一张图片的原始文件名（不含扩展名）作为备用
            base_name = group_to_process[0].stem
            filename = f"{timestamp}_{base_name}_result.txt"

        solution_path = config.SOLUTION_DIR / filename

        # 3. 保存解答到文件
        try:
            with open(solution_path, 'w', encoding='utf-8') as f:
                f.write(f"处理的图片组:\n")
                for img_path in group_to_process:
                    f.write(f"- {img_path.name}\n")
                f.write("\n" + "=" * 50 + "\n\n")
                f.write("DeepSeek 模型解答:\n")
                f.write(response_text)
            logger.info(f"解答已成功保存到: {solution_path}")
        except IOError as e:
            logger.error(f"保存解答文件失败: {e}")
            return  # 如果保存失败，最好不要移动图片

        # 4. 移动已处理的图片
        logger.info(f"正在将已处理的图片移动到 '{config.PROCESSED_DIR}'...")
        for img_path in group_to_process:
            try:
                # 确保目标文件不存在，如果存在则添加一个后缀
                destination = config.PROCESSED_DIR / img_path.name
                if destination.exists():
                    destination = config.PROCESSED_DIR / f"{img_path.stem}_{timestamp}{img_path.suffix}"

                shutil.move(str(img_path), str(destination))
            except (IOError, FileNotFoundError) as e:
                logger.error(f"移动图片 '{img_path.name}' 失败: {e}")