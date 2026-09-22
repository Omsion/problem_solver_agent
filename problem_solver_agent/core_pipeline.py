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

import json
import logging
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config, image_prep, prompts, solver_client, text_layout, vision_client
from .answer_card import extract_answer_card
from .cancel import CancelToken, CancelledError
from .image_order import describe_order, sort_images_by_time
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

# 求解 prompt 要求正文首行给出文件名建议（`prompts.FILENAME_SUGGESTION_INSTRUCTION`），
# 由 `_run_solve_attempt` 剥掉。容忍全角冒号、`文件名：` 变体与 `**` / 反引号装饰，
# 因为模型偶尔会给它加粗——那只是外观问题，不该让这行漏进解答正文。
_FILENAME_LINE_RE = re.compile(
    r"^[ \t]*(?:[*`]{0,2})(?:FILE|文件名)\s*[:：]\s*(?P<name>.+?)[ \t*`]*$",
    re.IGNORECASE,
)

# 首行判定上限：文件名建议必然很短（prompts 里要求 8–10 个字），因此首行累积超过
# 这么多字符还没等到换行时，就不必再等——它是正文，不是文件名建议。
_FILENAME_PROBE_MAX = 120


def _parse_formula_array(raw: str | None, *, expected: int) -> list[str] | None:
    """解析公式规范化调用的返回值，拿不到等长数组就返回 None（调用方保留原公式）。

    宽松解析：模型可能加 ```json 围栏、可能在数组前后写一句客套话 —— 这里只取
    第一段 `[...]`。**数量必须严格等于 expected**：数量不匹配意味着"哪条对应哪条"
    已经不可知，硬按顺序填回去会把公式错位到别的题目上，比不规范化危险得多。
    """
    if not raw:
        return None
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list) or len(parsed) != expected:
        return None
    if not all(isinstance(item, str) for item in parsed):
        return None
    return parsed


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
    # 本阶段**实际上传的图片张数**。与 `calls` 是两个独立的量，必须分开报：
    # 逐页 OCR 的 8 次调用各带 1 张图（calls=8, images=8），而分批合并的 2 次
    # 调用合起来带 8 张（calls=1, images=8）—— 只报 calls 时计费侧无法区分
    # "同一批图被传了多次"和"每次传一张不同的图"（见 webapp/usage.py）。
    # `None` = 未上报，计费侧按旧口径（calls × pages）兜底，语义见 UsageRecorder。
    images: int | None = None

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "model": self.model,
            "provider": self.provider,
            "pages": self.pages,
            "output_chars": self.output_chars,
            "calls": self.calls,
            "images": self.images,
        }


