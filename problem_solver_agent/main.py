# -*- coding: utf-8 -*-
r"""
main.py - 自动化多图解题Agent主程序入口
conda activate llm && python D:\Users\wzw\Pictures\problem_solver_agent\main.py
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

    logger.info(f"----- 检查所有已配置的核心求解器 -----")
    # 遍历所有在 config 中定义的求解器并逐一检查健康状况
    for provider, details in config.SOLVER_CONFIG.items():
        try:
            model_to_check = details["model"]
            if solver_client.check_solver_health(provider, model_to_check):
                logger.info(f"✓ 核心求解器 '{provider}' API连接测试通过")
            else:
                logger.error(f"✗ 核心求解器 '{provider}' API连接测试失败")
                all_apis_ok = False
        except Exception as e:
            logger.error(f"核心求解器 '{provider}' API连接测试异常: {e}")
            all_apis_ok = False

    if not all_apis_ok:
        logger.critical("API配置或健康检查失败，程序退出。")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info(f"监控目录: {config.MONITOR_DIR}")
    logger.info(f"视觉OCR模型: {config.QWEN_MODEL_NAME}")
    logger.info(f"视觉图像推理模型: {config.QWEN_VL_THINKING_MODEL_NAME}")
    logger.info("核心求解器: [根据问题类型动态选择]")
    # 从新的配置中读取并显示路由规则
    logger.info(f"  - 编程类问题 -> {config.SOLVER_ROUTING_CONFIG['CODING_SOLVER']}")
    logger.info(f"  - 其他问题 -> {config.SOLVER_ROUTING_CONFIG['DEFAULT_SOLVER']}")
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