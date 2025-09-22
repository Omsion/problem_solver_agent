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
    """主执行函数 """
    logger = setup_logger()
    logger.info("=" * 50)
    logger.info("启动自动化解题Agent (双模型流水线)...")
    logger.info("=" * 50)

    try:
        config.initialize_directories()
    except SystemExit:
        logger.critical("Directory initialization failed. Exiting.")
        sys.exit(1)

    # API密钥验证
    api_keys_valid = True

    if not config.DASHSCOPE_API_KEY:
        logger.critical("错误: DASHSCOPE_API_KEY 缺失!")
        api_keys_valid = False
    else:
        logger.info("✓ Qwen-VL API密钥配置正常")

    if not config.DEEPSEEK_API_KEY:
        logger.critical("错误: DEEPSEEK_API_KEY 缺失!")
        api_keys_valid = False
    else:
        logger.info("✓ DeepSeek API密钥配置正常")

        # 深度验证DeepSeek API连接
        try:
            from deepseek_client import check_deepseek_health
            if check_deepseek_health():
                logger.info("✓ DeepSeek API连接测试通过")
            else:
                logger.error("✗ DeepSeek API连接测试失败")
                api_keys_valid = False
        except Exception as e:
            logger.error(f"DeepSeek API连接测试异常: {e}")
            api_keys_valid = False

    if not api_keys_valid:
        logger.critical("API配置验证失败，程序退出")
        sys.exit(1)

    logger.info(f"监控目录: {config.MONITOR_DIR}")
    logger.info(f"第一步模型 (转录): {config.QWEN_MODEL_NAME}")
    logger.info(f"第二步模型 (推理): {config.MODEL_NAME}")

    image_grouper = ImageGrouper()
    observer = file_monitor.start_monitoring(config.MONITOR_DIR, image_grouper)

    try:
        observer.join()
    except KeyboardInterrupt:
        logger.info("接收到中断信号，正在关闭...")
    except Exception as e:
        logger.critical(f"文件监控发生意外错误: {e}")
    finally:
        observer.stop()
        observer.join()
        logger.info("文件监控已完全关闭。")


if __name__ == "__main__":
    main()

