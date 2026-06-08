"""
自动化多图解题 Agent (Automated Multi-Image Problem Solver Agent)

这是一个功能完备的自动化 AI 代理，通过实时监控、智能分组与多模型协同，
将连续截图自动转化为结构化 AI 解答。

模块结构:
    config       — 纯配置常量（API密钥、模型、路径、超时、关键词）
    prompts      — Prompt 模板（模块级常量）
    pipeline     — 共享流水线逻辑（重分类、类型映射、求解器路由、初始化/验证）
    vision_client — 视觉 API 客户端
    solver_client — 求解器 API 客户端
    image_grouper — CLI 调度器
    file_monitor  — 文件系统监控

公共 API:
    - ImageGrouper: 图片分组与处理核心调度器
    - start_monitoring: 启动文件系统监控
    - pipeline: 流水线共享函数模块

使用示例:
    >>> from problem_solver_agent import ImageGrouper, start_monitoring, pipeline
    >>> pipeline.initialize_directories()
    >>> grouper = ImageGrouper()
    >>> observer = start_monitoring(grouper)
    >>> observer.join()  # 阻塞等待

主入口:
    python -m problem_solver_agent.main
"""

# 核心类和函数导出
from .file_monitor import start_monitoring
from .image_grouper import ImageGrouper

# 子模块
from . import config, pipeline, prompts

# 定义包的公共 API
__all__ = [
    "ImageGrouper",
    "start_monitoring",
    "config",
    "pipeline",
    "prompts",
]

__version__ = "2.3.0"
__author__ = "WZW"