@dataclass
class StageTimings:
    """各阶段耗时（毫秒），用于回答"到底慢在哪"。"""

    classify: int = 0
    ocr: int = 0
    polish: int = 0
    # 文件名生成耗时。此前这次辅助调用既不计时也不计费（T4），用户既看不到它的
    # 耗时也看不到它的费用；现在它默认走本地生成，只有真调了模型才会有非零值。
    filename: int = 0
    solve: int = 0
    total: int = 0
    cached: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "classify": self.classify,
            "ocr": self.ocr,
            "polish": self.polish,
            "filename": self.filename,
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
        # 当次任务的上下文。这三项只服务于"当前这一次 run()/resolve()"：
        #   _task_id             写进解答文件 frontmatter，使「解答文件 ↔ tasks 表 ↔ uploads 目录」可互相对照
        #   _ocr_archive         第 1 层 OCR 归档的落盘路径
        #   _filename_suggestion 求解正文首行的 FILE: 建议
        # 放在实例上而不是层层传参，是因为 _solve_with_escalation → _run_solve_attempt
        # → _write_header 这几层的签名已冻结，而它们本来就只服务于当次任务。
        # 入口（run/resolve）每次都会重置，实例被复用时不会串上一次的值。
        self._task_id = ""
        self._ocr_archive: Path | None = None
        self._filename_suggestion: str | None = None

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
        # 落盘顺序 ≠ 拍摄顺序（Syncthing 按数据块同步，顺序是乱的），
        # 而顺序决定"哪张是第一页"——错了整道题就废了，所以先按拍摄时间重排
        ordered_paths = sort_images_by_time(image_paths)
        if ordered_paths != image_paths:
            logger.info("图片已按拍摄时间重排: %s", describe_order(image_paths))
        image_paths = ordered_paths
        timings = StageTimings()
        # 当次任务的上下文（说明见 __init__）：每次入口都必须重置
        self._task_id = task_id
        self._ocr_archive = None
        self._filename_suggestion = None
        started = time.time()
        temp_path = self.solution_dir / f"{task_id}_inprogress.md"
        final_path: Path | None = None
        transcribed_text = "N/A"
        final_type = "GENERAL"
        provider = ""
        model = ""
        # 下面三者在 try 之前先给默认值：取消分支也要如实回传"走到哪一步了"。
        # `vision_mode` 用空串表示"视觉阶段还没跑到" —— 早先这里预置 "parallel"，
        # 于是"第一步就被取消"的任务也会被记成走了并行转录路径，把用于统计两条路径
        # 耗时的那一列污染成假数据（取消的任务本就没有转录路径可言）。
        pages: list[str] = []
        continuations: list[bool] = []
        vision_mode = ""
        ended = True

        try:
            self._status("classifying", f"正在分析 {len(image_paths)} 张图片…")

            # ---- 步骤 1+2：分类与转录（合并/并行两条路径统一走一次缓存）----
            cancel.raise_if_cancelled()
            transcribe = self._transcribe(task_id, image_paths, timings)
            problem_type = transcribe["problem_type"]
            pages = transcribe["pages"]
            failed_pages = transcribe["failed_pages"]
            continuations = transcribe["continuations"]
            vision_mode = transcribe["vision_mode"]
            # 页面之间有没有可信的 NEW/CONT 接缝判断（决定能否本地内联、跳过润色）
            seams = bool(transcribe.get("seams", False))
            ended = bool(transcribe.get("ended", True))

            # ---- 步骤 2.5：OCR 归档（第 1 层落盘）----
            # 必须在视觉阶段结束后**立刻**写，而不是成功收尾时才写：这样取消与求解
            # 失败也保住了原始转录 —— 它是唯一无法从解答文件里反推出来的东西。
            self._ocr_archive = self._write_ocr_archive(task_id, image_paths, transcribe)

            cancel.raise_if_cancelled()
            logger.info("题型=%s，转录成功 %d/%d 页", problem_type, len(image_paths) - len(failed_pages), len(image_paths))

            # ---- 步骤 3：文本合并 / 润色（可跳过）----
            ocr_fallback = problem_type != "VISUAL_REASONING" and bool(failed_pages)
            if problem_type == "VISUAL_REASONING":
                transcribed_text = "N/A"
            else:
                transcribed_text, polish_ms = self._textualize(
                    task_id, pages, failed_pages, cancel, timings,
                    vision_mode=vision_mode, continuations=continuations,
                    seams=seams,
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
                        # 纯文本调用：不上传任何图片（不写 0 会被计费侧按旧口径兜底成
                        # calls × pages，凭空给这次润色加上 8 张图的输入 token）
                        images=0,
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
                images=0,   # 文本求解，不带图
            ))

            # ---- 步骤 5：答案卡 + 命名 + 归档 ----
            cancel.raise_if_cancelled()
            self._status("archiving", "正在整理解答文件…")
            card = extract_answer_card(answer_text)
            timings.total = int((time.time() - started) * 1000)

            final_path = self._generate_filename(
                transcribed_text, final_type, task_id, timings=timings
            )
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
                **self._text_views(transcribed_text, pages, continuations),
                "vision_mode": vision_mode,
                # 协议级截断信号，透出给 webapp/CLI 做告警（缺失即为可能被截断）
                "ended": ended,
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
                # 取消发生在求解阶段时，视觉阶段的成果已经拿到手了，如实回传
                **self._text_views(transcribed_text, pages, continuations),
                "vision_mode": vision_mode,
                "ended": ended,
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
        if image_paths:
            # 原图直读求解同样依赖"第几页"的顺序
            image_paths = sort_images_by_time(image_paths)
        if not transcribed_text or not transcribed_text.strip():
            raise ValueError("缺少题目文本，无法重新求解（请先完成一次完整处理）")

        timings = StageTimings(cached=["classify", "ocr", "polish"])
        # 当次任务的上下文（说明见 __init__）：重解不经过视觉阶段，但归档文件通常
        # 已经由第一次 run() 写好，把它的路径找回来，让两份解答文件都能指回同一份 OCR
        self._task_id = task_id
        self._ocr_archive = self._find_ocr_archive(task_id)
        self._filename_suggestion = None
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
                images=0,   # 文本重解，不带图
            ))

            cancel.raise_if_cancelled()
            card = extract_answer_card(answer_text)
            timings.total = int((time.time() - started) * 1000)

            final_path = self._generate_filename(
                f"{transcribed_text}\n\n[重解:{style or config.SOLUTION_STYLE}/{final_type}]",
                final_type,
                task_id,
                timings=timings,
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
                # 三个转录字段尽量带回：重解没有视觉阶段，`ocr_raw_text` 拿不到逐页
                # 原始文本，只能是调用方传进来的那段（通常已是润色后的题目文本）——
                # 有值比没有好（webapp 的 resolve 分支并不覆写这三个字段）。
                "problem_text": transcribed_text,
                "ocr_raw_text": transcribed_text,
                "vision_mode": "",
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
            return {
                "status": "cancelled",
                "path": partial,
                "text": "",
                "problem_type": problem_type,
                "problem_text": transcribed_text,
                "ocr_raw_text": transcribed_text,
                "vision_mode": "",
            }

        except Exception as exc:
            logger.error("重新求解失败 task=%s: %s", task_id, exc, exc_info=True)
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            self._emit({"type": EVENT_ERROR, "task_id": task_id, "message": str(exc)})
            raise

    # ------------------------------------------------------------------
    # 各步骤实现
    # ------------------------------------------------------------------

    # 阶段缓存 payload 的包装：`{"_meta": <指纹>, "value": <原值>}`。
    # 缓存键是 (task_id, stage)，**不含任何参数** —— 只比对模型名时，"切换 provider"
    # 能失效（模型名不同），但**改参数**不能：把 `VISION_MAX_TOKENS` 从 8192 提到
    # 32768 之后点重试，仍会命中那份**被截断的**转录，等于计划书 §5 的补救措施失效。
    # 因此 `_meta` 里存一整份指纹（模型 / provider / 输出上限 / 协议版本），
    # 读取时逐项比对，任一项不同即视为未命中。
    _CACHE_META = "_meta"
    _CACHE_VALUE = "value"
    # 协议版本：PAGE 协议形态或提示词结构一变就 +1，旧条目自动作废
    _CACHE_PROTOCOL = "page-v2"

    def _cache_fingerprint(self, model_name: str) -> dict[str, object]:
        """当前配置的缓存指纹（写入 `_meta`，读取时逐项比对）。"""
        return {
            "model": model_name,
            "provider": config.VISION_PROVIDER_NAME,
            "max_tokens": config._vision_max_tokens(config.VISION_PROVIDER_NAME),
            "protocol": self._CACHE_PROTOCOL,
        }

    def _cached_or_compute(
        self,
        task_id: str,
        stage: str,
        producer,
        timings: StageTimings,
        *,
        model: str | None = None,
    ):
        """优先读阶段缓存（重试时复用，避免重复付费）。

        Args:
            model: 写进缓存、并在读取时比对的模型名；默认当前视觉模型。
        """
        fingerprint = self._cache_fingerprint(model or config.VISION_CLASSIFY_MODEL)
        if self.stage_cache is not None:
            cached = self.stage_cache.get(task_id, stage)
            value = self._read_cache_payload(cached, fingerprint)
            if value is not None:
                logger.info("命中阶段缓存: %s（模型 %s）", stage, fingerprint["model"])
                timings.cached.append(stage)
                return value
        value = producer()
        if value and self.stage_cache is not None:
            self.stage_cache.set(
                task_id,
                stage,
                {self._CACHE_META: dict(fingerprint), self._CACHE_VALUE: value},
            )
        return value

    @classmethod
    def _read_cache_payload(cls, cached, fingerprint: dict):
        """解出缓存里的原值；结构不符或**指纹不一致**一律视为未命中（返回 None）。

        "未命中"比"读到形状不明/参数过期的字典"安全：旧格式的裸 payload 会让下游按
        错误的字段读取，而**旧参数写下的转录**（例如被 8192 截断的那份）更是会让人
        误以为"已经修好了"，这类错误在缓存命中路径上极难发现。
        """
        if not isinstance(cached, dict):
            return None
        meta = cached.get(cls._CACHE_META)
        value = cached.get(cls._CACHE_VALUE)
        if not isinstance(meta, dict) or not isinstance(value, dict):
            logger.info("阶段缓存结构不符（旧格式），按未命中处理")
            return None
        for key, expected in fingerprint.items():
            actual = meta.get(key)
            if actual != expected:
                logger.info(
                    "阶段缓存指纹不一致（%s: %r != %r），缓存失效",
                    key, actual, expected,
                )
                return None
        return value

    def _transcribe(
        self,
        task_id: str,
        image_paths: list[Path],
        timings: StageTimings,
    ) -> dict:
        """分类 + 转录：合并路径与并行路径**统一包进一次** `_cached_or_compute`。

        旧实现只把合并调用包进缓存，而 `USE_COMBINED_VISION_CALL=auto` 且上限为 1 时
        多图根本不发合并请求（直接返回 None），于是缓存几乎永远写不进东西，而实际走的
        并行路径 100% 不缓存 —— 一次 8 图任务在求解阶段失败后点"重试"，会重新付 9 次
        视觉调用的钱。这是整份迁移里投入产出比最高的一处修复。

        Returns:
            {"problem_type", "pages", "failed_pages", "continuations", "vision_mode"}
        """
        started = time.time()
        calls = {"combined": 0, "parallel": 0, "refilled": 0}

        def produce() -> dict:
            combined = vision_client.classify_and_transcribe(
                image_paths, provider=config.VISION_PROVIDER_NAME
            )
            if combined:
                # 分批合并时"1 次合并调用"变成 ceil(N/VISION_BATCH_SIZE) 次真实请求；
                # 但**计费放大倍数仍是 1**：每张图只上传一次，输入 token 已按页数折算，
                # 乘批数会把视觉成本算成 N 倍。请求数只记日志（见下）。
                calls["combined"] = int(combined.get("calls") or 1)
                calls["refilled"] = int(combined.get("refilled") or 0)
                return self._normalize_transcribe(combined)
            # 回退：分类与逐页 OCR 并行，关键路径从相加变成取最大
            calls["parallel"] = 1
            problem_type, pages, failed_pages = vision_client.classify_and_transcribe_parallel(
                image_paths, config.VISION_PROVIDER_NAME
            )
            return {
                "problem_type": problem_type or "GENERAL",
                "pages": [str(p) for p in (pages or [])],
                "failed_pages": sorted(int(i) for i in (failed_pages or [])),
                "continuations": [],
                "vision_mode": "parallel",
                # 并行路径没有协议级截断信号（每页是独立请求），按"完整"处理：
                # 真正的缺页已经进了 failed_pages
                "ended": True,
            }

        result = self._cached_or_compute(task_id, "vision", produce, timings)

        elapsed = int((time.time() - started) * 1000)
        # classify 与 ocr 仍然记同一段墙钟时间：合并路径下一次请求同时完成两件事；
        # 并行路径下两者是并行执行的（关键路径取 max），相加反而是错的
        timings.classify = elapsed
        timings.ocr = elapsed

        # 协议级截断告警：流式下拿不到 finish_reason，缺 `<<<END>>>` 是唯一信号。
        # 缓存命中的条目不会走到这里（当时已经告警过一次），因此不会重复刷屏。
        if result.get("vision_mode") in ("combined", "json") and not result.get("ended", True):
            logger.warning(
                "本次转录缺少 <<<END>>> 标记（vision_mode=%s）：可能被 max_tokens 截断，"
                "已收集 %d/%d 页（缺失页已由 refill_pages 补做）",
                result.get("vision_mode"),
                sum(1 for page in result.get("pages") or [] if str(page).strip()),
                len(result.get("pages") or []),
            )

        if calls["combined"]:
            # 合并调用同时完成分类与全部页面转录。分批合并时这里有多次真实请求，
            # 但 `calls` 保持 1：它是**计费放大倍数**（固定 prompt 开销与输出都只算
            # 一份，因为每张图只上传一次）；请求数只写日志便于排查。
            if calls["combined"] > 1:
                logger.info(
                    "视觉阶段合并调用共 %d 次请求（分批合并，每批 ≤%d 张图，%d 批并发）",
                    calls["combined"], config.VISION_BATCH_SIZE, config.VISION_BATCH_WORKERS,
                )
            self._emit_usage(UsageReport(
                stage="vision",
                model=config.VISION_CLASSIFY_MODEL,
                provider=config.VISION_PROVIDER_NAME,
                pages=len(image_paths),
                calls=1,
                # 每张图只上传一次（分批只是切分请求，不重传）—— 这是合并路径
                # 相对并行回退路径唯一的成本优势，必须如实上报
                images=len(image_paths),
            ))
        elif calls["parallel"]:
            # 回退路径：1 次分类 + 每页 1 次转录。
            # 关键：分类那次带的是**全部** N 张图（DeepSeek 视觉调用必须带图），
            # 逐页 OCR 每次带 1 张 —— 因此 images 是 N + N，而 calls 是 1 + N。
            # 旧口径把"每图 1024 token"按 calls 再乘一遍，8 图会高估输入 token ≈4 倍。
            self._emit_usage(UsageReport(
                stage="classify",
                model=config.VISION_CLASSIFY_MODEL,
                provider=config.VISION_PROVIDER_NAME,
                pages=len(image_paths),
                calls=1,
                images=len(image_paths),
            ))
            self._emit_usage(UsageReport(
                stage="ocr",
                model=config.VISION_CLASSIFY_MODEL,
                provider=config.VISION_PROVIDER_NAME,
                pages=len(image_paths),
                calls=len(image_paths),
                images=len(image_paths),
            ))
        # 既没走合并也没走并行 = 命中缓存，一次调用都没发生，自然不记账。

        # refill_pages 的单页补做是真金白银的调用（补 2 页 = 2 次请求），必须如实反映。
        # 单独发一条 stage，而不是把次数加到上面的 vision 里：补做发生在合并路径之后，
        # 并进去会把"固定开销"按补做次数再放大一遍，且无法区分补做与首次转录的成本。
        if calls["refilled"]:
            self._emit_usage(UsageReport(
                stage="vision_refill",
                model=config.VISION_CLASSIFY_MODEL,
                provider=config.VISION_PROVIDER_NAME,
                pages=calls["refilled"],
                calls=1,
                images=calls["refilled"],   # 每次补做只重传那 1 页
            ))

        return result

    @staticmethod
    def _normalize_transcribe(raw: dict) -> dict:
        """把合并调用的结果规整成 `_transcribe` 的统一形状。

        `vision_mode` 缺失时按 `"parallel"` 处理（**保守**：保留润色、不做内联去重）——
        太旧的缓存 payload、或将来新增的第三种协议都可能不带这个字段，而误把并行结果
        当成合并结果去内联，代价是丢掉一次跨页去重。
        """
        mode = str(raw.get("vision_mode") or "parallel")
        if mode not in ("combined", "batched", "json"):
            mode = "parallel"
        # 并行路径没有 NEW/CONT 信息，硬塞一个等长列表只会让下游误以为有接缝判断
        continuations = (
            [bool(c) for c in (raw.get("continuations") or [])] if mode != "parallel" else []
        )
        # `seams`：页面之间是否存在可信的 NEW/CONT 接缝判断（决定能否本地内联拼接）。
        # 缺省按 mode 推断，兼容较早的缓存 payload（组 H2 之前只有 combined/json）。
        seams = bool(raw.get("seams", mode == "combined"))
        return {
            "problem_type": raw.get("problem_type") or "GENERAL",
            "pages": [str(p) for p in (raw.get("pages") or [])],
            "failed_pages": sorted(int(i) for i in (raw.get("failed_pages") or [])),
            "continuations": continuations,
            "seams": seams,
            "vision_mode": mode,
            # 协议级截断信号（响应里有没有 `<<<END>>>`）。并行路径没有这个信号，
            # 按 True 处理 —— 缺失页已经由 failed_pages 如实表达，不能在这里编一个截断。
            "ended": bool(raw.get("ended", True)) if mode != "parallel" else True,
        }

    @staticmethod
    def _text_views(transcribed_text: str, pages: list[str], continuations: list[bool]) -> dict:
        """构造落库用的两个文本视图（组 I，供 webapp 写进 tasks 表）。

        - `problem_text`：真正送进求解器的文本（润色后 / 内联拼接后）。视觉推理题
          没有文本输入（`transcribed_text == "N/A"`），写空串而不是 "N/A"，
          否则历史搜索里全是这种噪声。
        - `ocr_raw_text`：逐页原始 OCR 拼接，**没有被润色改写** —— 润色会重写整篇文本，
          用户搜原图里的关键词时可能一个字都对不上。这里用**显式页边界**
          （`---[NEXT]---`）而不是 `join_by_continuation` 的空行：没有可信接缝判断时，
          空行会让"两道互不相关的题"在文本里连成一体，而页边界是读题的必要信息。
        """
        return {
            "problem_text": "" if transcribed_text == "N/A" else transcribed_text,
            "ocr_raw_text": text_layout.join_with_separator(pages) if pages else "",
        }

    def _write_ocr_archive(
        self,
        task_id: str,
        image_paths: list[Path],
        transcribe_result: dict,
    ) -> Path | None:
        """把逐页原始 OCR 落盘到 `<OCR_DIR>/<YYYY-MM-DD>/<task_id>.md`（组 I，第 1 层）。

        用 `task_id` 而不是最终文件名做 stem：转录在第 1 阶段就产生了，而文件名要等
        求解器给出 —— 这样中途取消/求解失败也留下了归档，且与 `uploads/<task_id>/`
        天然可配对。分页数**必须等于图片数**（S11），因此按 `image_paths` 遍历，
        缺页如实标成"识别失败"而不是少写一节。

        归档只是副产品：任何失败都只记日志，绝不能让解题失败。
        """
        pages = list(transcribe_result.get("pages") or [])
        failed = {int(i) for i in (transcribe_result.get("failed_pages") or [])}
        continuations = list(transcribe_result.get("continuations") or [])
        mode = str(transcribe_result.get("vision_mode") or "parallel")
        try:
            archive_dir = Path(config.OCR_DIR) / time.strftime("%Y-%m-%d")
            archive_dir.mkdir(parents=True, exist_ok=True)
            path = archive_dir / f"{sanitize_filename(task_id) or 'task'}.md"
            names = ", ".join(image.name for image in image_paths)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("---\n")
                handle.write(f"task_id: {task_id}\n")
                handle.write(f"created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                handle.write(f"vision_provider: {config.VISION_PROVIDER_NAME}\n")
                handle.write(f"vision_model: {config.VISION_CLASSIFY_MODEL}\n")
                handle.write(f"vision_mode: {mode}\n")
                handle.write(f"images: [{names}]\n")
                handle.write(f"pages: {len(image_paths)}\n")
                handle.write(f"failed_pages: [{', '.join(str(i) for i in sorted(failed))}]\n")
                handle.write("---\n\n")
                for index, image in enumerate(image_paths):
                    body = pages[index].strip() if index < len(pages) and pages[index] else ""
                    if not body:
                        label = "识别失败"
                    elif index < len(continuations) and continuations[index]:
                        label = "CONT"
                    else:
                        label = "NEW"
                    handle.write(f"## 第 {index + 1} 页 — {image.name} — {label}\n\n")
                    if body:
                        handle.write(body + "\n\n")
                    else:
                        handle.write(
                            "> 该页 OCR 返回空内容；已由 refill_pages 补做，"
                            "或求解阶段回退为原图直读。\n\n"
                        )
            logger.info("OCR 归档已保存: %s", path)
            return path
        except Exception as exc:  # 归档不能拖垮解题：写盘/编码异常都只记日志
            logger.warning("写入 OCR 归档失败（不影响解题）: %s", exc)
            return None

    @staticmethod
    def _find_ocr_archive(task_id: str) -> Path | None:
        """找回某个任务已落盘的 OCR 归档（换路重解时写进 frontmatter 做交叉引用）。"""
        try:
            matches = sorted(Path(config.OCR_DIR).glob(f"*/{sanitize_filename(task_id)}.md"))
        except OSError as exc:  # pragma: no cover - 目录不可读时按"没有归档"处理
            logger.warning("查找 OCR 归档失败: %s", exc)
            return None
        return matches[-1] if matches else None

    def _textualize(
        self,
        task_id: str,
        pages: list[str],
        failed_pages: list[int],
        cancel: CancelToken,
        timings: StageTimings,
        *,
        vision_mode: str = "parallel",
        continuations: list[bool] | None = None,
        seams: bool = False,
    ) -> tuple[str, int]:
        """把逐页文本合并成题目文本；必要时才调用润色模型。

        Returns:
            (题目文本, 润色耗时毫秒)
        """
        if len(pages) == 1:
            # 单图无需"合并去重"，但**仍需排版与公式规范化**：
            # 单页同样带 App 页眉，公式同样可能没被 `$...$` 包住。
            text, removed = text_layout.normalize_pages(pages)
            if removed:
                logger.info("单图任务：剥离 %d 行页眉/导航噪音", len(removed))
            text, formula_ms = self._normalize_formulas(text, cancel)
            return text or pages[0].strip(), formula_ms

        # 合并路径内联：`<<<PAGE n|CONT>>>` 的接缝判断来自视觉模型自己，本地拼一下
        # 就够了，再让第二个模型"找最长重叠"重写整篇文本不但白花钱，还会**静默删掉
        # 一道题的开头**（多道独立题时相邻页必然有"下列哪项正确"这类相同措辞）。
        # 缺页（部分页转录失败）时不敢内联：那几页内容本来就不完整，仍交给润色。
        #
        # **只在有可信接缝判断时内联**（`seams=True`，即每批都走 PAGE 协议）：
        # JSON 回退协议没有 NEW/CONT 标记（continuations 恒为 False），拿它内联等于把
        # "每页完整重述一次"的文本用空行直接拼起来 —— 既不去重、也没有 `---[NEXT]---`
        # 边界，重复内容会整段进解答文件。那种情况正好是润色 prompt 的"场景判断"要处理的
        # 场景，必须保留润色。分批合并时批内接缝可信（批首页按 NEW），同样可以内联。
        if (
            seams
            and not failed_pages
            and config.VISION_INLINE_MERGE
        ):
            # A：本地排版规范化（剥页眉、合并跨页代码围栏、规整空行）—— 零 token 成本。
            # 这是"合并路径不调润色"当初欠下的一环：润色 prompt 里的公式规范化与
            # 排版处理随之一起消失了，而人是要看着这份文本读题的。
            inlined, removed = text_layout.normalize_pages(pages, continuations or [])
            if inlined.strip():
                if removed:
                    logger.info(
                        "合并路径（%s）剥离 %d 行页眉/导航噪音", vision_mode, len(removed)
                    )
                # C：只把公式送去规范化（输出量几十 token，而非重写整篇 10K）。
                # 正文一个字都不经过模型 —— 不存在被删改的风险。
                inlined, formula_ms = self._normalize_formulas(inlined, cancel)
                logger.info("合并路径（%s）本地排版完成，跳过润色步骤", vision_mode)
                return inlined.strip(), formula_ms
            logger.warning("合并路径内联拼接结果为空，回退到润色流程")

        joined = text_layout.join_with_separator([page.strip() for page in pages])
        if len(joined) < config.MERGE_SKIP_THRESHOLD:
            logger.info("合并文本较短（%d 字符），跳过润色步骤", len(joined))
            # 润色被跳过（文本太短），但**公式规范化不该跟着省掉**。
            # 注意这里必须传 `joined`（已带 `---[NEXT]---` 页边界），不能用
            # `normalize_pages` 的结果替换它 —— 那是"同一题连续页"的语义，会把
            # 两道互不相关的题连成一体（有回归用例锁住）。
            text, formula_ms = self._normalize_formulas(joined, cancel)
            return text, formula_ms

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
        # 润色 prompt 本身就要求做公式规范化，这里不再叠加一次（避免两次改写）
        return polished, elapsed

    def _normalize_formulas(self, text: str, cancel: CancelToken) -> tuple[str, int]:
        """C：把文本里的公式片段送去模型规范化，正文不动。

        为什么只送公式：润色模型重写整篇要输出约 10K token（整条链最贵的一次），
        而它的"找最长重叠"指令在多道独立题场景会**静默删掉一道题的开头**。
        只送公式则输出量降到几十 token，且**正文一个字符都不经过模型**。

        失败与异常一律回退原文（`text` 原样返回）——排版降级可以接受，
        丢内容不可以。

        Returns:
            (规范化后的文本, 耗时毫秒)。未启用 / 没有公式 / 失败时耗时均为 0。
        """
        if not config.FORMULA_NORMALIZE or not text:
            return text, 0

        skeleton, formulas = text_layout.extract_math_spans(text)
        if not formulas:
            return text, 0
        # 片段太碎的（比如只有 `$x$`）不值得一次调用：规范化收益 < 一次往返成本
        if sum(len(item) for item in formulas) < config.FORMULA_MIN_CHARS:
            logger.info(
                "公式片段过少（%d 个 / %d 字符），跳过规范化",
                len(formulas), sum(len(item) for item in formulas),
            )
            return text, 0

        cancel.raise_if_cancelled()
        started = time.time()
        prompt = prompts.FORMULA_NORMALIZE_PROMPT.replace(
            "{formulas_json}", json.dumps(formulas, ensure_ascii=False)
        )
        try:
            raw = solver_client.ask_for_analysis(
                prompt, provider=config.AUX_PROVIDER, model=config.AUX_MODEL_NAME
            )
        except Exception as exc:  # 规范化失败绝不能让解题失败
            logger.warning("公式规范化调用异常，保留原公式: %s", exc)
            return text, int((time.time() - started) * 1000)

        normalized = _parse_formula_array(raw, expected=len(formulas))
        elapsed = int((time.time() - started) * 1000)
        if normalized is None:
            logger.warning(
                "公式规范化返回不可用（数量不匹配或非 JSON），保留原公式：%r",
                (raw or "")[:120],
            )
            return text, elapsed

        # 只接受"确实变了"的结果；逐条为空则视为模型偷懒，保留原样
        final = [new.strip() or old for old, new in zip(formulas, normalized)]
        changed = sum(1 for old, new in zip(formulas, final) if old != new)
        logger.info(
            "公式规范化完成：%d 个片段，其中 %d 个被修正（%d ms）",
            len(formulas), changed, elapsed,
        )
        self._emit_usage(UsageReport(
            stage="formula",
            model=config.AUX_MODEL_NAME,
            provider=config.AUX_PROVIDER,
            output_chars=len(raw or ""),
            calls=1,
            images=0,
        ))
        return text_layout.rebuild_math(skeleton, final), elapsed

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
        # 首行判定状态机（T4）：
        #   pending —— 还没判定归属的首行缓冲。判定之前一个字符都不写出去，
        #              否则 `FILE:` 行已经落进解答文件和 chunks 了，剥不掉。
        #   skipped —— 被跳过的前导空行。判定结果若是"不是文件名行"，原样补回，保证不丢内容。
        pending = ""
        skipped = ""
        decided = False

        def _flush(content: str) -> None:
            """把确定属于正文的文本写进文件/事件流。"""
            if not content:
                return
            chunks.append(content)
            handle.write(content)
            handle.flush()
            self._emit({"type": EVENT_CHUNK, "content": content})

        def _feed(content: str, *, eof: bool) -> None:
            """把分片喂进首行判定状态机。

            `FILE:` 行必须**跨分片**判断（一个分片可能只到 `FI`），因此要等到两个
            信号之一才决定：拿到换行，或缓冲超过 `_FILENAME_PROBE_MAX`（首行太长，
            不可能是文件名建议）。流结束时（eof）也必须决定，否则末尾内容会烂在缓冲里。
            """
            nonlocal pending, skipped, decided
            if decided:
                _flush(content)
                return
            pending += content
            while True:
                newline = pending.find("\n")
                if newline < 0:
                    if eof:
                        matched = _FILENAME_LINE_RE.match(pending)
                        if matched:
                            self._remember_filename_suggestion(matched.group("name"))
                        else:
                            _flush(skipped + pending)
                        pending = skipped = ""
                        decided = True
                    elif len(pending) > _FILENAME_PROBE_MAX:
                        _flush(skipped + pending)
                        pending = skipped = ""
                        decided = True
                    return
                first, rest = pending[:newline], pending[newline + 1:]
                if not first.strip():
                    # 模型习惯在正文前多打一个换行：先跳过，判定不是 FILE 行时补回
                    skipped += first + "\n"
                    pending = rest
                    continue
                matched = _FILENAME_LINE_RE.match(first)
                if matched:
                    # 这一行只用于命名：不写进解答文件、不进 chunks、
                    # 也不影响答案卡抽取与 _should_escalate（它们只看 chunks）
                    self._remember_filename_suggestion(matched.group("name"))
                    pending = skipped = ""
                    _flush(rest)
                else:
                    _flush(skipped + first + "\n" + rest)
                    pending = skipped = ""
                decided = True
                return

        with open(path, "w", encoding="utf-8") as handle:
            self._write_header(
                handle, image_paths, final_type, transcribed_text,
                provider, model, timings, ocr_fallback,
            )
            for event in stream:
                # 取消在每个分片之间生效：用户点取消后最多再等一个分片
                if cancel.cancelled:
                    # 此刻不能再等后续分片来判定首行，按"不是文件名行"写出缓冲内容，
                    # 否则用户已经看到的第一段会从部分文件里凭空消失
                    _feed("", eof=True)
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
                    _feed(
                        event.get("content", "") if isinstance(event, dict) else str(event),
                        eof=False,
                    )
            # 流正常结束或提前 break：把缓冲里的正文补写出去（error 分支同样不能丢内容）
            _feed("", eof=True)
        return "".join(chunks), meta, error

    def _remember_filename_suggestion(self, raw: str) -> None:
        """记下求解首行给出的文件名建议（只用于命名，不进正文）。"""
        name = (raw or "").strip().strip("*`").strip()
        if name:
            self._filename_suggestion = name
            logger.info("求解首行给出文件名建议: %s", name)

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
        # task_id 此前完全没有落盘，导致「解答文件 ↔ tasks 表 ↔ uploads 目录」三者
        # 无法互相对照；ocr_archive 指向第 1 层的原始 OCR 归档（组 I）。
        handle.write(f"task_id: {self._task_id or 'unknown'}\n")
        handle.write(f"problem_type: {problem_type}\n")
        handle.write(f"solver: {provider} ({model})\n")
        handle.write(f"aux_model: {config.AUX_PROVIDER} ({config.AUX_MODEL_NAME})\n")
        if problem_type in ("LEETCODE", "ACM", "ML_CODING"):
            handle.write(f"style: {config.SOLUTION_STYLE}\n")
        if ocr_fallback:
            handle.write("ocr_fallback: true\n")
        handle.write(f"created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        # 归档不存在时留空值而不是省略这一行：键稳定，下游按 key 解析不用做兼容
        handle.write(f"ocr_archive: {self._ocr_archive or ''}\n")
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

    def _generate_filename(
        self,
        text: str,
        problem_type: str,
        task_id: str,
        *,
        timings: StageTimings | None = None,
        suggestion: str | None = None,
    ) -> Path:
        """生成解答文件名；优先本地生成，只有解析不出题号时才调模型（T4）。

        三档 `config.FILENAME_MODE`（`suggestion` 是求解正文首行给出的 FILE: 建议）：
          auto  = FILE: 建议 → 本地题号 → 才调模型
          local = FILE: 建议 → 本地题号（**从不**调模型）
          model = 旧行为：每次调模型，模型答不出时才回退本地题号

        此前无论如何都要调一次辅助模型，而且这次调用游离在 StageTimings / UsageReport
        之外（用户既看不到耗时也看不到费用）。而 `extract_question_numbers` 本来就是
        它的 fallback 路径 —— 学术题几乎都有题号，于是这一次调用可以整段省掉。
        """
        mode = config.FILENAME_MODE
        suggestion = (suggestion or self._filename_suggestion or "").strip()
        name = ""

        if mode != "model":
            name = suggestion or self._local_filename(text, problem_type)
        if not name and mode in ("auto", "model"):
            asked = (self._ask_model_for_filename(text, timings) or "").strip()
            name = asked.splitlines()[0] if asked else ""
        if not name:
            name = self._local_filename(text, problem_type) or f"{problem_type}_Solution"

        # 模型偶尔会连解释一起返回：只取第一行，与旧行为保持一致
        first_line = name.strip().splitlines()[0] if name.strip() else ""
        safe = sanitize_filename(first_line)[:80] or f"{problem_type}_Solution"
        candidate = self.solution_dir / f"{safe}.md"
        # 避免同名覆盖：加序号
        counter = 2
        while candidate.exists():
            candidate = self.solution_dir / f"{safe}_{counter}.md"
            counter += 1
        return candidate

    @staticmethod
    def _local_filename(text: str, problem_type: str) -> str:
        """本地按题号生成文件名；解析不到题号时返回空串，由调用方决定下一步。"""
        prefix = format_number_prefix(extract_question_numbers(text))
        return f"{prefix}_{problem_type}_Solution" if prefix else ""

    def _ask_model_for_filename(self, text: str, timings: StageTimings | None) -> str | None:
        """调辅助模型要一个文件名。

        耗时计入 `StageTimings.filename`、用量发一条 `UsageReport(stage="filename")` ——
        这次调用必须可见，否则用户看不到它的费用，也就无法判断"本地生成"省下了多少。
        """
        prompt = prompts.FILENAME_GENERATION_PROMPT.replace("{transcribed_text}", text[:4000])
        started = time.time()
        body = solver_client.ask_for_analysis(
            prompt, provider=config.AUX_PROVIDER, model=config.AUX_MODEL_NAME
        )
        elapsed = int((time.time() - started) * 1000)
        if timings is not None:
            timings.filename += elapsed
        self._emit_usage(UsageReport(
            stage="filename",
            model=config.AUX_MODEL_NAME,
            provider=config.AUX_PROVIDER,
            output_chars=len(body or ""),
            calls=1,
            images=0,   # 纯文本生成文件名
        ))
        return body

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
