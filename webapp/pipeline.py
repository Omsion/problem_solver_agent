# -*- coding: utf-8 -*-
"""流水线服务 — 将截图转为结构化解答，通过回调推送进度事件"""

import logging
from pathlib import Path
from typing import Callable, List

from problem_solver_agent import config as core_config
from problem_solver_agent import vision_client, solver_client
from problem_solver_agent.utils import sanitize_filename, extract_question_numbers, format_number_prefix

logger = logging.getLogger("WebappPipeline")


class PipelineService:

    def __init__(self, solution_dir: Path, task_manager):
        self.solution_dir = solution_dir
        self.solution_dir.mkdir(parents=True, exist_ok=True)
        self.task_manager = task_manager

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def run(self, task_id: str, image_paths: List[Path], on_progress: Callable[[dict], None],
            enable_thinking: bool = True) -> None:
        """串行执行完整流水线，阶段切换 / 流式内容均通过 on_progress 推送。

        Args:
            enable_thinking: 是否启用求解器思考模式（DeepSeek reasoning）。开启后
                            思考过程以 type="reasoning" 事件独立推送，不影响最终答案。
        """
        transcribed_text = "N/A"
        temp_path = None

        try:
            self.task_manager.update_task(task_id, status="processing")

            # ---- 步骤 1：分类 ----
            on_progress({"type": "status", "phase": "classifying", "message": "正在进行问题类型分类…"})
            problem_type = vision_client.classify_problem_type(image_paths)
            self.task_manager.update_task(task_id, problem_type=problem_type)

            # ---- 步骤 2：OCR + 润色 ----
            if problem_type != "VISUAL_REASONING":
                on_progress({"type": "status", "phase": "ocr", "message": f"正在进行 OCR 转录（{len(image_paths)} 张图片）…"})
                transcribed_text = self._textualize_problem(image_paths)
                problem_type = self._reclassify(problem_type, transcribed_text)
                self.task_manager.update_task(task_id, problem_type=problem_type)

            # ---- 步骤 3：求解 ----
            on_progress({"type": "status", "phase": "solving", "message": "正在调用求解器生成解答…"})
            final_type, solver_provider, solver_model, stream = self._start_solve(problem_type, transcribed_text, image_paths, enable_thinking)
            self.task_manager.update_task(task_id, solver_provider=solver_provider, solver_model=solver_model)

            # ---- 写入文件 + 流式推送 ----
            temp_path = self.solution_dir / f"{task_id}_inprogress.md"
            full_answer: list[str] = []
            with open(temp_path, "w", encoding="utf-8") as f:
                self._write_header(f, image_paths, final_type, transcribed_text, solver_provider, solver_model)
                for event in stream:
                    ev_type = event.get("type", "") if isinstance(event, dict) else ""
                    if ev_type == "reasoning":
                        # 思考过程：仅推送 SSE，不写入解答文件
                        on_progress({"type": "reasoning", "content": event["content"]})
                    elif ev_type == "content":
                        # 解答内容：写入文件 + 推送 SSE
                        full_answer.append(event["content"])
                        f.write(event["content"])
                        f.flush()
                        on_progress({"type": "chunk", "content": event["content"]})
                    elif ev_type == "error":
                        raise ValueError(event["content"])
                    else:
                        # 向后兼容：纯字符串 chunk（来自非 dict 流）
                        chunk = event if isinstance(event, str) else event.get("content", "")
                        full_answer.append(chunk)
                        f.write(chunk)
                        f.flush()
                        on_progress({"type": "chunk", "content": chunk})

            full_text = "".join(full_answer)
            if not full_text.strip() or "--- ERROR ---" in full_text:
                raise ValueError("求解器返回空响应或包含内部错误")

            # ---- 步骤 4：命名 & 归档 ----
            final_path = self._generate_filename(transcribed_text, final_type, task_id)
            temp_path.replace(final_path)

            self._cleanup_images(image_paths)
            self.task_manager.update_task(task_id, status="completed", solution_path=str(final_path), filename=final_path.name)
            self._cleanup_old()

            on_progress({"type": "done", "task_id": task_id, "filename": final_path.name})

        except Exception as exc:
            logger.error("流水线异常 task=%s: %s", task_id, exc, exc_info=True)
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            self.task_manager.update_task(task_id, status="failed", error_message=str(exc))
            on_progress({"type": "error", "message": str(exc)})

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    def _textualize_problem(self, image_paths: List[Path]) -> str:
        raw = vision_client.transcribe_images_raw(image_paths)
        if not raw:
            raise ValueError("OCR 转录返回空结果")
        joined = "\n---[NEXT]---\n".join(raw)
        prompt = core_config.TEXT_MERGE_AND_POLISH_PROMPT.format(raw_texts=joined)
        polished = solver_client.ask_for_analysis(prompt, provider=core_config.AUX_PROVIDER, model=core_config.AUX_MODEL_NAME)
        if not polished:
            raise ValueError("文本合并 / 润色失败")
        if len(polished) < 5:
            raise ValueError("合并后文本过短，质量检查未通过")
        return polished

    def _reclassify(self, problem_type: str, text: str) -> str:
        if problem_type not in {"GENERAL", "FILL_IN_THE_BLANKS", "QUESTION_ANSWERING", "CODING"}:
            return problem_type

        ml_keywords = [
            "numpy", "torch", "tensorflow", "mlp", "transformer", "注意力",
            "normalization", "norm", "cnn", "rnn", "神经网络", "感知机",
            "反向传播", "前向传播", "mnist", "cifar",
        ]
        coding_keywords = ["手撕", "算法", "leetcode", "acm", "代码", "函数", "实现", "编程"]

        text_lower = text.lower()
        if any(kw in text_lower for kw in ml_keywords):
            return "ML_CODING"
        if any(kw in text_lower for kw in coding_keywords) and problem_type != "CODING":
            return "CODING"
        return problem_type

    def _start_solve(self, problem_type: str, transcribed_text: str, image_paths: List[Path],
                     enable_thinking: bool = True):
        if problem_type == "VISUAL_REASONING":
            final_type = "VISUAL_REASONING"
            provider = "GLM-4.6V"
            model = core_config.VISION_REASONING_MODEL
            stream = vision_client.solve_visual_reasoning_problem(image_paths)
        else:
            final_type = self._map_final_type(problem_type, transcribed_text)
            provider, model = self._determine_solver(final_type)
            prompt = self._build_prompt(final_type, transcribed_text)
            stream = solver_client.stream_solve(prompt, provider, model, enable_thinking=enable_thinking)
        return final_type, provider, model, stream

    def _map_final_type(self, problem_type: str, text: str) -> str:
        if problem_type == "ML_CODING":
            return "ML_CODING"
        if problem_type == "CODING":
            return "LEETCODE" if "leetcode" in text.lower() else "ACM"
        return problem_type

    def _determine_solver(self, final_type: str) -> tuple[str, str]:
        if final_type in ("LEETCODE", "ACM", "ML_CODING"):
            provider = core_config.SOLVER_ROUTING_CONFIG["CODING_SOLVER"]
        else:
            provider = core_config.SOLVER_ROUTING_CONFIG["DEFAULT_SOLVER"]
        return provider, core_config.SOLVER_CONFIG[provider]["model"]

    def _build_prompt(self, final_type: str, text: str) -> str:
        template = core_config.PROMPT_TEMPLATES.get(final_type)
        if not template:
            raise ValueError(f"缺少 '{final_type}' 的 Prompt 模板")
        if final_type in ("LEETCODE", "ACM", "ML_CODING"):
            template = template[core_config.SOLUTION_STYLE]
        return template.format(transcribed_text=text)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _write_header(self, f, image_paths, problem_type, transcribed_text, solver_provider, solver_model):
        f.write("Processed by Web Agent:\n- " + "\n- ".join(p.name for p in image_paths) + "\n\n")
        f.write("=" * 50 + "\n")
        f.write(f"- Detected Problem Type: {problem_type}\n")
        f.write(f"- Selected Solver: {solver_provider} ({solver_model})\n")
        f.write(f"- Auxiliary Model: {core_config.AUX_PROVIDER} ({core_config.AUX_MODEL_NAME})\n\n")
        f.write("=" * 50 + "\n\n")
        f.write("Transcribed & Polished Text:\n" + transcribed_text + "\n\n")
        f.write("=" * 50 + "\n\n")
        style = f" (Style: {core_config.SOLUTION_STYLE})" if problem_type in ("LEETCODE", "ACM", "ML_CODING") else ""
        f.write(f"Final Solution{style}:\n")
        f.flush()

    def _generate_filename(self, text: str, problem_type: str, task_id: str) -> Path:
        prompt = core_config.FILENAME_GENERATION_PROMPT.format(transcribed_text=text)
        filename_body = solver_client.ask_for_analysis(
            prompt, provider=core_config.AUX_PROVIDER, model=core_config.AUX_MODEL_NAME
        )
        if not filename_body:
            numbers = extract_question_numbers(text)
            prefix = format_number_prefix(numbers)
            fallback = f"{problem_type}_Solution"
            filename_body = f"{prefix}_{fallback}" if prefix else fallback

        safe = sanitize_filename(filename_body)
        return self.solution_dir / f"{safe}.md"

    @staticmethod
    def _cleanup_images(image_paths: List[Path]) -> None:
        for p in image_paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def _cleanup_old(self) -> None:
        paths = self.task_manager.cleanup_old_tasks(keep=100)
        for p in paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
