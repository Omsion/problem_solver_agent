"""
pipeline.py — 共享流水线逻辑（CLI Agent 和 Web App 共用）

本模块包含：
1. 最终类型映射（map_final_type）
2. 求解器路由（determine_solver）
3. Prompt 模板选择（build_prompt）
4. 启动初始化与配置验证（initialize_directories / validate_config）

历史说明：这里曾有一个 `reclassify_problem_type`，靠 `ML_KEYWORDS` / `CODING_KEYWORDS`
做关键词匹配来产生 `ML_CODING` 标签。它全仓无调用点、无测试覆盖，等于一条不可达的
死路径（`ML_CODING` 的求解模板、`map_final_type` 分支、前端展示映射全成了摆设）。
现已把 `ML_CODING` 上移到视觉分类 prompt 的标签表 —— 视觉模型读得到题面全文，
比关键词匹配准得多 —— 然后删掉这个函数与两个关键词表。
"""

import sys
from pathlib import Path

from . import config
from . import prompts


# ---------------------------------------------------------------------------
# 流水线核心函数
# ---------------------------------------------------------------------------

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


def build_prompt(final_type: str, transcribed_text: str, style: str | None = None) -> str:
    """根据最终问题类型构建求解器 Prompt。

    Args:
        final_type: 最终问题类型
        transcribed_text: 题目文本
        style: 编程题的求解风格（OPTIMAL / EXPLORATORY）；None 时用全局配置

    Returns:
        格式化后的 Prompt 字符串
    """
    template = prompts.PROMPT_TEMPLATES.get(final_type)
    if not template:
        raise ValueError(f"缺少 '{final_type}' 的 Prompt 模板")
    if final_type in ("LEETCODE", "ACM", "ML_CODING"):
        chosen = (style or config.SOLUTION_STYLE).upper()
        if chosen not in template:
            chosen = config.SOLUTION_STYLE
        template = template[chosen]
    # 用 replace 而不是 format：题目正文里的花括号不应被当作占位符
    prompt = template.replace("{transcribed_text}", transcribed_text)
    # T4：把"文件名建议"并进求解首行，省掉一次隐形的辅助模型调用。
    # 由 `core_pipeline._run_solve_attempt` 负责剥掉首行，不进解答正文。
    return prompt + prompts.FILENAME_SUGGESTION_INSTRUCTION


# ---------------------------------------------------------------------------
# 启动初始化
# ---------------------------------------------------------------------------

def initialize_directories() -> None:
    """初始化项目所需的目录结构。"""
    print("正在初始化目录结构...")
    for dir_path in [config.PROCESSED_DIR, config.SOLUTION_DIR, config.OCR_DIR]:
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
    # 视觉层密钥按 provider 判定：默认 deepseek 时与求解密钥同一个，
    # 切成 zhipu 后要求的是 ZHIPU_API_KEY —— 校验逻辑不跟着 provider 改。
    if not config.VISION_API_KEY:
        errors.append(
            f"视觉层密钥未设置（VISION_PROVIDER={config.VISION_PROVIDER}，"
            f"需要 {config.VISION_PROVIDER_CONFIG[config.VISION_PROVIDER]['api_key_env']}），"
            f"请在 .env 文件中配置"
        )

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
