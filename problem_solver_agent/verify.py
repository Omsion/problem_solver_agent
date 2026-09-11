"""
verify.py - 核对模式

用第二个视觉模型对照原图复核已生成的解答，输出 `agree` / `disagree` / `unclear`。

设计要点：
- **默认关闭**。由用户在界面上主动点击「核对答案」才触发，不改变默认出答案速度
- **不修改已有解答**：核对结果单独返回，由调用方决定是否追加到解答文件，
  避免一次误判把正确答案改坏
- **失败不影响主流程**：无法解析模型输出时按 `unclear` 处理并给出原因
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import config, prompts, vision_client

logger = logging.getLogger("Verify")

# 送进核对 Prompt 的解答文本上限（字符）。过长会挤占图片 token 且收益很低。
MAX_ANSWER_CHARS = 20_000

VALID_VERDICTS = ("agree", "disagree", "unclear")


@dataclass
class VerificationResult:
    """一次核对的结果。"""

    verdict: str
    issues: list[str] = field(default_factory=list)
    corrections: str = ""
    reason: str = ""
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "issues": list(self.issues),
            "corrections": self.corrections,
            "reason": self.reason,
            "model": self.model,
        }

    @property
    def has_problems(self) -> bool:
        return self.verdict == "disagree"

    def as_markdown(self) -> str:
        """渲染成追加到解答文件末尾的小节。"""
        label = {
            "agree": "✅ 核对通过",
            "disagree": "⚠️ 核对发现疑点",
            "unclear": "❔ 无法判定",
        }.get(self.verdict, self.verdict)

        lines = ["", "---", "", "## 核对结果", "", f"**{label}**", ""]
        if self.issues:
            lines.append("**发现的问题：**")
            lines.append("")
            for issue in self.issues:
                lines.append(f"- {issue}")
            lines.append("")
        if self.corrections:
            lines.append("**建议修正：**")
            lines.append("")
            lines.append(self.corrections)
            lines.append("")
        if self.reason:
            lines.append(f"> {self.reason}")
            lines.append("")
        if self.model:
            lines.append(f"<sub>核对模型：{self.model}</sub>")
            lines.append("")
        return "\n".join(lines)


def _coerce_issues(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [str(raw)]


def parse_verification(raw: str | None, model: str = "") -> VerificationResult:
    """把模型输出解析成结构化结果。解析失败按 unclear 处理。"""
    parsed = vision_client.parse_json_response(raw)
    if not parsed:
        return VerificationResult(
            verdict="unclear",
            reason="模型返回内容无法解析为 JSON，无法给出核对结论",
            model=model,
        )

    verdict_raw = str(parsed.get("verdict", "")).strip().lower()
    if verdict_raw not in VALID_VERDICTS:
        # 常见变体做一次归一化
        if verdict_raw in ("ok", "pass", "correct", "yes", "true"):
            verdict_raw = "agree"
        elif verdict_raw in ("fail", "incorrect", "no", "false", "disagree"):
            verdict_raw = "disagree"
        else:
            verdict_raw = "unclear"

    issues = _coerce_issues(parsed.get("issues"))
    corrections = str(parsed.get("corrections") or "").strip()

    # 一致性保护：声称有问题却没给出任何线索时降级为 unclear，避免误报惊吓用户
    if verdict_raw == "disagree" and not issues and not corrections:
        return VerificationResult(
            verdict="unclear",
            reason="模型表示存在错误，但没有给出具体问题或修正建议",
            model=model,
        )

    return VerificationResult(
        verdict=verdict_raw,
        issues=issues,
        corrections=corrections,
        model=model,
    )


def verify_answer(image_paths: list[Path], answer_text: str, *, model: str | None = None) -> VerificationResult:
    """用视觉模型核对解答。

    Args:
        image_paths: 原始题目图片（会走 image_prep 压缩后再发送）
        answer_text: 待核对的解答正文
        model: 覆盖默认的视觉推理模型

    Returns:
        VerificationResult；任何异常都被捕获并转成 unclear，绝不抛出
    """
    if not image_paths:
        return VerificationResult(verdict="unclear", reason="缺少题目图片，无法核对")
    if not answer_text or not answer_text.strip():
        return VerificationResult(verdict="unclear", reason="解答内容为空，无法核对")

    used_model = model or config.VISION_REASONING_MODEL
    truncated = answer_text[:MAX_ANSWER_CHARS]
    note = ""
    if len(answer_text) > MAX_ANSWER_CHARS:
        note = f"\n\n（解答过长，仅送前 {MAX_ANSWER_CHARS} 字符用于核对）"

    prompt = prompts.ANSWER_VERIFICATION_PROMPT.replace("{answer}", truncated + note)

    try:
        raw = vision_client._call_vision_api(  # noqa: SLF001 - 复用统一的重试与压缩逻辑
            list(image_paths),
            prompt,
            used_model,
            stream=False,
        )
    except Exception as exc:  # pragma: no cover - 兜底
        logger.error("核对调用异常: %s", exc, exc_info=True)
        return VerificationResult(verdict="unclear", reason=f"核对调用失败：{exc}", model=used_model)

    if not isinstance(raw, str):
        return VerificationResult(verdict="unclear", reason="核对未获得有效响应", model=used_model)

    result = parse_verification(raw, model=used_model)
    logger.info("核对完成: verdict=%s issues=%d", result.verdict, len(result.issues))
    return result


def append_verification_to_solution(solution_path: Path, result: VerificationResult) -> bool:
    """把核对结果追加到解答文件末尾。失败只记录日志。"""
    try:
        path = Path(solution_path)
        if not path.exists():
            return False
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(result.as_markdown())
        logger.info("核对结果已写入 %s", path.name)
        return True
    except OSError as exc:
        logger.warning("写入核对结果失败: %s", exc)
        return False
