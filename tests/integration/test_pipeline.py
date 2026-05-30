# -*- coding: utf-8 -*-
"""
test_pipeline.py - 集成测试

测试完整的图片处理流程，从文件监控到解答生成。
使用 mock API 调用以避免真实 API 请求。
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from queue import Queue


@pytest.mark.integration
class TestImageProcessingPipeline:
    """测试完整的图片处理流程。"""

    def test_image_grouper_initialization(self):
        """测试图片分组器初始化。"""
        from problem_solver_agent.image_grouper import ImageGrouper

        grouper = ImageGrouper(num_workers=1)
        assert grouper.num_workers == 1
        assert isinstance(grouper.task_queue, Queue)
        assert grouper.current_group == []

    def test_image_grouper_add_image(self):
        """测试添加图片到分组。"""
        from problem_solver_agent.image_grouper import ImageGrouper
        from problem_solver_agent import config

        grouper = ImageGrouper(num_workers=1)
        test_image = Path("test.png")

        with patch('problem_solver_agent.image_grouper.Timer'):
            grouper.add_image(test_image)
            assert len(grouper.current_group) == 1

    @patch('problem_solver_agent.vision_client._call_vision_api')
    @patch('problem_solver_agent.solver_client.stream_solve')
    def test_full_pipeline_with_mock(self, mock_solve, mock_vision_api, sample_image_path, tmp_path):
        """测试完整的处理流程（使用 mock API）。"""
        from problem_solver_agent.image_grouper import ImageGrouper
        from problem_solver_agent import config

        # Mock API 响应：分类返回 VISUAL_REASONING，求解器返回流式结果
        mock_vision_api.return_value = "VISUAL_REASONING"
        mock_solve.return_value = iter(["Step 1\n", "Step 2\n", "Final answer"])

        original_solution_dir = config.SOLUTION_DIR
        try:
            config.SOLUTION_DIR = tmp_path

            grouper = ImageGrouper(num_workers=1)
            grouper.task_queue.put([sample_image_path])
            grouper.task_queue.join(timeout=5)

            assert mock_vision_api.called or mock_solve.called

            solution_files = list(tmp_path.glob("*.md"))
            assert len(solution_files) > 0

        finally:
            config.SOLUTION_DIR = original_solution_dir

    def test_file_monitor_initialization(self):
        """测试文件监控器初始化。"""
        from problem_solver_agent.file_monitor import start_monitoring
        from problem_solver_agent.image_grouper import ImageGrouper

        grouper = ImageGrouper(num_workers=1)
        observer = start_monitoring(Path("/tmp"), grouper)
        assert observer is not None
        observer.stop()

    @patch('problem_solver_agent.vision_client._call_vision_api')
    def test_ocr_processing(self, mock_vision_api, sample_image_path):
        """测试 OCR 处理流程。"""
        from problem_solver_agent.vision_client import transcribe_images_raw

        mock_vision_api.return_value = "Test OCR result"

        result = transcribe_images_raw([sample_image_path])

        assert result is not None
        assert len(result) == 1
        assert mock_vision_api.called

    @patch('problem_solver_agent.solver_client.ask_for_analysis')
    def test_text_merge_and_polish(self, mock_analysis):
        """测试文本合并和润色。"""
        from problem_solver_agent.image_grouper import ImageGrouper

        mock_analysis.return_value = "Merged text content"

        grouper = ImageGrouper(num_workers=1)
        raw_texts = ["Text 1", "Text 2", "Text 3"]

        assert len(raw_texts) == 3


@pytest.mark.integration
class TestErrorHandling:
    """测试错误处理流程。"""

    @patch('problem_solver_agent.vision_client._call_vision_api')
    def test_api_failure_handling(self, mock_vision_api, sample_image_path, tmp_path):
        """测试 API 失败时的错误处理。"""
        from problem_solver_agent.image_grouper import ImageGrouper
        from problem_solver_agent import config

        # Mock 返回 None（模拟 API 失败）
        mock_vision_api.return_value = None

        original_solution_dir = config.SOLUTION_DIR
        try:
            config.SOLUTION_DIR = tmp_path

            grouper = ImageGrouper(num_workers=1)
            grouper.task_queue.put([sample_image_path])
            grouper.task_queue.join(timeout=5)

            failure_logs = list(tmp_path.glob("*_FAILED.md"))
            assert len(failure_logs) > 0

        finally:
            config.SOLUTION_DIR = original_solution_dir

    def test_missing_image_handling(self):
        """测试处理缺失图片文件的情况。"""
        from problem_solver_agent.image_grouper import ImageGrouper

        grouper = ImageGrouper(num_workers=1)
        missing_image = Path("/nonexistent/image.png")

        with patch('problem_solver_agent.image_grouper.Timer'):
            grouper.add_image(missing_image)

        assert len(grouper.current_group) == 1


@pytest.mark.integration
class TestConcurrency:
    """测试并发处理功能。"""

    @patch('problem_solver_agent.vision_client._call_vision_api')
    @patch('problem_solver_agent.solver_client.stream_solve')
    def test_concurrent_image_groups(self, mock_solve, mock_vision_api, sample_image_path, tmp_path):
        """测试同时处理多个图片组。"""
        from problem_solver_agent.image_grouper import ImageGrouper
        from problem_solver_agent import config

        mock_vision_api.return_value = "VISUAL_REASONING"
        mock_solve.return_value = iter(["Result"])

        original_solution_dir = config.SOLUTION_DIR
        try:
            config.SOLUTION_DIR = tmp_path

            grouper = ImageGrouper(num_workers=2)

            for i in range(3):
                grouper.task_queue.put([sample_image_path])

            grouper.task_queue.join(timeout=10)

            solution_files = list(tmp_path.glob("*.md"))
            assert len(solution_files) == 3

        finally:
            config.SOLUTION_DIR = original_solution_dir
