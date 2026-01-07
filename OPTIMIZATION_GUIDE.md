# OnlineTest 项目优化指南

> 本文档提供了完整的项目重构/优化计划，按阶段组织，每个阶段在独立的 Git 分支上开发。

**当前状态**: ✅ 阶段 1 和阶段 2 已完成并合并到主分支

---

## 📋 优化概览

| 阶段 | 主题 | 优先级 | 工作量 | 状态 |
|------|------|--------|--------|------|
| 阶段 1 | 紧急修复 | 🔴 最高 | 2-3h | ✅ 已完成 |
| 阶段 2 | 架构优化 | 🟡 高 | 6-8h | ✅ 已完成 |
| 阶段 3 | 代码质量 | 🟡 中 | 8-10h | ⏳ 待完成 |
| 阶段 4 | 测试覆盖 | 🟢 推荐 | 10-15h | ⏳ 待完成 |
| 阶段 5 | 性能优化 | 🟢 可选 | 4-6h | ⏳ 待完成 |
| 阶段 6 | 开发体验 | 🟢 可选 | 2-4h | ⏳ 待完成 |

---

## ✅ 阶段 1：紧急修复（已完成）

**分支**: `feature/phase1-critical-fixes` ✅ 已合并

### 已完成的任务

#### 1.1 修复 TypedDict 语法错误
- **文件**: `problem_solver_agent/solver_client.py`
- **问题**: TypedDict 定义中使用了无效的分号
- **修复**: 验证代码语法正确（实际已无需修改）
- **提交**: `24615b6`

#### 1.2 更新依赖清单
- **文件**: `requirements.txt`
- **新增依赖**:
  - `openai>=1.0.0` - OpenAI SDK
  - `Pillow>=10.0.0` - 图像处理
  - `keyboard>=0.13.0` - 热键监听
  - `pywin32>=306.0.0` - Windows API（仅 Windows）
- **改进**: 添加详细的注释和版本说明

#### 1.3 修复配置导入问题
- **文件**: `problem_solver_agent/config.py`
- **问题**: 使用 `from .prompts import *` 破坏模块封装
- **修复**: 改为显式导入所需变量
  ```python
  from .prompts import (
      CLASSIFICATION_PROMPT,
      TRANSCRIPTION_PROMPT,
      TEXT_MERGE_AND_POLISH_PROMPT,
      FILENAME_GENERATION_PROMPT,
      PROMPT_TEMPLATES,
  )
  ```

### 验收结果
- [x] 代码通过语法检查
- [x] 依赖清单完整
- [x] 模块导入清晰
- [x] 已合并到主分支

---

## ✅ 阶段 2：架构优化（已完成）

**分支**: `feature/phase2-architecture` ✅ 已合并

### 已完成的任务

#### 2.1 重构 Logger 系统（单例模式）
- **文件**: `problem_solver_agent/utils.py`
- **改进**:
  - 使用单例模式，确保全局只有一个 Logger 实例
  - 添加 `_logger` 模块级变量缓存实例
  - 添加 `propagate = False` 防止日志重复
- **收益**:
  - 避免重复创建 Handler
  - 减少资源消耗
  - 统一日志配置

#### 2.2 定义包的公共 API
- **文件**: `problem_solver_agent/__init__.py`
- **新增内容**:
  ```python
  from .image_grouper import ImageGrouper
  from .file_monitor import start_monitoring

  __all__ = ["ImageGrouper", "start_monitoring"]
  __version__ = "2.2.0"
  ```
- **收益**:
  - 清晰的包接口定义
  - IDE 自动补全友好
  - 便于文档生成

#### 2.3 创建配置验证系统
- **文件**: `problem_solver_agent/config.py`
- **新增函数**: `validate_config()`
- **检查项目**:
  - API 密钥是否已配置
  - 路径配置是否有效
  - 超时和重试设置是否合理
  - 求解器配置是否完整
- **收益**:
  - 早期发现配置错误
  - 提供清晰的错误提示
  - 避免运行时崩溃

#### 2.4 创建配置管理类
- **文件**: `problem_solver_agent/config_types.py` (新文件)
- **内容**:
  - `SolverConfig` - 单个求解器配置
  - `HotkeyConfig` - 热键配置
  - `AgentConfig` - 主配置类（基于 dataclass）
- **特性**:
  - 类型安全的配置定义
  - IDE 自动补全支持
  - 内置配置验证
  - 详细的文档字符串
