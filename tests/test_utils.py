# -*- coding: utf-8 -*-
"""
test_utils.py - 工具函数单元测试

测试 problem_solver_agent.utils 中的所有工具函数。
"""

import pytest
from pathlib import Path
from problem_solver_agent.utils import (
    sanitize_filename,
    extract_question_numbers,
    format_number_prefix,
)


class TestSanitizeFilename:
    """测试文件名清理功能。"""

    def test_remove_illegal_chars(self):
        """测试移除非法文件名字符。"""
        assert sanitize_filename("test/file?.txt") == "testfile.txt"
        assert sanitize_filename("data<info>.csv") == "datainfo.csv"
        assert sanitize_filename("name|with|pipes.txt") == "namewithpipes.txt"

    def test_keep_valid_chars(self):
        """测试保留有效字符。"""
        assert sanitize_filename("valid-name_123.txt") == "valid-name_123.txt"
        assert sanitize_filename("中文文件名.txt") == "中文文件名.txt"

    def test_empty_string(self):
        """测试空字符串处理。"""
        assert sanitize_filename("") == ""

    def test_multiple_separators(self):
        """测试多个分隔符的连续处理。"""
        assert sanitize_filename("test//file\\\\name.txt") == "testfilename.txt"


class TestExtractQuestionNumbers:
    """测试题号提取功能。"""

    def test_extract_simple_numbers(self):
        """测试提取简单题号。"""
        text = "1. 第一题\n2. 第二题\n3. 第三题"
        numbers = extract_question_numbers(text)
        assert numbers == [1, 2, 3]

    def test_extract_with_spaces(self):
        """测试带空格的题号提取。"""
        text = "  1. 第一题  \n  2. 第二题  "
        numbers = extract_question_numbers(text)
        assert numbers == [1, 2]

    def test_extract_with_ideographic_pause(self):
        """测试使用顿号的题号提取。"""
        text = "1、第一题\n2、第二题"
        numbers = extract_question_numbers(text)
        assert numbers == [1, 2]

    def test_remove_duplicates(self):
        """测试去重功能。"""
        text = "1. 第一题\n2. 第二题\n1. 重复的第一题"
        numbers = extract_question_numbers(text)
        assert numbers == [1, 2]  # 去重

    def test_no_numbers_found(self):
        """测试没有题号的情况。"""
        text = "这是一段没有题号的文本。"
        numbers = extract_question_numbers(text)
        assert numbers == []

    def test_sort_numbers(self):
        """测试排序功能。"""
        text = "5. 第五题\n2. 第二题\n8. 第八题"
        numbers = extract_question_numbers(text)
        assert numbers == [2, 5, 8]  # 已排序


class TestFormatNumberPrefix:
    """测试题号前缀格式化功能。"""

    def test_single_number(self):
        """测试单个题号的格式化。"""
        numbers = [16]
        result = format_number_prefix(numbers)
        assert result == "16"

    def test_consecutive_numbers(self):
        """测试连续题号的格式化。"""
        numbers = [16, 17, 18, 19, 20]
        result = format_number_prefix(numbers)
        assert result == "16-20"

    def test_non_consecutive_numbers(self):
        """测试不连续题号的格式化。"""
        numbers = [1, 2, 5]
        result = format_number_prefix(numbers)
        assert result == "1,2,5"

    @pytest.mark.parametrize("numbers,expected", [
        ([1], "1"),
        ([1, 2, 3], "1-3"),
        ([1, 3, 5], "1,3,5"),
        ([10, 11, 12, 13, 14], "10-14"),
    ])
    def test_various_number_formats(self, numbers, expected):
        """参数化测试各种题号格式。"""
        result = format_number_prefix(numbers)
        assert result == expected

    def test_empty_list(self):
        """测试空列表的格式化。"""
        result = format_number_prefix([])
        assert result == ""
