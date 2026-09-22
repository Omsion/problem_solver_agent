r"""
main.py - 自动化多图解题Agent主程序入口
conda activate llm; cd "D:\Users\wzw\Pictures\OnlineTest"; python -m problem_solver_agent.main
"""

import sys

from . import config, file_monitor, pipeline, solver_client
from .image_grouper import ImageGrouper
from .utils import setup_logger


def main():
    """主执行函数"""
    logger = setup_logger()
    logger.info("=" * 50)
    logger.info("启动自动化解题Agent (统一客户端)...")
    logger.info("=" * 50)

    try:
        pipeline.initialize_directories()
    except SystemExit:
        logger.critical("目录初始化失败，程序退出。")
        sys.exit(1)

    all_apis_ok = True
    # 视觉层密钥按 provider 判定（默认 deepseek，与求解共用 DEEPSEEK_API_KEY；
    # 设 VISION_PROVIDER=zhipu 才需要 ZHIPU_API_KEY）
    vision_key_env = config.VISION_PROVIDER_CONFIG[config.VISION_PROVIDER]["api_key_env"]
    if not config.VISION_API_KEY:
        logger.critical(
            "❌ 错误: 视觉层密钥 %s 缺失（VISION_PROVIDER=%s，模型=%s）!",
            vision_key_env, config.VISION_PROVIDER, config.VISION_CLASSIFY_MODEL,
        )
        all_apis_ok = False
    else:
        logger.info(
            "✓ 视觉层密钥配置正常（provider=%s，模型=%s，思考=%s）",
            config.VISION_PROVIDER, config.VISION_CLASSIFY_MODEL,
            "关闭" if config.VISION_DISABLE_THINKING else "开启",
        )

    logger.info("----- 检查所有已配置的核心求解器 -----")
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
    logger.info(f"OCR 归档目录: {config.OCR_DIR}")
    logger.info(
        f"视觉层: provider={config.VISION_PROVIDER} 模型={config.VISION_CLASSIFY_MODEL}"
        f"（回退：zhipu=GLM-4.6V-FlashX）"
    )
    logger.info(f"题目视觉分类模型: {config.VISION_CLASSIFY_MODEL}")
    logger.info(f"题目OCR模型: {config.VISION_CLASSIFY_MODEL}")
    logger.info(f"视觉推理模型: {config.VISION_REASONING_MODEL}")
    logger.info("核心求解器: [根据问题类型动态选择]")
    logger.info(f"  - 编程类问题 -> {config.SOLVER_ROUTING_CONFIG['CODING_SOLVER']}")
    logger.info(f"  - 其他问题 -> {config.SOLVER_ROUTING_CONFIG['DEFAULT_SOLVER']}")
    logger.info(f"辅助模型: {config.AUX_PROVIDER} -> {config.AUX_MODEL_NAME}")
    logger.info("=" * 50)

    image_grouper = ImageGrouper()
    # 启动时先清掉上一次进程残留的处理锁：强杀（任务管理器结束进程 / 崩溃）不会
    # 执行 `_execute_pipeline` 的 finally，锁会留在盘上，导致那一组图片被永久跳过。
    # 此刻不可能有本进程发起的在途任务，因此清锁是安全的。
    file_monitor.recover_stale_locks(config.SOLUTION_DIR)
    # file_monitor 现在接收任意回调，不再反向依赖 ImageGrouper；
    # on_group 用于补偿扫描（漏事件/停机期间到达的文件）整组补投
    observer = file_monitor.start_monitoring(
        config.MONITOR_DIR,
        image_grouper.add_image,
        on_group=image_grouper.submit_group,
    )

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
