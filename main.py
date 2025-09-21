# -*- coding: utf-8 -*-
"""
main.py - 自动化多图解题Agent主程序入口

职责：
1. 初始化整个应用环境（加载配置、创建文件夹、设置日志）。
2. 实例化核心组件（ImageGrouper, FileMonitor）。
3. 启动文件监控。
4. 保持主线程运行，并优雅地处理退出信号（如 Ctrl+C）。
"""

import time
import sys

# 从项目中导入所有模块和配置
import config
import file_monitor
from image_grouper import ImageGrouper
from utils import setup_logger

def main():
    """主执行函数"""
    logger = setup_logger()
    logger.info("="*50)
    logger.info("自动化多图解题Agent启动...")
    logger.info("="*50)

    try:
        config.initialize_directories()
    except SystemExit:
        logger.critical("目录初始化失败，程序即将退出。")
        sys.exit(1)

    if not config.DEEPSEEK_API_KEY:
        logger.critical("错误：未找到 DEEPSEEK_API_KEY！")
        logger.critical("请在项目根目录创建 .env 文件，并添加 DEEPSEEK_API_KEY='your_key'。")
        return

    logger.info(f"监控目录: {config.MONITOR_DIR}")
    logger.info(f"已处理目录: {config.PROCESSED_DIR}")
    logger.info(f"解答保存目录: {config.SOLUTION_DIR}")
    logger.info(f"分组超时设置为: {config.GROUP_TIMEOUT} 秒")
    # ### UPDATED ### - 启动时显示当前选择的模式
    logger.info(f"DeepSeek模型模式: {config.DEEPSEEK_MODEL_MODE} (模型: {config.MODEL_NAME})")

    image_grouper = ImageGrouper()
    observer = file_monitor.start_monitoring(config.MONITOR_DIR, image_grouper)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n检测到用户中断 (Ctrl+C)，正在优雅地关闭程序...")
        observer.stop()
        observer.join()
        logger.info("文件监控已停止。程序退出。")
    except Exception as e:
        logger.critical(f"发生未预料的错误: {e}")
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()