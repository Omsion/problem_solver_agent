"""
usage.py - 把 core 上报的用量画像换算成计费事件

core 只上报**可观测的事实**（阶段、模型、页数、输出字符数），不猜 token 数。
本模块负责把字符数估算成 token 并落库。

为什么是估算：标准 OpenAI SDK 只有流式响应结束后才能从 `usage` 字段取到精确
token；并非所有兼容网关都返回该字段。因此这里用保守的启发式换算，
目的是**限制滥用**，而不是出精确账单。

换算规则：
- 中文/日文等 CJK 字符约 1 token/字；英文约 4 字符/token
- 因此按"每字符 0.6 token"取中间偏保守值，并把图片按每页固定成本折算
"""

from __future__ import annotations

import logging

from .accounts import AccountManager, estimate_cost

logger = logging.getLogger("Usage")

# 每个字符折算的 token 数（CJK 偏 1.0，英文偏 0.25，取 0.6 作为折中）
CHARS_PER_TOKEN_FACTOR = 0.6
# 一张图片在视觉模型中的固定折算。
# 取 1024 是因为 DeepSeek 官方给出的**每图 token 上限**就是 1024（服务端会把图
# 二次缩放到约 1300×1300 等效）；沿用旧的 700 会在换到 deepseek 后低估近 1/3 的
# 输入成本 —— 额度系统宁可高估也不能低估。
TOKENS_PER_IMAGE = 1024
# 题面文本本身的输入开销（prompt 模板 + 上下文）
BASE_INPUT_TOKENS = 600


def estimate_tokens(pages: int, output_chars: int) -> tuple[int, int]:
    """估算 (输入 token, 输出 token)。

    输入侧只算图片与固定开销：题目文本的真实长度在 core 层没有回传，
    与其拍一个可能严重偏离的数字，不如只计可确定的部分。
    """
    input_tokens = BASE_INPUT_TOKENS + pages * TOKENS_PER_IMAGE
    output_tokens = int(output_chars * CHARS_PER_TOKEN_FACTOR)
    return input_tokens, output_tokens


class UsageRecorder:
    """把流水线上报的用量写入账户系统。"""

    def __init__(self, accounts: AccountManager) -> None:
        self.accounts = accounts

    def record(
        self,
        *,
        user_id: str,
        task_id: str | None,
        report: dict,
        tenant_id: str = "default",
    ) -> dict | None:
        """记录一次用量。

        Args:
            report: core 的 `usage` 事件负载，含 stage/model/provider/pages/output_chars/calls

        Returns:
            记账结果；失败返回 None（**绝不抛出**，记账不能影响解题）。
        """
        if not user_id:
            return None
        try:
            stage = str(report.get("stage") or "unknown")
            model = str(report.get("model") or "")
            provider = str(report.get("provider") or "")
            pages = int(report.get("pages") or 0)
            output_chars = int(report.get("output_chars") or 0)
            calls = max(1, int(report.get("calls") or 1))

            input_tokens, output_tokens = estimate_tokens(pages, output_chars)
            # 多次调用（例如逐页 OCR）按次数放大
            input_tokens *= calls
            output_tokens *= calls

            return self.accounts.record_usage(
                user_id=user_id,
                stage=stage,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                task_id=task_id,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.warning("用量记账失败（不影响解题）: %s", exc)
            return None

    def estimate_task_cost(self, pages: int, *, model: str = "deepseek-flash") -> float:
        """预估一次任务的大致费用，用于提交前的额度预检。

        按"1 次视觉调用 + 1 次求解"估算，偏保守（宁可高估也不能低估）。
        """
        # 视觉层的模型名必须跟随当前 provider 走（deepseek-flash / GLM-4.6V-FlashX），
        # 写死会在切换 provider 后按错的单价预检额度。
        from problem_solver_agent import config as core_config

        vision_input, _ = estimate_tokens(pages, 0)
        solve_input, solve_output = estimate_tokens(pages, 4000)
        return round(
            estimate_cost(core_config.VISION_CLASSIFY_MODEL, vision_input, 0)
            + estimate_cost(model, solve_input, solve_output),
            6,
        )
