# -*- coding: utf-8 -*-
"""
main.py - 自动化多图解题Agent主程序入口
"""

import sys
import config
import file_monitor
from image_grouper import ImageGrouper
from utils import setup_logger
import solver_client

def main():
    """主执行函数 """
    logger = setup_logger()
    logger.info("=" * 50)
    logger.info("启动自动化解题Agent (统一客户端)...")
    logger.info("=" * 50)

    try:
        config.initialize_directories()
    except SystemExit:
        logger.critical("目录初始化失败，程序退出。")
        sys.exit(1)

    all_apis_ok = True
    if not config.DASHSCOPE_API_KEY:
        logger.critical("❌ 错误: DASHSCOPE_API_KEY (视觉模型) 缺失!")
        all_apis_ok = False
    else:
        logger.info("✓ Qwen-VL API密钥配置正常")

    logger.info(f"----- 检查核心求解器: {config.SOLVER_PROVIDER} -----")
    try:
        if solver_client.check_solver_health():
            logger.info(f"✓ 核心求解器 '{config.SOLVER_PROVIDER}' API连接测试通过")
        else:
            logger.error(f"✗ 核心求解器 '{config.SOLVER_PROVIDER}' API连接测试失败")
            all_apis_ok = False
    except Exception as e:
        logger.error(f"核心求解器 '{config.SOLVER_PROVIDER}' API连接测试异常: {e}")
        all_apis_ok = False

    if not all_apis_ok:
        logger.critical("API配置或健康检查失败，程序退出。")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info(f"监控目录: {config.MONITOR_DIR}")
    logger.info(f"视觉模型: {config.QWEN_MODEL_NAME}")
    logger.info(f"核心求解器: {config.SOLVER_PROVIDER} -> {config.SOLVER_MODEL_NAME}")
    logger.info(f"辅助模型: {config.AUX_PROVIDER} -> {config.AUX_MODEL_NAME}")
    logger.info("=" * 50)

    image_grouper = ImageGrouper()
    observer = file_monitor.start_monitoring(config.MONITOR_DIR, image_grouper)

    try:
        while observer.is_alive():
            observer.join(1)
    except KeyboardInterrupt:
        logger.info("接收到中断信号，正在关闭...")
    finally:
        observer.stop()
        observer.join()
        logger.info("文件监控已完全关闭。")


if __name__ == "__main__":
    main()