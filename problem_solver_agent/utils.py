"""
utils.py - 通用工具模块

存放项目中可被多处调用的辅助函数，例如：
- 日志记录器设置（单例模式）
- 图片到Base64的编码
- 清理字符串以适配为合法的文件名
- 智能题号提取与格式化
"""

import base64
import logging
import re
from pathlib import Path

# ==============================================================================
# --- Logger 单例系统 ---
# ==============================================================================

# 单例模式：Logger 只初始化一次
_logger: logging.Logger | None = None


def setup_logger() -> logging.Logger:
    """
    配置并返回一个全局日志记录器（单例模式）。

    使用单例模式确保整个应用只创建一个 Logger 实例，避免：
    1. 重复创建 Handler 导致日志重复输出
    2. 资源浪费（多个 Logger 实例）
    3. 配置不一致的问题

    Returns:
        logging.Logger: 配置好的全局 Logger 实例

    Example:
        >>> logger = setup_logger()
        >>> logger.info("Application started")
    """
    global _logger

    # 如果 Logger 已存在，直接返回
    if _logger is not None:
        return _logger

    # 创建新的 Logger 实例
    _logger = logging.getLogger("AgentLogger")
    _logger.setLevel(logging.INFO)

    # 防止日志传播到父 Logger（避免重复输出）
    _logger.propagate = False

    # 添加 Handler（只添加一次）
    if not _logger.handlers:
        console_handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(module)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        _logger.addHandler(console_handler)

    return _logger


def encode_image_to_base64(image_path: Path) -> str | None:
    """
    读取指定路径的图片文件，并将其编码为Base64字符串。
    """
    logger = setup_logger()
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except FileNotFoundError:
        logger.error(f"图片文件未找到: {image_path}")
        return None
    except Exception as e:
        logger.error(f"编码图片 '{image_path}' 时发生错误: {e}")
        return None


def sanitize_filename(filename: str) -> str:
    """
    清理字符串，移除或替换其中不适用于文件名的非法字符。
    """
    illegal_chars = r'[\\/:"*?<>|]+'
    return re.sub(illegal_chars, '', filename)


def extract_question_numbers(text: str) -> list[int]:
    """
    使用正则表达式从文本中提取所有以数字开头，并后跟点、顿号或空格的题号。
    """
    # 正则表达式:
    # ^\s*       - 匹配一行的开始，后面可以有任意空格
    # (\d+)      - 捕获一个或多个数字（这是我们的题号）
    # [.\s、]    - 匹配一个点、一个空格或一个中文顿号
    # re.MULTILINE - 使 `^` 能够匹配每一行的开头
    matches = re.findall(r'^\s*(\d+)[.\s、]', text, re.MULTILINE)
    # 转换为整数，去重，并排序
    if not matches:
        return []
    return sorted(list(set(map(int, matches))))


# 题号前缀格式化函数
def format_number_prefix(numbers: list[int]) -> str:
    """
    根据提取出的题号列表，智能生成文件名所需的前缀。
    - 单个题号: "16"
    - 连续题号: "16-20"
    - 不连续题号: "15,16,19"
    """
    if not numbers:
        return ""
    if len(numbers) == 1:
        return str(numbers[0])

    # 检查是否连续
    is_consecutive = (numbers[-1] - numbers[0] == len(numbers) - 1)

    if is_consecutive:
        return f"{numbers[0]}-{numbers[-1]}"
    else:
        return ",".join(map(str, numbers))
