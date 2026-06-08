"""
pipeline.py — 共享流水线逻辑（CLI Agent 和 Web App 共用）

本模块包含：
1. 问题重分类（reclassify_problem_type）
2. 最终类型映射（map_final_type）
3. 求解器路由（determine_solver）
4. Prompt 模板选择（build_prompt）
5. 启动初始化与配置验证（initialize_directories / validate_config）
"""

import sys
from pathlib import Path

from . import config
from . import prompts


# ---------------------------------------------------------------------------
# 流水线核心函数
# ---------------------------------------------------------------------------

def reclassify_problem_type(problem_type: str, transcribed_text: str) -> str:
    """基于 OCR 文本检测 ML/编程关键词，修正视觉模型的初步分类。

    Args:
        problem_type: 视觉分类结果（GENERAL / CODING / FILL_IN_THE_BLANKS 等）
        transcribed_text: OCR + 润色后的文本

    Returns:
        修正后的问题类型（ML_CODING / CODING / 原类型）
    """
    reclassifiable = {"GENERAL", "FILL_IN_THE_BLANKS", "QUESTION_ANSWERING", "CODING"}
    if problem_type not in reclassifiable or transcribed_text == "N/A":
        return problem_type

    text_lower = transcribed_text.lower()
    if any(kw in text_lower for kw in config.ML_KEYWORDS):
        return "ML_CODING"
    if any(kw in text_lower for kw in config.CODING_KEYWORDS) and problem_type != "CODING":
        return "CODING"
    return problem_type


def map_final_type(problem_type: str, text: str) -> str:
    """映射最终问题类型：CODING → LEETCODE/ACM，ML_CODING 保持不变。

    Args:
        problem_type: 重分类后的问题类型
        text: 题目文本（用于检测 leetcode 关键词）

    Returns:
        最终问题类型
    """
    if problem_type == "ML_CODING":
        return "ML_CODING"
    if problem_type == "CODING":
        return "LEETCODE" if "leetcode" in text.lower() else "ACM"
    return problem_type


def determine_solver(final_type: str) -> tuple[str, str]:
    """根据最终问题类型和路由配置，选择求解器 provider 和 model。

    Args:
        final_type: 最终问题类型（LEETCODE / ACM / ML_CODING / 其他）

    Returns:
        (provider, model) 元组
    """
    if final_type in ("LEETCODE", "ACM", "ML_CODING"):
        provider = config.SOLVER_ROUTING_CONFIG["CODING_SOLVER"]
    else:
        provider = config.SOLVER_ROUTING_CONFIG["DEFAULT_SOLVER"]
    return provider, config.SOLVER_CONFIG[provider]["model"]


def build_prompt(final_type: str, transcribed_text: str) -> str:
    """根据最终问题类型构建求解器 Prompt。

    Args:
        final_type: 最终问题类型
        transcribed_text: 题目文本

    Returns:
        格式化后的 Prompt 字符串
    """
    template = prompts.PROMPT_TEMPLATES.get(final_type)
    if not template:
        raise ValueError(f"缺少 '{final_type}' 的 Prompt 模板")
    if final_type in ("LEETCODE", "ACM", "ML_CODING"):
        template = template[config.SOLUTION_STYLE]
    return template.replace("{transcribed_text}", transcribed_text)


# ---------------------------------------------------------------------------
# 启动初始化
# ---------------------------------------------------------------------------

def initialize_directories() -> None:
    """初始化项目所需的目录结构。"""
    print("正在初始化目录结构...")
    for dir_path in [config.PROCESSED_DIR, config.SOLUTION_DIR]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"  - 目录 '{dir_path}' 已确认存在。")
        except OSError as e:
            print(f"创建目录 '{dir_path}' 时发生错误: {e}")
            sys.exit(1)


def validate_config() -> None:
    """验证所有必需的配置项。

    检查项目：
    1. API 密钥是否已设置
    2. 关键路径是否存在或可创建
    3. 配置值是否在合理范围内

    Raises:
        ValueError: 当配置验证失败时抛出
    """
    errors: list[str] = []
    warnings: list[str] = []

    # API 密钥
    if not config.DEEPSEEK_API_KEY:
        errors.append("DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")
    if not config.ZHIPU_API_KEY:
        errors.append("ZHIPU_API_KEY 未设置，请在 .env 文件中配置")

    # 超时设置
    if config.API_TIMEOUT < 10:
        warnings.append(f"API_TIMEOUT ({config.API_TIMEOUT}s) 设置过小，可能导致大模型调用超时")
    if config.API_TIMEOUT > 3600:
        warnings.append(f"API_TIMEOUT ({config.API_TIMEOUT}s) 设置过大")

    # 重试设置
    if config.MAX_RETRIES < 0:
        errors.append(f"MAX_RETRIES ({config.MAX_RETRIES}) 不能为负数")
    if config.RETRY_DELAY < 0:
        errors.append(f"RETRY_DELAY ({config.RETRY_DELAY}s) 不能为负数")

    # 路径
    if not config.ROOT_DIR.exists():
        try:
            config.ROOT_DIR.mkdir(parents=True, exist_ok=True)
            warnings.append(f"ROOT_DIR 不存在，已自动创建: {config.ROOT_DIR}")
        except OSError as e:
            errors.append(f"无法创建 ROOT_DIR: {config.ROOT_DIR}, 错误: {e}")

    if not config.MONITOR_DIR.exists():
        try:
            config.MONITOR_DIR.mkdir(parents=True, exist_ok=True)
            warnings.append(f"MONITOR_DIR 不存在，已自动创建: {config.MONITOR_DIR}")
        except OSError as e:
            errors.append(f"无法创建 MONITOR_DIR: {config.MONITOR_DIR}, 错误: {e}")

    # 求解器配置
    for provider, cfg in config.SOLVER_CONFIG.items():
        if "model" not in cfg or not cfg["model"]:
            errors.append(f"求解器 '{provider}' 缺少 model 配置")
        if "base_url" not in cfg or not cfg["base_url"]:
            errors.append(f"求解器 '{provider}' 缺少 base_url 配置")

    # 路由配置
    if "CODING_SOLVER" not in config.SOLVER_ROUTING_CONFIG:
        errors.append("SOLVER_ROUTING_CONFIG 中缺少 'CODING_SOLVER' 配置")
    if "DEFAULT_SOLVER" not in config.SOLVER_ROUTING_CONFIG:
        errors.append("SOLVER_ROUTING_CONFIG 中缺少 'DEFAULT_SOLVER' 配置")

    if warnings:
        print("\n配置警告：")
        for w in warnings:
            print(f"  ⚠️  {w}")
        print()

    if errors:
        print("\n配置错误：")
        for e in errors:
            print(f"  ❌ {e}")
        raise ValueError("\n配置验证失败，请修复上述错误后重试。")

    print("✅ 配置验证通过。")
