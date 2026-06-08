# -*- coding: utf-8 -*-
"""核心包基础测试 — 验证配置加载和共享函数。"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from problem_solver_agent.config import (
    reclassify_problem_type,
    map_final_type,
    determine_solver,
    ML_KEYWORDS,
    CODING_KEYWORDS,
    validate_config,
    NUM_WORKERS,
    OCR_PARALLEL_WORKERS,
    VISION_PROVIDER_NAME,
)


class TestReclassify:
    """验证共享重分类函数。"""

    def test_ml_coding_detection(self):
        """包含 ML 关键词的文本应被重新分类为 ML_CODING。"""
        result = reclassify_problem_type("GENERAL", "请使用 numpy 实现一个 Transformer 模型")
        assert result == "ML_CODING"

    def test_coding_detection(self):
        """包含编程关键词的非 CODING 类型应被修正。"""
        result = reclassify_problem_type("QUESTION_ANSWERING", "请编写一个算法实现 leetcode 题目")
        assert result == "CODING"

    def test_no_change(self):
        """不包含关键词的文本应保持原分类。"""
        result = reclassify_problem_type("GENERAL", "这是一道数学题")
        assert result == "GENERAL"

    def test_visual_reasoning_passthrough(self):
        """VISUAL_REASONING 类型不应被重分类。"""
        result = reclassify_problem_type("VISUAL_REASONING", "包含 numpy 的图形推理题")
        assert result == "VISUAL_REASONING"

    def test_na_text_passthrough(self):
        """N/A 文本应保持原分类。"""
        result = reclassify_problem_type("GENERAL", "N/A")
        assert result == "GENERAL"


class TestMapFinalType:
    """验证问题类型映射函数。"""

    def test_ml_coding_identity(self):
        assert map_final_type("ML_CODING", "") == "ML_CODING"

    def test_coding_to_leetcode(self):
        assert map_final_type("CODING", "leetcode 两数之和") == "LEETCODE"

    def test_coding_to_acm(self):
        assert map_final_type("CODING", "请实现快速排序") == "ACM"

    def test_other_passthrough(self):
        assert map_final_type("GENERAL", "") == "GENERAL"


class TestDetermineSolver:
    """验证求解器选择函数。"""

    def test_coding_solver(self):
        for pt in ("LEETCODE", "ACM", "ML_CODING"):
            provider, model = determine_solver(pt)
            assert provider != ""
            assert model != ""

    def test_default_solver(self):
        provider, model = determine_solver("GENERAL")
        assert provider != ""
        assert model != ""


class TestConfig:
    """验证配置值在合理范围内。"""

    def test_num_workers_valid(self):
        assert NUM_WORKERS >= 1, "NUM_WORKERS 至少为 1"

    def test_ocr_workers_valid(self):
        assert OCR_PARALLEL_WORKERS >= 1, "OCR_PARALLEL_WORKERS 至少为 1"

    def test_keywords_not_empty(self):
        assert len(ML_KEYWORDS) > 0
        assert len(CODING_KEYWORDS) > 0

    def test_vision_provider_name(self):
        assert VISION_PROVIDER_NAME == "zhipu"

    def test_keywords_lowercase(self):
        """关键词应为小写以匹配 text.lower()。"""
        for kw in ML_KEYWORDS:
            assert kw == kw.lower(), f"ML_KEYWORDS '{kw}' 应为小写"
        for kw in CODING_KEYWORDS:
            assert kw == kw.lower() or kw.isascii() is False, f"CODING_KEYWORDS '{kw}' 异常"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