- **使用示例**:
  ```python
  from .config_types import AgentConfig

  config = AgentConfig(
      dashscope_api_key="sk-xxx",
      deepseek_api_key="sk-xxx",
  )
  config.validate()
  ```

### 验收结果
- [x] Logger 单例模式实现
- [x] 包公共 API 定义完成
- [x] 配置验证系统实现
- [x] dataclass 配置类创建
- [x] 已合并到主分支

---

## ⏳ 阶段 3：代码质量提升（待完成）

**分支**: `feature/phase3-code-quality`

### 3.1 统一类型注解风格
- **目标**: 将所有 `Union[str, None]` 改为 `str | None`（Python 3.10+ 语法）
- **涉及文件**:
  - `problem_solver_agent/solver_client.py`
  - `problem_solver_agent/qwen_client.py`
  - `problem_solver_agent/image_grouper.py`
  - `problem_solver_agent/utils.py`
- **示例**:
  ```python
  # Before
  def encode_image_to_base64(image_path: Path) -> Union[str, None]:

  # After
  def encode_image_to_base64(image_path: Path) -> str | None:
  ```

### 3.2 修复过于宽泛的错误处理
- **目标**: 将 `except Exception:` 替换为具体异常类型
- **改进示例**:
  ```python
  # Before
  try:
      result = api_call()
  except Exception as e:
      logger.error(f"Error: {e}")

  # After
  try:
      result = api_call()
  except (APIConnectionError, APITimeoutError) as e:
      logger.error(f"Network error: {e}")
      # 重试逻辑
  except ValueError as e:
      logger.error(f"Invalid input: {e}")
  except Exception as e:
      logger.critical(f"Unexpected error: {e}", exc_info=True)
      raise
  ```

### 3.3 完善文档字符串
- **目标**: 为所有公共函数添加 Google Style 文档字符串
- **模板**:
  ```python
  def function_name(param1: type, param2: type) -> return_type:
      """
      函数功能简述（一行）。

      详细说明函数的功能和使用场景（可选）。

      Args:
          param1: 参数1的说明。
          param2: 参数2的说明。

      Returns:
          返回值类型及其含义。

      Raises:
          ValueError: 参数无效时抛出。
          RuntimeError: 运行时错误时抛出。

      Examples:
          >>> result = function_name("test", 42)
          >>> print(result)
          'output'
      """
  ```

### 3.4 为配置添加详细注释
- **文件**: `problem_solver_agent/config.py`
- **示例**:
  ```python
  # API 超时时间（秒）
  # 建议范围: 30-1200 (0.5分钟 - 20分钟)
  # 对于复杂推理任务，可能需要更长的超时时间
  API_TIMEOUT = 600.0

  # 最大重试次数
  # 网络错误时的自动重试次数
  # 建议: 3-5 次（避免无限重试）
  MAX_RETRIES = 3

  # 分组超时时间（秒）
  # 当用户在 GROUP_TIMEOUT 秒内没有新截图时，
  # 将当前收集的图片作为一个完整任务提交
  # 建议: 5-15 秒（根据用户操作习惯调整）
  GROUP_TIMEOUT = 8.0
  ```

### 实施步骤
```bash
# 1. 创建分支
git checkout -b feature/phase3-code-quality

# 2. 统一类型注解
# 手动修改或使用 AST 工具批量替换

# 3. 修复错误处理
# 逐个文件检查和修改

# 4. 完善文档字符串
# 为所有公共函数添加文档

# 5. 添加配置注释
# 为魔法数字添加说明

# 6. 提交代码
git add -A
git commit -m "Phase 3: Code Quality Improvements"

# 7. 合并到主分支
git checkout main
git merge feature/phase3-code-quality --no-edit
```

---

## ⏳ 阶段 4：测试覆盖（待完成）

**分支**: `feature/phase4-testing`

### 4.1 创建测试框架
- **目录结构**:
  ```
  tests/
  ├── __init__.py
  ├── conftest.py              # pytest 配置和 fixtures
  ├── test_utils.py            # 工具函数测试
  ├── test_config.py           # 配置测试
  ├── test_qwen_client.py      # Qwen 客户端测试（mock）
  ├── test_solver_client.py    # 求解器客户端测试（mock）
  └── fixtures/               # 测试数据
      ├── test_images/
      └── test_prompts/
  ```

