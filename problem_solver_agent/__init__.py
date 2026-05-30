# -*- coding: utf-8 -*-
"""
自动化多图解题 Agent (Automated Multi-Image Problem Solver Agent)

这是一个功能完备的自动化 AI 代理，通过实时监控、智能分组与多模型协同，
将连续截图自动转化为结构化 AI 解答。

公共 API:
    - ImageGrouper: 图片分组与处理核心调度器
    - start_monitoring: 启动文件系统监控

使用示例:
    >>> from problem_solver_agent import ImageGrouper, start_monitoring
    >>> grouper = ImageGrouper(num_workers=8)
    >>> observer = start_monitoring(grouper)
    >>> observer.join()  # 阻塞等待

主入口:
    python -m problem_solver_agent.main
"""

# 核心类和函数导出
from .image_grouper import ImageGrouper
from .file_monitor import start_monitoring

# 定义包的公共 API
__all__ = [
    "ImageGrouper",
    "start_monitoring",
]

__version__ = "2.3.0"
__author__ = "WZW"
