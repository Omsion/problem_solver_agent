# -*- coding: utf-8 -*-
"""
test_config.py - 配置模块单元测试

测试 problem_solver_agent.config 中的配置验证和初始化功能。
"""

import os
import pytest
from pathlib import Path
from problem_solver_agent.config import (
    validate_config,
    initialize_directories,
)
from problem_solver_agent.config_types import (
    AgentConfig,
    SolverConfig,
    HotkeyConfig,
)


class TestValidateConfig:
    """测试配置验证功能。"""

    def test_validate_config_success(self, mock_config):
        """测试配置验证成功场景。"""
        # 不应抛出异常
        validate_config()

    def test_validate_config_missing_api_keys(self, monkeypatch):
        """测试 API 密钥缺失时的错误处理。"""
        # 清除所有 API 密钥
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)

        with pytest.raises(ValueError, match="DASHSCOPE_API_KEY 未设置"):
            validate_config()

    def test_validate_config_invalid_timeout(self, monkeypatch, mock_config):
        """测试无效的超时配置。"""
        monkeypatch.setattr("problem_solver_agent.config.API_TIMEOUT", -10.0)

        with pytest.raises(ValueError, match="不能为负数"):
            validate_config()

    def test_validate_config_invalid_retries(self, monkeypatch, mock_config):
        """测试无效的重试次数配置。"""
        monkeypatch.setattr("problem_solver_agent.config.MAX_RETRIES", -1)

        with pytest.raises(ValueError, match="不能为负数"):
            validate_config()


class TestInitializeDirectories:
    """测试目录初始化功能。"""

    def test_initialize_directories_success(self, tmp_path):
        """测试目录初始化成功场景。"""
        from problem_solver_agent import config

        # Mock 目录配置
        original_root = config.ROOT_DIR
        original_processed = config.PROCESSED_DIR
        original_solution = config.SOLUTION_DIR

        try:
            # 设置临时目录
            config.ROOT_DIR = tmp_path
            config.PROCESSED_DIR = tmp_path / "processed"
            config.SOLUTION_DIR = tmp_path / "solutions"

            # 初始化目录
            initialize_directories()

            # 验证目录已创建
            assert config.PROCESSED_DIR.exists()
            assert config.SOLUTION_DIR.exists()
        finally:
            # 恢复原始配置
            config.ROOT_DIR = original_root
            config.PROCESSED_DIR = original_processed
            config.SOLUTION_DIR = original_solution


class TestSolverConfig:
    """测试求解器配置类。"""

    def test_solver_config_creation(self):
        """测试创建有效的求解器配置。"""
        config = SolverConfig(
            model="test-model",
            base_url="https://api.test.com/v1"
        )
        assert config.model == "test-model"
        assert config.base_url == "https://api.test.com/v1"

    def test_solver_config_empty_model(self):
        """测试空模型名称。"""
        with pytest.raises(ValueError, match="model 不能为空"):
            SolverConfig(model="", base_url="https://api.test.com")

    def test_solver_config_empty_base_url(self):
        """测试空 base_url。"""
        with pytest.raises(ValueError, match="base_url 不能为空"):
            SolverConfig(model="test-model", base_url="")


class TestHotkeyConfig:
    """测试热键配置类。"""

    def test_hotkey_config_defaults(self):
        """测试热键配置的默认值。"""
        config = HotkeyConfig()
        assert config.modifiers_vk == [0x12]
        assert config.key_vk == ord('X')
        assert config.string == "Alt + X"

    def test_hotkey_config_custom(self):
        """测试自定义热键配置。"""
        config = HotkeyConfig(
            modifiers_vk=[0x11],  # VK_CONTROL
            key_vk=ord('S'),
            string="Ctrl + S"
        )
        assert config.modifiers_vk == [0x11]
        assert config.key_vk == ord('S')
        assert config.string == "Ctrl + S"


class TestAgentConfig:
    """测试主配置类。"""

    def test_agent_config_defaults(self):
        """测试默认配置值。"""
        config = AgentConfig(
            dashscope_api_key="test-key",
        )

        assert config.dashscope_api_key == "test-key"
        assert config.api_timeout == 600.0
        assert config.max_retries == 3
        assert config.retry_delay == 10.0

    def test_agent_config_path_computation(self, tmp_path):
        """测试路径自动计算功能。"""
        config = AgentConfig(
            dashscope_api_key="test-key",
            root_dir=tmp_path,
        )

        # 验证派生路径
        assert config.monitor_dir == tmp_path / "Screenshots"
        assert config.processed_dir == tmp_path / "processed"
        assert config.solution_dir == tmp_path / "solutions"

    def test_agent_config_no_api_keys(self):
        """测试缺少 API 密钥时的验证。"""
        with pytest.raises(ValueError, match="至少需要配置一个 API 密钥"):
            AgentConfig()

    def test_agent_config_validate_success(self):
        """测试配置验证成功。"""
        config = AgentConfig(
            dashscope_api_key="test-key",
        )
        assert config.validate() is True

    def test_agent_config_invalid_timeout(self):
        """测试无效的超时配置。"""
        with pytest.raises(ValueError, match="api_timeout 不能为负数"):
            AgentConfig(
                dashscope_api_key="test-key",
                api_timeout=-10.0,
            )