### 4.2 编写工具函数测试
- **文件**: `tests/test_utils.py`
- **测试内容**:
  ```python
  import pytest
  from pathlib import Path
  from problem_solver_agent.utils import (
      sanitize_filename,
      extract_question_numbers,
      format_number_prefix,
  )

  def test_sanitize_filename():
      """测试文件名清理功能。"""
      assert sanitize_filename("test/file?.txt") == "testfile.txt"
      assert sanitize_filename("valid-name.txt") == "valid-name.txt"

  def test_extract_question_numbers():
      """测试题号提取功能。"""
      text = "1. 第一题\n2. 第二题\n3. 第三题"
      numbers = extract_question_numbers(text)
      assert numbers == [1, 2, 3]

  @pytest.mark.parametrize("numbers,expected", [
      ([16], "16"),
      ([16, 17, 18], "16-18"),
      ([1, 2, 5], "1,2,5"),
  ])
  def test_format_number_prefix(numbers, expected):
      """测试题号前缀格式化。"""
      assert format_number_prefix(numbers) == expected
  ```

### 4.3 编写配置测试
- **文件**: `tests/test_config.py`
- **测试内容**:
  ```python
  import pytest
  from problem_solver_agent.config import validate_config
  from problem_solver_agent.config_types import AgentConfig, SolverConfig

  def test_validate_config_success():
      """测试配置验证成功场景。"""
      # 设置临时环境变量
      os.environ['DASHSCOPE_API_KEY'] = 'test-key'
      # 调用验证函数
      validate_config()  # 不应抛出异常

  def test_validate_config_missing_api_key():
      """测试 API 密钥缺失时的错误处理。"""
      with pytest.raises(ValueError, match="API密钥未设置"):
          validate_config()

  def test_solver_config_validation():
      """测试求解器配置验证。"""
      config = SolverConfig(model="test-model", base_url="https://api.test.com")
      assert config.model == "test-model"
      assert config.base_url == "https://api.test.com"

  def test_solver_config_invalid():
      """测试无效求解器配置。"""
      with pytest.raises(ValueError):
          SolverConfig(model="", base_url="https://api.test.com")
  ```

### 4.4 编写集成测试
- **文件**: `tests/integration/test_pipeline.py`
- **测试内容**:
  ```python
  import pytest
  from pathlib import Path
  from unittest.mock import Mock, patch
  from problem_solver_agent.image_grouper import ImageGrouper

  @pytest.mark.integration
  def test_full_pipeline_with_mock():
      """测试完整的图片处理流程（使用 mock API）。"""
      # 创建临时测试图片
      test_images = [Path("fixtures/test_images/test1.png")]

      # 创建 ImageGrouper
      grouper = ImageGrouper(num_workers=1)

      # Mock API 调用
      with patch('problem_solver_agent.qwen_client._call_qwen_api') as mock_api:
          mock_api.return_value = "Test transcription"

          # 提交任务
          grouper.task_queue.put(test_images)

          # 等待任务完成
          grouper.task_queue.join()

      # 验证结果
      assert mock_api.called
      # 检查是否生成了解答文件
  ```

### 4.5 配置 pytest
- **文件**: `tests/conftest.py`
- **内容**:
  ```python
  import pytest
  from pathlib import Path

  @pytest.fixture
  def sample_image_path(tmp_path):
      """创建测试用图片文件的 fixture。"""
      from PIL import Image
      image_path = tmp_path / "test.png"
      Image.new('RGB', (100, 100), color='red').save(image_path)
      return image_path

  @pytest.fixture
  def mock_config(monkeypatch):
      """Mock 配置的 fixture。"""
      monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
      monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
      monkeypatch.setenv("ZHIPU_API_KEY", "test-key")
  ```

### 实施步骤
```bash
# 1. 安装测试依赖
pip install pytest pytest-cov pytest-mock pytest-asyncio

# 2. 创建分支
git checkout -b feature/phase4-testing

# 3. 创建测试目录结构
mkdir -p tests/fixtures/test_images
touch tests/__init__.py

# 4. 编写测试文件
# 按上述内容创建测试文件

# 5. 运行测试
pytest tests/ -v --cov=problem_solver_agent

# 6. 提交代码
git add tests/
git commit -m "Phase 4: Add comprehensive test suite"

# 7. 合并到主分支
git checkout main
git merge feature/phase4-testing --no-edit
```

---

## ⏳ 阶段 5：性能优化（待完成）

**分支**: `feature/phase5-performance`

### 5.1 异步 OCR 并发处理
- **文件**: `problem_solver_agent/qwen_client.py`
- **改进**: 使用 `asyncio` 替代 `ThreadPoolExecutor`
- **示例**:
  ```python
  import asyncio
  from typing import List

  async def transcribe_images_async(image_paths: List[Path]) -> List[str]:
      """异步并行转录多张图片。"""
      async def transcribe_single(img: Path) -> str:
          # 异步调用 API
          return await _call_qwen_api_async([img], "...")

      # 并发执行
      tasks = [transcribe_single(img) for img in image_paths]
      results = await asyncio.gather(*tasks, return_exceptions=True)

      # 处理结果
      return [r if not isinstance(r, Exception) else "" for r in results]
  ```

