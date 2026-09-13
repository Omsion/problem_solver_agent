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
from collections.abc import Callable
from pathlib import Path

from problem_solver_agent import config as core_config
from problem_solver_agent.cancel import CancelToken
from problem_solver_agent.core_pipeline import SolutionPipeline

logger = logging.getLogger("WebappPipeline")

# 需要在解答文件里保留的状态（用于判断"是否已完成"）
TERMINAL_COMPLETED = "completed"
TERMINAL_CANCELLED = "cancelled"


class _StageCache:
    """把阶段结果写进 SQLite，供重试时复用（避免重复调用付费 API）。"""

    STAGES = ("vision",)

    def __init__(self, task_manager) -> None:
        self.task_manager = task_manager

    def get(self, task_id: str, stage: str):
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
            raise

        status = result.get("status")
        path: Path | None = result.get("path")
        timings_json = json.dumps(result.get("timings") or {}, ensure_ascii=False)

        if status == TERMINAL_COMPLETED and path is not None:
            self.task_manager.update_task(
                task_id,
                status="completed",
                solution_path=str(path),
                filename=path.name,
                timings_json=timings_json,
                answer_card=result.get("answer_card", {}).get("text", ""),
                problem_type=str(result.get("problem_type") or ""),
            )
            self._sync_to_root_solutions(path)
            self._cleanup_old()
        else:
            # 取消：保留部分内容，记录路径以便前端继续查看
            self.task_manager.update_task(
                task_id,
                status="cancelled",
                solution_path=str(path) if path else "",
                filename=path.name if path else "",
                timings_json=timings_json,
                error_message="",
            )

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
        """按数量与天数清理旧任务，并同步删除上传目录与缓存。"""
        from .retention import cutoff_timestamp, prune_uploads

        paths = self.task_manager.cleanup_old_tasks(
            keep=core_config.TASK_RETENTION_COUNT,
            older_than=cutoff_timestamp(core_config.TASK_RETENTION_DAYS),
        )
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

        # 上传目录按"数据库里仍存在的任务"保留（缺陷 N2：图片此前从不清理）
        from . import config as web_config

        prune_uploads(web_config.UPLOAD_DIR, self.task_manager.all_task_ids())
