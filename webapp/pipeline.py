"""流水线服务 — 把共享 core 流水线适配成 Web 事件流

本文件只负责三件事：
1. 订阅 core 流水线的事件，转发给 SSE 事件总线
2. 把任务状态/耗时/答案卡写进数据库
3. 提供一个阶段缓存实现，让"重试"可以复用已完成的分类与转录

真正的流水线逻辑在 `problem_solver_agent/core_pipeline.py`，与 CLI Agent 共用。
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

from problem_solver_agent import config as core_config
from problem_solver_agent import vision_client
from problem_solver_agent.cancel import CancelToken
from problem_solver_agent.core_pipeline import SolutionPipeline
from problem_solver_agent.utils import sanitize_filename

logger = logging.getLogger("WebappPipeline")

# 需要在解答文件里保留的状态（用于判断"是否已完成"）
TERMINAL_COMPLETED = "completed"
TERMINAL_CANCELLED = "cancelled"

# 视觉阶段在 stage_cache 里的键名（core_pipeline._cached_or_compute 写入的 stage）
VISION_STAGE = "vision"


# ----------------------------------------------------------------------
# 阶段缓存读取（F1）
# ----------------------------------------------------------------------


def read_cached_transcript(task_manager, task_id: str) -> dict | None:
    """读取本任务已经付费过的视觉转录结果（阶段缓存）。

    为什么不能直接 `get_cached_stage(...).get("pages")`：迁移后 core 把缓存写成了
    `{"_meta": {...}, "value": {...}}` 包装，而 stage_cache 的主键是
    `(task_id, stage)`、**不含模型名** —— 切换视觉 provider 后按裸字段读会同时踩两个坑：
    (1) 读不到任何东西（包装层让 `pages`/`problem_type` 恒为 None），
    (2) 若有人"兼容"地按裸字段读，又会在切 provider 后静默复用上一个 provider 的转录。

    判定规则（与 `SolutionPipeline._read_cache_payload` 对齐）：

    - 结构不符（例如迁移前的裸 payload）→ None，绝不按旧字段名猜着读；
    - `_meta.model` 必须等于当前视觉模型；`_meta.provider` 存在时还必须等于当前
      provider —— 但 provider 缺失时容忍（只要求 model 匹配），多出来的键同理：
      本函数**不依赖 `_meta` 的键集合**，Lead 后续加键不会让缓存全部失效。

    Args:
        task_manager: 提供 `get_cached_stage(task_id, stage)` 的任务管理器。
        task_id: 任务 id。

    Returns:
        转录字典 `{problem_type, pages, failed_pages, continuations, vision_mode}`
        （可能被扩展更多键）；不可用或已失效时返回 None。
    """
    cached = task_manager.get_cached_stage(task_id, VISION_STAGE)
    if not isinstance(cached, dict):
        return None
    meta = cached.get(SolutionPipeline._CACHE_META)  # noqa: SLF001 - 缓存包装是跨模块契约
    value = cached.get(SolutionPipeline._CACHE_VALUE)  # noqa: SLF001
    if not isinstance(meta, dict) or not isinstance(value, dict):
        logger.info("阶段缓存结构不符（迁移前的裸 payload？），按未命中处理")
        return None

    cached_model = str(meta.get("model") or "")
    if cached_model != core_config.VISION_CLASSIFY_MODEL:
        logger.info(
            "阶段缓存由视觉模型 %s 写入，当前模型 %s，缓存失效",
            cached_model or "<缺失>",
            core_config.VISION_CLASSIFY_MODEL,
        )
        return None

    cached_provider = str(meta.get("provider") or "")
    if cached_provider and cached_provider != core_config.VISION_PROVIDER_NAME:
        logger.info(
            "阶段缓存由 provider %s 写入，当前 provider %s，缓存失效",
            cached_provider,
            core_config.VISION_PROVIDER_NAME,
        )
        return None
    return value


def transcript_text_views(value: dict) -> tuple[str, str]:
    """从视觉缓存值里算出落库用的两个文本视图（对齐 core 的 `_text_views`）。

    - `problem_text`：真正送进求解器的题面。缓存里通常**没有**润色后的文本
      （润色发生在写缓存之后），此时用 core 同一个 `join_by_continuation()` 本地
      拼接逐页文本 —— 这是唯一"真实可得"的题面，而不是编一个 "N/A" 或写错内容；
      视觉推理题（VISUAL_REASONING）没有文本输入，按 core 的约定写空串。
    - `ocr_raw_text`：逐页原始 OCR 拼接，**没有被润色改写**（用户搜原图关键词靠它）。

    Returns:
        (problem_text, ocr_raw_text)
    """
    pages = [str(p) for p in (value.get("pages") or [])]
    continuations = [bool(c) for c in (value.get("continuations") or [])]
    joined = vision_client.join_by_continuation(pages, continuations) if pages else ""

    ocr_raw_text = str(value.get("ocr_raw_text") or "").strip() or joined.strip()
    problem_text = str(value.get("problem_text") or "").strip()
    if problem_text == "N/A":  # core 约定的"无文本输入"哨兵值，落库要写空串
        problem_text = ""
    if not problem_text and str(value.get("problem_type") or "") != "VISUAL_REASONING":
        problem_text = ocr_raw_text
    return problem_text, ocr_raw_text


def cached_transcript_fields(task_manager, task_id: str) -> dict[str, str]:
    """从视觉阶段缓存恢复可落库的转录字段（F5：求解失败也别丢已付费的 OCR）。

    Returns:
        只包含"真有值"的键（problem_text / ocr_raw_text / vision_mode）；
        缓存不可用或为空时返回 `{}` —— 调用方必须保持数据库列原样，
        **绝不写编造文本**（"N/A" / 空占位都会污染历史搜索）。
    """
    value = read_cached_transcript(task_manager, task_id)
    if not value:
        return {}
    problem_text, ocr_raw_text = transcript_text_views(value)
    fields: dict[str, str] = {}
    if problem_text:
        fields["problem_text"] = problem_text
    if ocr_raw_text:
        fields["ocr_raw_text"] = ocr_raw_text
    vision_mode = str(value.get("vision_mode") or "").strip()
    if vision_mode:
        fields["vision_mode"] = vision_mode
    return fields


# ----------------------------------------------------------------------
# OCR 归档清理（F6）
# ----------------------------------------------------------------------


def find_ocr_archives(task_id: str) -> list[Path]:
    """找出某任务的全部 OCR 归档（`<OCR_DIR>/<YYYY-MM-DD>/<task_id>.md`）。

    按日期目录 glob 而不是拼"今天"：归档写于任务创建那天，删除/清理可能发生在
    之后任意一天，拼"今天"会漏删并让垃圾永久留在磁盘上。
    文件名 stem 用 core 同一套 `sanitize_filename()`（否则 task_id 含非法字符时
    两边会算出不同文件名，归档就成了删不掉的孤儿）。
    """
    root = Path(core_config.OCR_DIR)
    stem = sanitize_filename(task_id or "").strip()
    if not stem:
        return []
    try:
        return sorted(root.glob(f"*/{stem}.md"))
    except OSError as exc:  # pragma: no cover - 目录不可读时按"没有归档"处理
        logger.warning("查找 OCR 归档失败 %s: %s", task_id, exc)
        return []


def _is_inside_ocr_dir(path: Path, root: Path) -> bool:
    """确认 path 确实落在 OCR_DIR 之内。

    删除是不可逆的（2026-09-20 真实上传被误删的教训），所以除了"stem 已 sanitize"
    之外再做一次路径包含性校验；`resolve()` 会跟穿软链接，指到外面的链接会被跳过。
    """
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def delete_ocr_archives(task_ids: Iterable[str]) -> int:
    """删除这些任务的 OCR 归档，返回实际删除的文件数。

    尽力而为：单个文件失败只记日志，不影响其它删除，也不抛出（调用点在删除接口
    与保留策略里，失败不该让接口 500）。
    """
    root = Path(core_config.OCR_DIR)
    removed = 0
    for task_id in task_ids:
        for path in find_ocr_archives(task_id):
            if not _is_inside_ocr_dir(path, root):
                logger.warning("跳过 OCR_DIR 之外的归档路径: %s", path)
                continue
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                logger.warning("删除 OCR 归档失败 %s: %s", path, exc)
    if removed:
        logger.info("清理 OCR 归档 %d 个", removed)
    return removed


class _StageCache:
    """把阶段结果写进 SQLite，供重试时复用（避免重复调用付费 API）。"""

    STAGES = (VISION_STAGE,)

    def __init__(self, task_manager) -> None:
        self.task_manager = task_manager

    def get(self, task_id: str, stage: str):
        """取**原始**缓存 payload（含 `{"_meta": …, "value": …}` 包装）。

        这里刻意不拆包装：调用方是 core 的 `_cached_or_compute`，它自己会
        `_read_cache_payload` 校验模型并解包。webapp 侧要读转录请用
        `read_cached_transcript()`（F1），不要在这里改语义。
        """
        if stage not in self.STAGES:
            return None
        return self.task_manager.get_cached_stage(task_id, stage)

    def set(self, task_id: str, stage: str, payload) -> None:
        if stage not in self.STAGES:
            return
        try:
            self.task_manager.set_cached_stage(task_id, stage, payload)
        except Exception as exc:  # 缓存写入失败不影响主流程
            logger.warning("写入阶段缓存失败: %s", exc)


class PipelineService:
    """Web 端的流水线服务，保留既有 `run()` 签名以免调用点改动。"""

    def __init__(self, solution_dir: Path, task_manager) -> None:
        self.solution_dir = Path(solution_dir)
        self.solution_dir.mkdir(parents=True, exist_ok=True)
        self.task_manager = task_manager
        self.stage_cache = _StageCache(task_manager)

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def run(
        self,
        task_id: str,
        image_paths: list[Path],
        on_progress: Callable[[dict], None],
        enable_thinking: bool | None = None,
        cancel: CancelToken | None = None,
        style: str | None = None,
    ) -> dict:
        """执行流水线，事件通过 on_progress 推送。

        Args:
            cancel: 取消令牌；由 TaskRegistry 在收到取消请求时置位。
            style: 编程题求解风格（OPTIMAL / EXPLORATORY），None 用全局配置。
            enable_thinking: None = 用配置默认值（默认不首选思考，答案不合格时
                由 core_pipeline 自动升级到思考档）。
        """
        self.task_manager.update_task(task_id, status="processing", error_message="")

        def _forward(event: dict) -> None:
            # 事件类型保持与前端契约一致（status/chunk/reasoning/timings/done/error/cancelled）
            if event.get("type") == "done":
                self.task_manager.update_task(
                    task_id,
                    timings_json=json.dumps(event.get("timings") or {}, ensure_ascii=False),
                )
            on_progress(event)

        pipeline = SolutionPipeline(
            solution_dir=self.solution_dir,
            on_event=_forward,
            stage_cache=self.stage_cache,
        )

        try:
            result = pipeline.run(
                task_id,
                image_paths,
                cancel=cancel,
                enable_thinking=enable_thinking,
                style=style,
            )
        except Exception as exc:
            logger.error("流水线异常 task=%s: %s", task_id, exc, exc_info=True)
            self.task_manager.update_task(task_id, status="failed", error_message=str(exc))
            # F5：OCR 在视觉阶段就已付费完成并进了阶段缓存，而异常路径拿不到
            # core 的返回值（result 不存在）——不补写的话这三个字段会永远为空，
            # 用户重试时既看不到转录，历史搜索也搜不到这次已经付过钱的识别结果。
            self._backfill_cached_transcript(task_id)
            raise

        status = result.get("status")
        path: Path | None = result.get("path")
        timings_json = json.dumps(result.get("timings") or {}, ensure_ascii=False)
        # 转录双层落盘的第二层落库：problem_text / ocr_raw_text 供历史搜索，
        # vision_mode 供统计两条转录路径。这三个键是 core 的契约（组 I），
        # 缺失时写空串，不要写 None —— 列有 DEFAULT '' 且前端按字符串用。
        transcript_fields = {
            "problem_text": str(result.get("problem_text") or ""),
            "ocr_raw_text": str(result.get("ocr_raw_text") or ""),
            "vision_mode": str(result.get("vision_mode") or ""),
        }

        if status == TERMINAL_COMPLETED and path is not None:
            self.task_manager.update_task(
                task_id,
                status="completed",
                solution_path=str(path),
                filename=path.name,
                timings_json=timings_json,
                answer_card=result.get("answer_card", {}).get("text", ""),
                problem_type=str(result.get("problem_type") or ""),
                **transcript_fields,
            )
            self._sync_to_root_solutions(path)
            self._cleanup_old()
        else:
            # 取消：保留部分内容，记录路径以便前端继续查看。
            # 转录字段"能写多少写多少"—— OCR 在视觉阶段就已完成，
            # 取消只影响求解，因此这些字段此时通常是有值的（归档价值最高）。
            self.task_manager.update_task(
                task_id,
                status="cancelled",
                solution_path=str(path) if path else "",
                filename=path.name if path else "",
                timings_json=timings_json,
                error_message="",
                **transcript_fields,
            )
            # core 没带回转录字段时（旧版本 core / 异常降级）用缓存补上；
            # 已有值不会被覆盖（见 _backfill_cached_transcript）
            self._backfill_cached_transcript(task_id)

        return result

    def resolve(
        self,
        task_id: str,
        image_paths: list[Path],
        on_progress: Callable[[dict], None],
        *,
        problem_type: str,
        transcribed_text: str,
        enable_thinking: bool | None = None,
        style: str | None = None,
        cancel: CancelToken | None = None,
    ) -> dict:
        """换路重解：复用已识别的题目文本，只重跑求解。

        典型用法：第一版答案不满意 → 换风格 / 开关思考模式 → 几秒内拿第二版。
        `enable_thinking=None` 表示用配置默认值；显式的 True/False 优先。
        """
        self.task_manager.update_task(task_id, status="processing", error_message="")

        def _forward(event: dict) -> None:
            if event.get("type") == "done":
                self.task_manager.update_task(
                    task_id,
                    timings_json=json.dumps(event.get("timings") or {}, ensure_ascii=False),
                )
            on_progress(event)

        pipeline = SolutionPipeline(
            solution_dir=self.solution_dir,
            on_event=_forward,
            stage_cache=self.stage_cache,
        )

        try:
            result = pipeline.resolve(
                task_id,
                problem_type,
                transcribed_text,
                enable_thinking=enable_thinking,
                style=style,
                cancel=cancel,
                image_paths=image_paths,
            )
        except Exception as exc:
            logger.error("重新求解异常 task=%s: %s", task_id, exc, exc_info=True)
            self.task_manager.update_task(task_id, status="failed", error_message=str(exc))
            self._backfill_cached_transcript(task_id)
            raise

        path = result.get("path")
        if result.get("status") == "completed" and path is not None:
            self.task_manager.update_task(
                task_id,
                status="completed",
                solution_path=str(path),
                filename=Path(path).name,
                timings_json=json.dumps(result.get("timings") or {}, ensure_ascii=False),
                answer_card=result.get("answer_card", {}).get("text", ""),
                problem_type=str(result.get("problem_type") or problem_type),
            )
            self._sync_to_root_solutions(Path(path))
        else:
            self.task_manager.update_task(task_id, status="cancelled")
        # 重解不经过视觉阶段，沿用第一次跑出来的转录：只补**空**列，
        # 以免把已有的原始 OCR 覆盖成题面文本（core 的 resolve 也是这个约定）。
        self._backfill_cached_transcript(task_id)
        return result

    def verify(self, task_id: str, image_paths: list[Path], answer_text: str):
        """核对已完成的解答，并把结果追加到解答文件。

        注意：`verified` 标记由调用方（路由）负责写入，这里只做核对与落盘。

        Returns:
            VerificationResult
        """
        from problem_solver_agent.verify import append_verification_to_solution, verify_answer

        task = self.task_manager.get_task(task_id)
        if not task:
            raise ValueError("任务不存在")

        result = verify_answer(image_paths, answer_text)

        solution_path = task.get("solution_path") or ""
        if solution_path and Path(solution_path).exists():
            append_verification_to_solution(Path(solution_path), result)
        return result

    @staticmethod
    def extract_problem_text(solution_file: Path) -> str:
        """从已生成的解答文件中取出「题目文本」小节。

        换路重解需要原始题目文本；如果文件里没有记录（例如很旧的解答），
        调用方应回退到重新识别。
        """
        try:
            content = Path(solution_file).read_text(encoding="utf-8")
        except OSError:
            return ""

        # 解答文件结构：frontmatter → "# 题目文本" 小节 → "---" → "# 解答"
        marker = "# 题目文本"
        index = content.find(marker)
        if index == -1:
            return ""
        body = content[index + len(marker):]
        # 到第一个分隔线为止
        for sep in ("\n---\n", "\n# 解答"):
            cut = body.find(sep)
            if cut != -1:
                body = body[:cut]
        return body.strip()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _backfill_cached_transcript(self, task_id: str) -> None:
        """把阶段缓存里还留着的转录补写进 tasks 表（F5）。

        只补**空**列：已有值可能来自润色后/更完整的文本（例如取消分支写进去的
        `problem_text`），用逐页拼接去覆盖它属于数据降级；而空列补上逐页拼接
        总比让这次已经付费的 OCR 在库里完全没有痕迹要好。缓存不可用/已失效
        （切了 provider 或模型）时什么都不写 —— 宁可不写，也不写错内容。
        """
        fields = cached_transcript_fields(self.task_manager, task_id)
        if not fields:
            return
        task = self.task_manager.get_task(task_id) or {}
        missing = {
            key: value
            for key, value in fields.items()
            if not str(task.get(key) or "").strip()
        }
        if not missing:
            return
        try:
            self.task_manager.update_task(task_id, **missing)
        except Exception as exc:  # 补写失败不能影响失败状态与原始异常链
            logger.warning("补写转录字段失败 task=%s: %s", task_id, exc)

    def _sync_to_root_solutions(self, final_path: Path) -> None:
        """把解答同步到 ROOT_DIR/solutions，供手机端 Samba/文件管理器查看。"""
        try:
            target_dir = Path(core_config.SOLUTION_DIR)
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_path, target_dir / final_path.name)
            logger.info("解答已同步到 %s", target_dir / final_path.name)
        except Exception as exc:
            logger.warning("同步解答到 ROOT_DIR/solutions 失败: %s", exc)

    def _cleanup_old(self) -> None:
        """按数量与天数清理旧任务，并同步删除上传目录、OCR 归档与缓存。"""
        from .retention import cutoff_timestamp, prune_uploads

        # 先记下清理前的任务集合：cleanup_old_tasks 只回传 solution_path，
        # 拿不到被剪掉的任务 id，而 OCR 归档的文件名就是 task_id（组 I 第 1 层）。
        before = self.task_manager.all_task_ids()
        paths = self.task_manager.cleanup_old_tasks(
            keep=core_config.TASK_RETENTION_COUNT,
            older_than=cutoff_timestamp(core_config.TASK_RETENTION_DAYS),
        )
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

        # F6：`<OCR_DIR>/<日期>/<task_id>.md` 是任务产物，被剪掉的任务其归档也要删，
        # 否则文件永久留在磁盘上，而且再也没有任何记录指向它（无法追溯/清理）。
        delete_ocr_archives(before - self.task_manager.all_task_ids())

        # 上传目录按"数据库里仍存在的任务"保留（缺陷 N2：图片此前从不清理）
        from . import config as web_config

        prune_uploads(web_config.UPLOAD_DIR, self.task_manager.all_task_ids())
