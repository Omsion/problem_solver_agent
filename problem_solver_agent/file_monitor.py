# -*- coding: utf-8 -*-
"""
file_monitor.py - 文件系统监控模块

使用 'watchdog' 库来实时监控指定文件夹中的文件创建事件。
当检测到新的、符合条件的图片文件时，将事件传递给 ImageGrouper 进行处理。
"""

from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 从项目中导入配置和核心处理器
import config
from image_grouper import ImageGrouper
from utils import setup_logger

# 初始化日志记录器
logger = setup_logger()


class ImageEventHandler(FileSystemEventHandler):
    """
    自定义的文件系统事件处理器。
    只关心文件的 'on_created' 事件。
    """

    def __init__(self, grouper: ImageGrouper):
        self.image_grouper = grouper

    def on_created(self, event):
        """
        当在受监控目录中创建新文件时，此方法被调用。
        """
        # 1. 我们只关心文件，不关心目录
        if event.is_directory:
            return

        # 2. 检查文件扩展名是否在允许列表中
        src_path = Path(event.src_path)
        if src_path.suffix.lower() in config.ALLOWED_EXTENSIONS:
            logger.info(f"检测到新图片: {src_path.name}")
            # 3. 将图片路径传递给分组处理器
            self.image_grouper.add_image(src_path)


def start_monitoring(path: Path, grouper: ImageGrouper) -> Observer:
    """
    初始化并启动文件系统监控。

    Args:
        path (Path): 需要监控的文件夹路径。
        grouper (ImageGrouper): ImageGrouper的实例，用于处理事件。

    Returns:
        Observer: watchdog的观察者对象，主程序可以用它来管理监控进程。
    """
    event_handler = ImageEventHandler(grouper)
    observer = Observer()
    observer.schedule(event_handler, str(path), recursive=False)  # recursive=False 表示不监控子目录

    # 在后台线程中启动观察者
    observer.start()
    logger.info(f"文件监控已启动，正在监视目录: {path}")

    return observer