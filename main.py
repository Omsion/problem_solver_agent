# -*- coding: utf-8 -*-
"""
main.py - 自动化多图解题Agent主程序入口 (V2.1)
"""

import sys
import config
import file_monitor
from image_grouper import ImageGrouper
from utils import setup_logger
# 导入 solver_client 以使用其动态健康检查功能
import solver_client

def main():
    """主执行函数 """
    logger = setup_logger()
    logger.info("=" * 50)
    logger.info("启动自动化解题Agent (多求解器流水线)...")
    logger.info("=" * 50)

    try:
        config.initialize_directories()
    except SystemExit:
        logger.critical("目录初始化失败，程序退出。")
        sys.exit(1)

    # --- API密钥与健康检查 ---
    # 动态检查当前配置的求解器和视觉模型
    all_apis_ok = True

    # 1. 检查视觉模型 (Qwen-VL)
    if not config.DASHSCOPE_API_KEY:
        logger.critical("❌ 错误: DASHSCOPE_API_KEY (视觉模型) 缺失!")
        all_apis_ok = False
    else:
        logger.info("✓ Qwen-VL API密钥配置正常")

    # 2. 检查核心求解器
    logger.info(f"----- 检查核心求解器: {config.SOLVER_PROVIDER} -----")
    try:
        # 调用统一的、动态的健康检查，而不是硬编码检查DeepSeek
        if solver_client.check_solver_health():
            logger.info(f"✓ 核心求解器 '{config.SOLVER_PROVIDER}' API连接测试通过")
        else:
            logger.error(f"✗ 核心求解器 '{config.SOLVER_PROVIDER}' API连接测试失败")
            all_apis_ok = False
    except Exception as e:
        logger.error(f"核心求解器 '{config.SOLVER_PROVIDER}' API连接测试异常: {e}")
        all_apis_ok = False

    if not all_apis_ok:
        logger.critical("API配置或健康检查失败，程序退出。请检查 .env 文件和网络连接。")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info(f"监控目录: {config.MONITOR_DIR}")
    logger.info(f"视觉模型 (分类/转录): {config.QWEN_MODEL_NAME}")
    # 打印正确的求解器模型变量
    logger.info(f"核心求解器 (推理): {config.SOLVER_PROVIDER} -> {config.SOLVER_MODEL_NAME}")
    logger.info("=" * 50)

    image_grouper = ImageGrouper()
    observer = file_monitor.start_monitoring(config.MONITOR_DIR, image_grouper)

    try:
        # 主线程阻塞，等待监控线程（以及守护的工作线程）
        while observer.is_alive():
            observer.join(1)
    except KeyboardInterrupt:
        logger.info("接收到中断信号，正在关闭...")
    except Exception as e:
        logger.critical(f"文件监控发生意外错误: {e}", exc_info=True)
    finally:
        observer.stop()
        observer.join()
        logger.info("文件监控已完全关闭。")


if __name__ == "__main__":
    main()