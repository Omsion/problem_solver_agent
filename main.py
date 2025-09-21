# -*- coding: utf-8 -*-
"""
main.py - 自动化多图解题Agent主程序入口
"""

import sys
import config
import file_monitor
from image_grouper import ImageGrouper
from utils import setup_logger

def main():
    """主执行函数"""
    logger = setup_logger()
    logger.info("="*50)
    logger.info("启动自动化解题Agent (双模型流水线)...")
    logger.info("="*50)

    try:
        config.initialize_directories()
    except SystemExit:
        logger.critical("Directory initialization failed. Exiting.")
        sys.exit(1)

    if not config.DASHSCOPE_API_KEY or not config.DEEPSEEK_API_KEY:
        logger.critical("Error: One or both API keys (DASHSCOPE_API_KEY, DEEPSEEK_API_KEY) are missing!")
        logger.critical("Please check your .env file.")
        return

    logger.info(f"Monitoring Directory: {config.MONITOR_DIR}")
    logger.info(f"Step 1 Model (Transcription): {config.QWEN_MODEL_NAME}")
    logger.info(f"Step 2 Model (Reasoning): {config.MODEL_NAME}")

    image_grouper = ImageGrouper()
    observer = file_monitor.start_monitoring(config.MONITOR_DIR, image_grouper)

    try:
        observer.join()
    except Exception as e:
        logger.critical(f"An unexpected error occurred in the file monitor: {e}")
    finally:
        observer.stop()
        logger.info("File monitor has been shut down.")
    # -------------------------------------------------------------------------------------


if __name__ == "__main__":
    main()