### 5.2 实现 OCR 结果缓存
- **文件**: `problem_solver_agent/qwen_client.py`
- **改进**: 使用 `functools.lru_cache` 缓存 OCR 结果
- **示例**:
  ```python
  from functools import lru_cache
  import hashlib

  def get_image_hash(image_path: Path) -> str:
      """计算图片文件的哈希值。"""
      return hashlib.md5(image_path.read_bytes()).hexdigest()

  @lru_cache(maxsize=128)
  def transcribe_with_cache(image_hash: str, image_path: Path) -> str:
      """带缓存的 OCR 函数。"""
      return _call_qwen_api([image_path], "...")

  # 使用
  def transcribe_images(image_paths: List[Path]) -> List[str]:
      results = []
      for img in image_paths:
          img_hash = get_image_hash(img)
          result = transcribe_with_cache(img_hash, img)
          results.append(result)
      return results
  ```

### 5.3 优化流式处理
- **文件**: `problem_solver_agent/solver_client.py`, `problem_solver_agent/image_grouper.py`
- **改进**: 直接写入文件，避免内存堆积
- **示例**:
  ```python
  def stream_to_file(response_stream: Generator, file_path: Path) -> None:
      """将流式响应直接写入文件，避免内存堆积。"""
      with open(file_path, 'a', encoding='utf-8') as f:
          for chunk in response_stream:
              f.write(chunk)
              f.flush()  # 立即刷新到磁盘
  ```

### 实施步骤
```bash
# 1. 创建分支
git checkout -b feature/phase5-performance

# 2. 实现异步 OCR
# 修改 qwen_client.py

# 3. 实现缓存机制
# 添加 LRU Cache

# 4. 优化流式处理
# 修改流式写入逻辑

# 5. 性能测试
# 对比优化前后的性能差异

# 6. 提交代码
git add -A
git commit -m "Phase 5: Performance optimizations"

# 7. 合并到主分支
git checkout main
git merge feature/phase5-performance --no-edit
```

---

## ⏳ 阶段 6：开发体验优化（待完成）

**分支**: `feature/phase6-dev-experience`

### 6.1 添加 Linting 配置

#### pyproject.toml
```toml
[tool.black]
line-length = 100
target-version = ['py310']
include = '\.pyi?$'
exclude = '''
/(
    \.git
  | \.venv
  | build
  | dist
)/
'''

[tool.isort]
profile = "black"
line_length = 100
known_first_party = ["problem_solver_agent"]

[tool.mypy]
python_version = "3.14"
strict = true
warn_return_any = true
warn_unused_configs = true
exclude = '''
/(
    \.venv
  | build
  | dist
)/
'''

[[tool.mypy.overrides]]
module = "tests.*"
strict = false

[tool.pytest.ini_options]
minversion = "7.0"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
```

#### .flake8
```ini
[flake8]
max-line-length = 100
exclude =
    .git,
    __pycache__,
    .venv,
    build,
    dist,
    venv,
    env,
    ENV
max-complexity = 10
ignore =
    E203,  # whitespace before ':'
    W503,  # line break before binary operator
```

### 6.2 配置 Pre-commit Hooks

#### .pre-commit-config.yaml
```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.4.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: check-merge-conflict
      - id: check-toml
      - id: check-json

  - repo: https://github.com/psf/black
    rev: 23.12.1
    hooks:
      - id: black
        language_version: python3.14

  - repo: https://github.com/pycqa/isort
    rev: 5.13.2
    hooks:
      - id: isort
        args: ["--profile", "black"]

  - repo: https://github.com/pycqa/flake8
    rev: 7.0.0
    hooks:
      - id: flake8

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.8.0
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
        args: [--ignore-missing-imports]
```

#### 安装 Pre-commit
```bash
pip install pre-commit
pre-commit install
```

### 6.3 更新项目文档

#### 更新 README.md
- 添加详细的安装说明
- 添加配置示例
- 添加故障排查指南
- 添加贡献指南

#### 创建 docs/ 目录结构
```
docs/
├── README.md           # 文档索引
├── installation.md     # 安装指南
├── configuration.md    # 配置说明
├── architecture.md    # 架构设计
├── api.md            # API 文档
├── troubleshooting.md # 故障排查
└── contributing.md    # 贡献指南
```

