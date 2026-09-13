"""
core_pipeline.py - 共享流水线（CLI Agent 与 Web 应用共用的唯一实现）

此前 `image_grouper.py`（CLI）与 `webapp/pipeline.py`（Web）各写了一份流程，
已经出现行为差异：CLI 用 `.replace("{raw_texts}")`、Web 用 `.format()`（后者会把
题目里的 `{}` 当占位符）、CLI 把求解器的思考过程 join 掉。这里统一成一份。

相比旧实现的关键优化（详见各步骤注释）：
1. 图片预处理：发送前统一缩放 + JPEG 压缩（实测体积缩小约 16 倍）
2. 分类 + 转录合并成一次视觉调用，失败自动回退
3. 回退路径下分类与逐页 OCR **并行**执行
4. 单图 / 短文本跳过"润色"调用
5. 部分页 OCR 失败时回退为"原图直读"，而不是整体失败
6. 每个阶段记录耗时，取消在每个阶段边界与流式分片之间生效
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config, image_prep, prompts, solver_client, vision_client
from .answer_card import extract_answer_card
from .cancel import CancelToken, CancelledError
from .utils import extract_question_numbers, format_number_prefix, sanitize_filename

logger = logging.getLogger("CorePipeline")

# 事件类型（前端与 CLI 共用）
EVENT_STATUS = "status"
EVENT_REASONING = "reasoning"
EVENT_CHUNK = "chunk"
EVENT_TIMINGS = "timings"
EVENT_DONE = "done"
EVENT_ERROR = "error"
EVENT_CANCELLED = "cancelled"
EVENT_USAGE = "usage"


@dataclass
class UsageReport:
    """一次调用或一个阶段的用量画像。

    这里上报的是**可观测的事实**（阶段、模型、页数、输出字符数），而不是精确
    token 数：标准 OpenAI SDK 只有流式结束后才能从 `usage` 字段取到精确值，
    且并非所有兼容网关都会返回。字符→token 的换算属于估算，放在 Web 层做
    （见 `webapp/usage.py`），core 只负责如实上报观测到的内容。
    """

    stage: str
    model: str
    provider: str = ""
    pages: int = 0
    output_chars: int = 0
    calls: int = 1

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "model": self.model,
            "provider": self.provider,
            "pages": self.pages,
            "output_chars": self.output_chars,
            "calls": self.calls,
        }


@dataclass
class StageTimings:
    """各阶段耗时（毫秒），用于回答"到底慢在哪"。"""

    classify: int = 0
    ocr: int = 0
    polish: int = 0
    solve: int = 0
    total: int = 0
    cached: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "classify": self.classify,
            "ocr": self.ocr,
            "polish": self.polish,
            "solve": self.solve,
            "total": self.total,
            "cached": list(self.cached),
        }


class SolutionPipeline:
    """把一组图片转换成结构化解答。

    使用方式::

        pipeline = SolutionPipeline(solution_dir=..., on_event=callback)
        result = pipeline.run(task_id, image_paths, cancel=token)
    """

    def __init__(
        self,
        solution_dir: Path,
        on_event: Callable[[dict], None] | None = None,
        *,
        stage_cache: "StageCache | None" = None,
        archive_dir: Path | None = None,
        write_failure_log: bool = True,
    ) -> None:
        self.solution_dir = Path(solution_dir)
        self.solution_dir.mkdir(parents=True, exist_ok=True)
        self.on_event = on_event or (lambda event: None)
        self.stage_cache = stage_cache
        # CLI 场景：处理完成后把原图归档到 PROCESSED_DIR
        self.archive_dir = archive_dir
        self.write_failure_log = write_failure_log

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        try:
            self.on_event(event)
        except Exception as exc:  # 回调异常不能拖垮流水线
            logger.warning("事件回调异常: %s", exc)

    def _emit_usage(self, report: UsageReport) -> None:
        """上报一次用量。

        记账问题绝不能让解题失败，因此这里全程吞异常只记日志。
        """
        try:
            self._emit({"type": EVENT_USAGE, **report.to_dict()})
        except Exception as exc:  # pragma: no cover - _emit 内部已兜底
            logger.warning("用量上报异常: %s", exc)

    def _status(self, phase: str, message: str, **extra) -> None:
        payload = {"type": EVENT_STATUS, "phase": phase, "message": message}
        payload.update(extra)
        self._emit(payload)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(
        self,
        task_id: str,
        image_paths: list[Path],
        *,
        cancel: CancelToken | None = None,
        enable_thinking: bool | None = None,
        style: str | None = None,
    ) -> dict:
        """执行完整流水线。

        Args:
            enable_thinking: 是否首选思考模式；None = 用 `config.SOLVER_THINKING_DEFAULT`
                （默认不首选，答案不合格时由 `_solve_with_escalation` 自动升级到思考档）。

        Returns:
            {"status": "completed"|"cancelled", "path": Path|None,
             "text": str, "problem_type": str, "timings": dict, "answer_card": dict}

        Raises:
            上游异常原样抛出；取消时抛 CancelledError（调用方负责保留部分文件）。
        """
        cancel = cancel or CancelToken()
        if enable_thinking is None:
            enable_thinking = config.SOLVER_THINKING_DEFAULT
        image_paths = [Path(p) for p in image_paths]
        timings = StageTimings()
        started = time.time()
        temp_path = self.solution_dir / f"{task_id}_inprogress.md"
        final_path: Path | None = None
        transcribed_text = "N/A"
        final_type = "GENERAL"
        provider = ""
        model = ""

        try:
            self._status("classifying", f"正在分析 {len(image_paths)} 张图片…")

            # ---- 步骤 1+2：分类与转录 ----
            cancel.raise_if_cancelled()
            classify_started = time.time()

            combined = self._cached_or_compute(
                task_id,
                "vision",
                lambda: vision_client.classify_and_transcribe(image_paths),
                timings,
            )
            if combined:
                problem_type = combined["problem_type"]
                pages = combined["pages"]
                failed_pages: list[int] = []
                timings.classify = int((time.time() - classify_started) * 1000)
                timings.ocr = timings.classify
                # 合并调用：一次请求同时完成分类与全部页面转录
                self._emit_usage(UsageReport(
                    stage="vision",
                    model=config.VISION_CLASSIFY_MODEL,
                    provider=config.VISION_PROVIDER_NAME,
                    pages=len(image_paths),
                    calls=1,
                ))
            else:
                # 回退：分类与逐页 OCR 并行，关键路径从相加变成取最大
                problem_type, pages, failed_pages = vision_client.classify_and_transcribe_parallel(image_paths)
                timings.classify = int((time.time() - classify_started) * 1000)
                timings.ocr = timings.classify
                # 回退路径：1 次分类 + 每页 1 次转录
                self._emit_usage(UsageReport(
                    stage="classify",
                    model=config.VISION_CLASSIFY_MODEL,
                    provider=config.VISION_PROVIDER_NAME,
                    pages=len(image_paths),
                    calls=1,
                ))
                self._emit_usage(UsageReport(
                    stage="ocr",
                    model=config.VISION_CLASSIFY_MODEL,
                    provider=config.VISION_PROVIDER_NAME,
                    pages=len(image_paths),
                    calls=len(image_paths),
                ))

            cancel.raise_if_cancelled()
            logger.info("题型=%s，转录成功 %d/%d 页", problem_type, len(image_paths) - len(failed_pages), len(image_paths))

            # ---- 步骤 3：文本合并 / 润色（可跳过）----
            ocr_fallback = problem_type != "VISUAL_REASONING" and bool(failed_pages)
            if problem_type == "VISUAL_REASONING":
                transcribed_text = "N/A"
            else:
                transcribed_text, polish_ms = self._textualize(
                    task_id, pages, failed_pages, cancel, timings
                )
                timings.polish = polish_ms
                if polish_ms > 0:
                    # 只有真正调用了润色模型才计入用量
                    self._emit_usage(UsageReport(
                        stage="polish",
                        model=config.AUX_MODEL_NAME,
                        provider=config.AUX_PROVIDER,
                        output_chars=len(transcribed_text),
                        calls=1,
                    ))

            # ---- 步骤 4：求解 ----
            cancel.raise_if_cancelled()
            self._status("solving", "正在调用求解器生成解答…")
            solve_started = time.time()
            answer_text, provider, model, final_type = self._solve_with_escalation(
                temp_path=temp_path,
                problem_type=problem_type,
                transcribed_text=transcribed_text,
                image_paths=image_paths,
                enable_thinking=enable_thinking,
                style=style,
                ocr_fallback=ocr_fallback,
                timings=timings,
                cancel=cancel,
            )

            timings.solve = int((time.time() - solve_started) * 1000)
            if not answer_text.strip() or "--- ERROR ---" in answer_text:
                raise RuntimeError(f"求解器返回空响应或包含内部错误（模型 {model}）")

            # 求解阶段用量（输入按题目文本长度估算，输出按实际生成字符数）
            self._emit_usage(UsageReport(
                stage="solve",
                model=model,
                provider=provider,
                output_chars=len(answer_text),
                calls=1,
            ))

            # ---- 步骤 5：答案卡 + 命名 + 归档 ----
            cancel.raise_if_cancelled()
            self._status("archiving", "正在整理解答文件…")
            card = extract_answer_card(answer_text)
            timings.total = int((time.time() - started) * 1000)

            final_path = self._generate_filename(transcribed_text, final_type, task_id)
            temp_path.replace(final_path)
            logger.info("解答已保存: %s", final_path)

            result = {
                "status": "completed",
                "path": final_path,
                "text": answer_text,
                "problem_type": final_type,
                "provider": provider,
                "model": model,
                "timings": timings.to_dict(),
                "answer_card": card,
            }
            self._emit({"type": EVENT_TIMINGS, "timings": timings.to_dict()})
            self._emit({
                "type": EVENT_DONE,
                "task_id": task_id,
                "filename": final_path.name,
                "answer_card": card,
                "timings": timings.to_dict(),
            })
            return result

        except CancelledError:
            # 保留已生成的部分内容：改名为 .partial.md，便于用户查看
            partial = self._preserve_partial(temp_path, task_id)
            timings.total = int((time.time() - started) * 1000)
            logger.info("任务 %s 已取消，部分内容保留于 %s", task_id, partial)
            self._emit({
                "type": EVENT_CANCELLED,
                "task_id": task_id,
                "message": "任务已取消，已保留已生成的内容",
                "partial_path": str(partial) if partial else None,
                "timings": timings.to_dict(),
            })
            return {
                "status": "cancelled",
                "path": partial,
                "text": partial.read_text(encoding="utf-8") if partial and partial.exists() else "",
                "problem_type": final_type,
                "timings": timings.to_dict(),
            }

        except Exception as exc:
            logger.error("流水线失败 task=%s: %s", task_id, exc, exc_info=True)
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            if self.write_failure_log:
                self._write_failure_log(image_paths, str(exc), transcribed_text)
            self._emit({"type": EVENT_ERROR, "task_id": task_id, "message": str(exc)})
            raise

        finally:
            # CLI 场景：无论成功失败都归档原图，避免重复处理同一张截图
            if self.archive_dir is not None:
                self._archive_images(image_paths)

    def resolve(
        self,
        task_id: str,
        problem_type: str,
        transcribed_text: str,
        *,
        enable_thinking: bool | None = None,
        style: str | None = None,
        cancel: CancelToken | None = None,
        image_paths: list[Path] | None = None,
    ) -> dict:
        """**换路重解**：跳过分类与识别，直接用已有题目文本重新求解。

        用途：第一版答案不满意时，换个求解风格（OPTIMAL / EXPLORATORY）、
        开关思考模式、或换模型再要一版。因为跳过了视觉步骤，
        整个过程只有一次求解调用，通常几秒到几十秒就能出第二版答案。

        Args:
            image_paths: 仅在 `problem_type == "VISUAL_REASONING"` 时需要（原图直读）

        Returns:
            与 `run()` 相同结构的结果字典。
        """
        cancel = cancel or CancelToken()
        if enable_thinking is None:
            enable_thinking = config.SOLVER_THINKING_DEFAULT
        if not transcribed_text or not transcribed_text.strip():
            raise ValueError("缺少题目文本，无法重新求解（请先完成一次完整处理）")

        timings = StageTimings(cached=["classify", "ocr", "polish"])
        started = time.time()
        temp_path = self.solution_dir / f"{task_id}_resolve.md"
        final_path: Path | None = None

        try:
            self._status("solving", "正在用新的求解策略生成解答…")
            cancel.raise_if_cancelled()

            solve_started = time.time()
            answer_text, provider, model, final_type = self._solve_with_escalation(
                temp_path=temp_path,
                problem_type=problem_type,
                transcribed_text=transcribed_text,
                image_paths=list(image_paths or []),
                enable_thinking=enable_thinking,
                style=style,
                ocr_fallback=False,
                timings=timings,
                cancel=cancel,
            )

            timings.solve = int((time.time() - solve_started) * 1000)
            if not answer_text.strip() or "--- ERROR ---" in answer_text:
                raise RuntimeError(f"求解器返回空响应或包含内部错误（模型 {model}）")

            # 换路重解也要计入用量，否则用户能靠反复重解绕过计费
            self._emit_usage(UsageReport(
                stage="resolve",
                model=model,
                provider=provider,
                output_chars=len(answer_text),
                calls=1,
            ))

            cancel.raise_if_cancelled()
            card = extract_answer_card(answer_text)
            timings.total = int((time.time() - started) * 1000)

            final_path = self._generate_filename(
                f"{transcribed_text}\n\n[重解:{style or config.SOLUTION_STYLE}/{final_type}]",
                final_type,
                task_id,
            )
            temp_path.replace(final_path)

            result = {
                "status": "completed",
                "path": final_path,
                "text": answer_text,
                "problem_type": final_type,
                "provider": provider,
                "model": model,
                "timings": timings.to_dict(),
                "answer_card": card,
                "resolved": True,
            }
            self._emit({"type": EVENT_TIMINGS, "timings": timings.to_dict()})
            self._emit({
                "type": EVENT_DONE,
                "task_id": task_id,
                "filename": final_path.name,
                "answer_card": card,
                "timings": timings.to_dict(),
                "resolved": True,
            })
            return result

        except CancelledError:
            partial = self._preserve_partial(temp_path, f"{task_id}_resolve")
            self._emit({
                "type": EVENT_CANCELLED,
                "task_id": task_id,
                "message": "重新求解已取消",
                "partial_path": str(partial) if partial else None,
            })
            return {"status": "cancelled", "path": partial, "text": "", "problem_type": problem_type}

        except Exception as exc:
            logger.error("重新求解失败 task=%s: %s", task_id, exc, exc_info=True)
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            self._emit({"type": EVENT_ERROR, "task_id": task_id, "message": str(exc)})
            raise

    # ------------------------------------------------------------------
    # 各步骤实现
    # ------------------------------------------------------------------

    def _cached_or_compute(self, task_id: str, stage: str, producer, timings: StageTimings):
        """优先读阶段缓存（重试时复用，避免重复付费）。"""
        if self.stage_cache is not None:
            cached = self.stage_cache.get(task_id, stage)
            if cached is not None:
                logger.info("命中阶段缓存: %s", stage)
                timings.cached.append(stage)
                return cached
        value = producer()
        if value and self.stage_cache is not None:
            self.stage_cache.set(task_id, stage, value)
        return value

    def _textualize(
        self,
        task_id: str,
        pages: list[str],
        failed_pages: list[int],
        cancel: CancelToken,
        timings: StageTimings,
    ) -> tuple[str, int]:
        """把逐页文本合并成题目文本；必要时才调用润色模型。

        Returns:
            (题目文本, 润色耗时毫秒)
        """
        if len(pages) == 1:
            # 单图无需"合并去重"，直接采用 OCR 原文（省一次 2-10 秒的调用）
            text = pages[0].strip()
            logger.info("单图任务，跳过润色步骤")
            return text, 0

        joined = "\n---[NEXT]---\n".join(page.strip() for page in pages)
        if len(joined) < config.MERGE_SKIP_THRESHOLD:
            logger.info("合并文本较短（%d 字符），跳过润色步骤", len(joined))
            return joined, 0

        cancel.raise_if_cancelled()
        started = time.time()
        # 统一用 replace 而不是 format：题目里的花括号不该被当成占位符
        prompt = prompts.TEXT_MERGE_AND_POLISH_PROMPT.replace("{raw_texts}", joined)
        polished = solver_client.ask_for_analysis(
            prompt, provider=config.AUX_PROVIDER, model=config.AUX_MODEL_NAME
        )
        elapsed = int((time.time() - started) * 1000)
        if not polished or len(polished.strip()) < 5:
            logger.warning("润色结果不可用，回退使用原始转录文本")
            return joined, elapsed
        return polished, elapsed

    def _run_solve_attempt(
        self,
        *,
        path: Path,
        stream,
        provider: str,
        model: str,
        final_type: str,
        transcribed_text: str,
        image_paths: list[Path],
        timings: StageTimings,
        ocr_fallback: bool,
        cancel: CancelToken,
    ) -> tuple[str, dict, str | None]:
        """消费一次求解事件流并写入文件。

        Returns:
            (正文, 画像 meta, 错误信息)。`meta` 为空表示这是**调用层面**的失败
            （网络/鉴权等），而不是"模型没写出正文"——两者处理方式不同。
        """
        chunks: list[str] = []
        meta: dict = {}
        error: str | None = None
        with open(path, "w", encoding="utf-8") as handle:
            self._write_header(
                handle, image_paths, final_type, transcribed_text,
                provider, model, timings, ocr_fallback,
            )
            for event in stream:
                # 取消在每个分片之间生效：用户点取消后最多再等一个分片
                if cancel.cancelled:
                    handle.flush()
                    raise CancelledError("任务已取消")
                event_type = event.get("type") if isinstance(event, dict) else "content"
                if event_type == "reasoning":
                    self._emit({"type": EVENT_REASONING, "content": event["content"]})
                elif event_type == "meta":
                    # 求解画像：不给前端，只用来判断这一版答案是否合格
                    meta = event
                elif event_type == "error":
                    error = str(event.get("content", "求解器返回错误"))
                    break
                else:
                    content = event.get("content", "") if isinstance(event, dict) else str(event)
                    chunks.append(content)
                    handle.write(content)
                    handle.flush()
                    self._emit({"type": EVENT_CHUNK, "content": content})
        return "".join(chunks), meta, error

    # 这些题型的答案"必然不短"（至少要有代码），过短本身就是异常信号。
    # 选择题/填空题的答案天然可以很短（"选 B"），不能因为短就重跑。
    _LONG_ANSWER_TYPES = ("LEETCODE", "ACM", "ML_CODING")

    def _should_escalate(
        self,
        text: str,
        meta: dict,
        *,
        enable_thinking: bool,
        final_type: str,
    ) -> bool:
        """首选档答案不合格时，是否值得用"开思考 + 大配额"重跑一次。

        注意：这里只判断"答案是否**存在且完整**"，不判断对错——判对错靠"核对"功能，
        不在这里做（拿不准的重跑只会白花钱）。
        """
        if not config.SOLVER_ESCALATE_TO_THINKING or enable_thinking:
            return False
        if meta.get("thinking"):
            return False
        body = text.strip()
        if not body or "--- ERROR ---" in text:
            return True
        if meta.get("truncated"):
            return True
        if final_type in self._LONG_ANSWER_TYPES:
            return len(body) < config.SOLVER_ESCALATE_MIN_CHARS
        return False

    def _solve_with_escalation(
        self,
        *,
        temp_path: Path,
        problem_type: str,
        transcribed_text: str,
        image_paths: list[Path],
        enable_thinking: bool,
        style: str | None,
        ocr_fallback: bool,
        timings: StageTimings,
        cancel: CancelToken,
    ) -> tuple[str, str, str, str]:
        """求解，必要时"按需升级"。

        先按首选档跑一次（默认不开思考，简单题十几秒出答案）；只有答案不合格
        （空 / 被截断 / 过短）时才升级到"开思考 + SOLVER_ESCALATE_MAX_TOKENS"。
        升级档同样没写出正文时**沿用首选档结果**，不做第三次调用。

        Returns:
            (answer_text, provider, model, final_type)
        """
        final_type, provider, model, stream = self._start_solve(
            problem_type, transcribed_text, image_paths, enable_thinking, style, ocr_fallback
        )
        text, meta, error = self._run_solve_attempt(
            path=temp_path,
            stream=stream,
            provider=provider,
            model=model,
            final_type=final_type,
            transcribed_text=transcribed_text,
            image_paths=image_paths,
            timings=timings,
            ocr_fallback=ocr_fallback,
            cancel=cancel,
        )
        if error and not meta:
            # 连画像都没有 → 调用就失败了，重跑一次也是白搭
            raise RuntimeError(error)

        if not self._should_escalate(
            text, meta, enable_thinking=enable_thinking, final_type=final_type
        ):
            return text, provider, model, final_type

        logger.info(
            "首选档答案不合格（%d 字符，finish_reason=%s），升级到思考模式 + %d token 重跑一次",
            len(text.strip()),
            meta.get("finish_reason"),
            config.SOLVER_ESCALATE_MAX_TOKENS,
        )
        self._status("solving", "第一版答案不合格，正在开启思考模式重新求解…")

        escalated_path = temp_path.with_name(f"{temp_path.stem}_escalated{temp_path.suffix}")
        try:
            esc_type, esc_provider, esc_model, esc_stream = self._start_solve(
                problem_type,
                transcribed_text,
                image_paths,
                True,
                style,
                ocr_fallback,
                max_tokens=config.SOLVER_ESCALATE_MAX_TOKENS,
            )
            esc_text, esc_meta, esc_error = self._run_solve_attempt(
                path=escalated_path,
                stream=esc_stream,
                provider=esc_provider,
                model=esc_model,
                final_type=esc_type,
                transcribed_text=transcribed_text,
                image_paths=image_paths,
                timings=timings,
                ocr_fallback=ocr_fallback,
                cancel=cancel,
            )
        except CancelledError:
            escalated_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            logger.warning("升级求解失败，沿用首选档答案: %s", exc)
            escalated_path.unlink(missing_ok=True)
            return text, provider, model, final_type

        if esc_text.strip() and "--- ERROR ---" not in esc_text:
            logger.info("升级档产出 %d 字符，采用升级结果", len(esc_text.strip()))
            escalated_path.replace(temp_path)
            return esc_text, esc_provider, esc_model, esc_type

        logger.info(
            "升级档同样没有正文（finish_reason=%s，error=%s），沿用首选档答案",
            esc_meta.get("finish_reason"),
            esc_error,
        )
        escalated_path.unlink(missing_ok=True)
        return text, provider, model, final_type

    def _start_solve(
        self,
        problem_type: str,
        transcribed_text: str,
        image_paths: list[Path],
        enable_thinking: bool,
        style: str | None,
        ocr_fallback: bool,
        max_tokens: int | None = None,
    ):
        """选择求解器并启动流式请求。

        `ocr_fallback=True`（部分页转录失败）时，直接让视觉推理模型读原图，
        比拿残缺文本去求解更可靠。
        """
        if problem_type == "VISUAL_REASONING" or ocr_fallback:
            final_type = "VISUAL_REASONING" if problem_type == "VISUAL_REASONING" else "GENERAL"
            provider = config.VISION_PROVIDER_NAME
            model = config.VISION_REASONING_MODEL
            if ocr_fallback:
                logger.warning("部分页面转录失败，回退为原图直读求解")
            stream = vision_client.solve_visual_reasoning_problem(image_paths)
            if stream is None:
                raise RuntimeError("视觉推理模型调用失败")
            return final_type, provider, model, self._as_dict_events(stream)

        from .pipeline import build_prompt, determine_solver, map_final_type

        final_type = map_final_type(problem_type, transcribed_text)
        provider, model = determine_solver(final_type)
        prompt = build_prompt(final_type, transcribed_text, style=style)
        stream = solver_client.stream_solve(
            prompt,
            provider,
            model,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens,
        )
        return final_type, provider, model, stream

    @staticmethod
    def _as_dict_events(stream):
        """把纯文本流包装成 {type, content} 事件流，供上层统一处理。"""
        for chunk in stream:
            if isinstance(chunk, dict):
                yield chunk
            else:
                yield {"type": "content", "content": chunk}

    def _write_header(
        self,
        handle,
        image_paths: list[Path],
        problem_type: str,
        transcribed_text: str,
        provider: str,
        model: str,
        timings: StageTimings,
        ocr_fallback: bool,
    ) -> None:
        """写入 YAML frontmatter + 结构化标题。"""
        handle.write("---\n")
        handle.write(f"problem_type: {problem_type}\n")
        handle.write(f"solver: {provider} ({model})\n")
        handle.write(f"aux_model: {config.AUX_PROVIDER} ({config.AUX_MODEL_NAME})\n")
        if problem_type in ("LEETCODE", "ACM", "ML_CODING"):
            handle.write(f"style: {config.SOLUTION_STYLE}\n")
        if ocr_fallback:
            handle.write("ocr_fallback: true\n")
        handle.write(f"created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        handle.write("images:\n")
        for path in image_paths:
            handle.write(f"  - {path.name}\n")
        handle.write("---\n\n")

        if transcribed_text and transcribed_text != "N/A":
            handle.write("# 题目文本\n\n")
            handle.write(transcribed_text.strip() + "\n\n")
            handle.write("---\n\n")

        handle.write("# 解答\n\n")
        handle.flush()

    def _generate_filename(self, text: str, problem_type: str, task_id: str) -> Path:
        """用辅助模型生成信息丰富的文件名，失败则回退到题号 + 题型。"""
        prompt = prompts.FILENAME_GENERATION_PROMPT.replace("{transcribed_text}", text[:4000])
        body = solver_client.ask_for_analysis(
            prompt, provider=config.AUX_PROVIDER, model=config.AUX_MODEL_NAME
        )
        if not body or not body.strip():
            numbers = extract_question_numbers(text)
            prefix = format_number_prefix(numbers)
            fallback = f"{problem_type}_Solution"
            body = f"{prefix}_{fallback}" if prefix else fallback
        safe = sanitize_filename(body.strip().splitlines()[0])[:80] or f"{problem_type}_Solution"
        candidate = self.solution_dir / f"{safe}.md"
        # 避免同名覆盖：加序号
        counter = 2
        while candidate.exists():
            candidate = self.solution_dir / f"{safe}_{counter}.md"
            counter += 1
        return candidate

    def _preserve_partial(self, temp_path: Path, task_id: str) -> Path | None:
        """取消时保留已写入的内容，改名为 .partial.md。"""
        if not temp_path.exists():
            return None
        target = self.solution_dir / f"{task_id}.partial.md"
        try:
            temp_path.replace(target)
            return target
        except OSError as exc:
            logger.warning("保留部分内容失败: %s", exc)
            return temp_path if temp_path.exists() else None

    def _write_failure_log(self, image_paths: list[Path], reason: str, transcribed_text: str = "N/A") -> Path | None:
        if not image_paths:
            return None
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.solution_dir / f"{timestamp}_{image_paths[0].stem}_FAILED.md"
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("---\n")
                handle.write("status: FAILED\n")
                handle.write(f"created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                handle.write("images:\n")
                for image in image_paths:
                    handle.write(f"  - {image.name}\n")
                handle.write("---\n\n")
                handle.write("# 处理失败\n\n")
                handle.write(f"> **原因**: {reason}\n\n")
                if transcribed_text and transcribed_text != "N/A":
                    handle.write("## 已提取的文本\n\n")
                    handle.write(transcribed_text)
            logger.info("失败日志已保存: %s", path)
            return path
        except OSError as exc:
            logger.error("写入失败日志出错: %s", exc)
            return None

    def _archive_images(self, image_paths: list[Path]) -> None:
        """把已处理的截图移出监控目录，避免重复触发。"""
        if self.archive_dir is None:
            return
        assert self.archive_dir is not None  # for type checkers
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("无法创建归档目录 %s: %s", self.archive_dir, exc)
            return
        for image in image_paths:
            if not image.exists():
                continue
            target = self.archive_dir / f"{image.stem}_{stamp}{image.suffix}"
            try:
                shutil.move(str(image), str(target))
            except OSError as exc:
                logger.warning("归档图片失败 %s: %s", image.name, exc)


class StageCache:
    """阶段缓存的抽象接口（Web 端由 TaskManager 实现，CLI 不启用）。"""

    def get(self, task_id: str, stage: str) -> dict | None:  # pragma: no cover - 接口
        raise NotImplementedError

    def set(self, task_id: str, stage: str, payload: dict) -> None:  # pragma: no cover - 接口
        raise NotImplementedError


def clean_transcribed_text(text: str) -> str:
    """去掉模型可能加上的"这是合并后的文本："之类前缀。"""
    return re.sub(r"^\s*(?:这是|以下是)[^\n]{0,30}[:：]\s*", "", text or "").strip()


def prepare_images(image_paths: list[Path]) -> list[image_prep.PreparedImage]:
    """便捷入口：预处理图片（供自检脚本与测试使用）。"""
    return image_prep.prepare_images(image_paths)
