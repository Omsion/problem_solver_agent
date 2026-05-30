# -*- coding: utf-8 -*-
"""
config_types.py - 配置类型定义（基于 dataclass）

本模块定义了类型安全的配置类，使用 dataclass 封装配置项。
这些类型提供：
1. 类型提示和 IDE 自动补全
2. 配置验证（通过 __post_init__）
3. 不可变配置（frozen=True）
4. 清晰的文档字符串

使用方法:
    from .config_types import AgentConfig, SolverConfig
    
    config = AgentConfig(
        deepseek_api_key="sk-xxx",
        zhipu_api_key="xxx",
    )
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Literal


@dataclass(frozen=True)
class SolverConfig:
    """
    单个求解器的配置。

    Attributes:
        model: 模型名称
        base_url: API 端点 URL
    """
    model: str
    base_url: str

    def __post_init__(self):
        """验证配置有效性。"""
        if not self.model:
            raise ValueError("model 不能为空")
        if not self.base_url:
            raise ValueError("base_url 不能为空")


@dataclass(frozen=True)
class HotkeyConfig:
    """
    全局热键配置。

    Attributes:
        modifiers_vk: 修饰键虚拟键码列表
        key_vk: 主键虚拟键码
        string: 用于日志输出的字符串表示
    """
    modifiers_vk: list[int] = field(default_factory=lambda: [0x12])
    key_vk: int = field(default_factory=lambda: ord('X'))
    string: str = "Alt + X"


@dataclass
class AgentConfig:
    """
    主配置类（可变，允许运行时调整）。

    注意：此类被标记为 frozen=False，允许在运行时修改配置。
    对于不可变配置，使用 frozen=True。

    Attributes:
        # API 密钥
        deepseek_api_key: DeepSeek API 密钥
        zhipu_api_key: 智谱 AI API 密钥

        # API 配置
        api_timeout: API 超时时间（秒）
        max_retries: 最大重试次数
        retry_delay: 重试延迟（秒）

        # 模型配置
        vision_base_url: 视觉模型 API 端点 (Zhipu)
        vision_classify_model: 视觉分类/OCR 模型
        vision_reasoning_model: 视觉推理模型

        # 辅助模型配置
        aux_provider: 辅助模型提供商
        aux_model_name: 辅助模型名称

        # 路径配置
        root_dir: 根目录
        monitor_dir: 监控目录
        processed_dir: 已处理文件目录
        solution_dir: 解答文件目录

        # 行为配置
        group_timeout: 分组超时时间（秒）
        allowed_extensions: 允许的文件扩展名
        solution_style: 求解风格（'EXPLORATORY' or 'OPTIMAL'）

        # 工具配置
        hotkey_config: 热键配置
        remote_trigger_port: 远程触发端口

        # 求解器配置
        solver_routing_config: 问题类型到求解器的映射
        solver_configs: 各求解器的详细配置
    """
    # API 密钥
    deepseek_api_key: str | None = None
    zhipu_api_key: str | None = None

    # API 配置
    api_timeout: float = 600.0
    max_retries: int = 3
    retry_delay: float = 10.0

    # 模型配置
    vision_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    vision_classify_model: str = "GLM-4.6V-FlashX"
    vision_reasoning_model: str = "GLM-4.6V"

    # 辅助模型配置
    aux_provider: str = "deepseek"
    aux_model_name: str = "deepseek-v4-flash"

    # 路径配置
    root_dir: Path = field(default_factory=lambda: Path(os.getenv(
        "SOLVER_ROOT_DIR", str(Path(__file__).resolve().parent.parent.parent)
    )))
    monitor_dir: Path | None = None
    processed_dir: Path | None = None
    solution_dir: Path | None = None

    # 行为配置
    group_timeout: float = 8.0
    allowed_extensions: tuple[str, ...] = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')
    solution_style: Literal["EXPLORATORY", "OPTIMAL"] = "OPTIMAL"

    # 工具配置
    hotkey_config: HotkeyConfig = field(default_factory=HotkeyConfig)
    remote_trigger_port: int = 5555

    # 求解器配置
    solver_routing_config: Dict[str, str] = field(default_factory=lambda: {
        "CODING_SOLVER": "deepseek",
        "DEFAULT_SOLVER": "deepseek"
    })
    solver_configs: Dict[str, SolverConfig] = field(default_factory=lambda: {
        "deepseek": SolverConfig(
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/v1"
        )
    })

    def __post_init__(self):
        """配置后处理：计算派生路径，验证配置。"""
        # 计算派生路径
        if self.monitor_dir is None:
            self.monitor_dir = self.root_dir / "Screenshots"
        if self.processed_dir is None:
            self.processed_dir = self.root_dir / "processed"
        if self.solution_dir is None:
            self.solution_dir = self.root_dir / "solutions"

        # 验证关键配置
        if not any([self.deepseek_api_key, self.zhipu_api_key]):
            raise ValueError("至少需要配置一个 API 密钥")

        if self.api_timeout < 0:
            raise ValueError("api_timeout 不能为负数")

        if self.max_retries < 0:
            raise ValueError("max_retries 不能为负数")

    def validate(self) -> bool:
        """
        验证配置是否有效。

        Returns:
            bool: 配置是否有效

        Raises:
            ValueError: 当配置无效时抛出
        """
        # 验证路径
        if not self.root_dir:
            raise ValueError("root_dir 未设置")

        # 验证求解器配置
        for provider, solver_config in self.solver_configs.items():
            if not solver_config.model or not solver_config.base_url:
                raise ValueError(f"求解器 '{provider}' 配置无效")

        return True