### 实施步骤
```bash
# 1. 创建分支
git checkout -b feature/phase6-dev-experience

# 2. 创建 pyproject.toml
# 添加上述配置

# 3. 创建 .pre-commit-config.yaml
# 添加 pre-commit 配置

# 4. 安装 pre-commit
pip install pre-commit
pre-commit install

# 5. 运行 pre-commit 检查
pre-commit run --all-files

# 6. 更新文档
# 完善 README.md，创建 docs/ 目录

# 7. 提交代码
git add -A
git commit -m "Phase 6: Development experience improvements"

# 8. 合并到主分支
git checkout main
git merge feature/phase6-dev-experience --no-edit
```

---

## 🎯 最终步骤

### 合并所有分支
```bash
# 确保在主分支
git checkout main

# 确认所有阶段分支已合并
git branch --merged

# 推送到远程（如果需要）
git push origin main
```

### 运行完整测试
```bash
# 安装所有依赖
pip install -r requirements.txt

# 运行测试套件
pytest tests/ -v --cov=problem_solver_agent --cov-report=html

# 运行 Linting
flake8 problem_solver_agent/
black --check problem_solver_agent/
isort --check-only problem_solver_agent/
mypy problem_solver_agent/

# 运行 Pre-commit 检查
pre-commit run --all-files
```

### 创建优化报告
```bash
# 生成测试覆盖率报告
pytest tests/ --cov=problem_solver_agent --cov-report=term-missing

# 生成代码复杂度报告
radon cc problem_solver_agent/ -a

# 记录优化成果
```

---

## 📊 优化成果总结

### 已完成（阶段 1-2）
- ✅ 修复了关键语法错误
- ✅ 更新了完整的依赖清单
- ✅ 改进了模块导入方式
- ✅ 实现了 Logger 单例模式
- ✅ 创建了配置验证系统
- ✅ 定义了包的公共 API
- ✅ 创建了类型安全的配置类

### 待完成（阶段 3-6）
- ⏳ 统一类型注解风格
- ⏳ 修复错误处理逻辑
- ⏳ 完善文档字符串
- ⏳ 添加配置注释
- ⏳ 建立测试框架
- ⏳ 实现性能优化
- ⏳ 配置开发工具

### 预期收益
- 🚀 **代码质量**: 类型安全、文档完善、风格统一
- 🛡️ **稳定性**: 配置验证、错误处理完善、测试覆盖
- ⚡ **性能**: 异步处理、缓存机制、内存优化
- 👨‍💻 **开发体验**: Linting 工具、Pre-commit、文档完善

---

## 📝 工作流程建议

### 日常开发流程
```bash
# 1. 从主分支创建功能分支
git checkout main
git checkout -b feature/your-feature-name

# 2. 开发和测试
# 编写代码
# 运行 pre-commit hooks
# 运行测试

# 3. 提交代码
git add .
git commit -m "feat: description"

# 4. 推送到远程
git push origin feature/your-feature-name

# 5. 创建 Pull Request
# 在 GitHub 上创建 PR
```

### 持续集成（CI）建议
在 `.github/workflows/ci.yml` 中配置：
- 自动运行测试
- 代码覆盖率检查
- Linting 检查
- 类型检查

---

## 📚 参考资料

### Python 最佳实践
- [PEP 8 - Style Guide for Python Code](https://peps.python.org/pep-0008/)
- [PEP 484 - Type Hints](https://peps.python.org/pep-0484/)
- [The Zen of Python](https://peps.python.org/pep-0020/)

### 测试框架
- [pytest Documentation](https://docs.pytest.org/)
- [pytest-mock](https://pytest-mock.readthedocs.io/)

### 代码质量工具
- [Black Code Formatter](https://black.readthedocs.io/)
- [isort - Import sorting](https://pycqa.github.io/isort/)
- [flake8 - Linting](https://flake8.pycqa.org/)
- [mypy - Type checking](https://mypy.readthedocs.io/)

---

## 🤝 贡献指南

如果您想为项目做出贡献，请：

1. 阅读本文档了解项目结构
2. 遵循代码风格指南
3. 为新功能编写测试
4. 更新相关文档
5. 提交 Pull Request

---

## 📞 联系方式

- **作者**: WZW
- **邮箱**: your.email@example.com
- **项目地址**: [GitHub Repository](https://github.com/your-username/OnlineTest)

---

**最后更新**: 2026-01-07
**文档版本**: 1.0.0
