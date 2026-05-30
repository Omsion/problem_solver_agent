# **自动化多图解题Agent**

**实时监控 → 智能分组 → 多模型协同，将连续截图自动转化为结构化 AI 解答。**

当一道复杂题目（数学、逻辑、编程挑战）内容过长无法容纳于单张截图时，本 Agent 自动将连续截取的多张图片合并为单个任务，驱动完整流水线——从问题分类、OCR 识别、内容整合，到调用推理模型求解，最终生成结构化的 Markdown 解答文档。

---

## **核心功能**

- **📂 实时文件监控**: `watchdog` 库低延迟监控截图目录，新图片到达即刻触发。

- **🧠 智能图片分组**: 基于 `threading.Timer` 的时间窗口分组。自动判断用户是否完成连续截图，将短时间内的一系列图片归为同一组。

- **🚀 高并发处理**: 经典的"生产者-消费者"架构。文件监控器为生产者，多个后台工作线程为消费者并行处理，高负载下依然流畅。

- **🤖 多模型协同流水线**:

  | 环节 | 模型 | 用途 |
  |---|---|---|
  | 视觉分类 | `GLM-4.6V-FlashX` | 识别题型（选择/填空/编程/视觉推理等） |
  | OCR 转录 | `GLM-4.6V-FlashX` | 并行提取图片中文字、表格、公式 |
  | 文本润色 | `deepseek-v4-flash` | 合并多张图 OCR 结果、去重、修正 |
  | 视觉推理 | `GLM-4.6V` | 图形/规律类问题专项求解 |
  | 编程求解 | `deepseek-v4-pro` | LeetCode / ACM / ML 编程题（思考模式） |
  | 通用求解 | `deepseek-v4-pro` | 选择/填空/问答题（思考模式） |
  | 文件命名 | `deepseek-v4-flash` | AI 自动生成信息丰富的文件名 |

- **💪 健壮性设计**:
  - **API 自动重试**: 网络波动时自动重试（次数/间隔可配）。
  - **失败日志**: 任何步骤失败生成 `.md` 失败日志，含完整上下文。
  - **原子化操作**: 临时文件 + 重命名机制，意外中断不产生损坏文件。

- **🛠️ 辅助工具集**:
  - `silent_screencapper.py` — 全局热键静默截图（专为在线考试设计）
  - `remote_trigger.py` — 手机远程遥控截图（Web 服务 + 二维码）
  - `human_typer.py` — 模拟真人打字，将 AI 代码自然输入 IDE

---

## **项目架构**

事件驱动设计，模块高度解耦：

1. **启动**: `main.py` 初始化配置、检查 API 健康状况、启动 `ImageGrouper` 和 `FileMonitor`。
2. **监控**: `file_monitor.py` 后台线程监控截图目录，新图片立即传递给调度器。
3. **分组**: `image_grouper.py` 通过可重置定时器将连续截图归组，作为任务推入队列。
4. **处理**: 多个后台工作线程从队列取任务，执行 `_execute_pipeline`。
5. **流水线**: 分类 → OCR → 润色 → 求解 → 命名 → 归档。

---

## **文件结构**

```
OnlineTest/
│
├── .env                  # API 密钥（不入库）
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt
│
├── problem_solver_agent/ # 核心包
│   ├── __init__.py       # 包定义与公共 API
│   ├── main.py           # 主入口
│   ├── config.py         # 所有配置（模型、路径、超时等）
│   ├── file_monitor.py   # 文件系统监控
│   ├── image_grouper.py  # 核心调度器 / 流水线编排
│   ├── prompts.py        # Prompt 模板
│   ├── vision_client.py  # 视觉 API 客户端（分类/OCR/视觉推理）
│   ├── solver_client.py  # 求解器客户端（统一多模型接口）
│   └── utils.py          # 工具函数
│
└── tools/                # 独立工具
    ├── __init__.py
    ├── human_typer.py
    ├── remote_trigger.py
    └── silent_screencapper.py
```

---

## **安装与配置**

### 1. 环境准备

- Python 3.10+
- `conda` 或 `venv` 虚拟环境

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API 密钥

在项目根目录创建 `.env` 文件，填入密钥：

```env
DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
ZHIPU_API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

| 密钥 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | 求解模型（deepseek-v4-pro）+ 辅助模型（deepseek-v4-flash） |
| `ZHIPU_API_KEY` | 视觉模型（GLM-4.6V-FlashX / GLM-4.6V） |

### 4. 调整配置（可选）

所有可调参数在 `problem_solver_agent/config.py` 中：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `VISION_CLASSIFY_MODEL` | 分类 + OCR 模型 | `GLM-4.6V-FlashX` |
| `VISION_REASONING_MODEL` | 视觉推理模型 | `GLM-4.6V` |
| `AUX_MODEL_NAME` | 文本润色 + 命名 | `deepseek-v4-flash` |
| `SOLVER_ROUTING_CONFIG` | 路由规则（编程 / 默认） | 均 → `deepseek` |
| `SOLVER_CONFIG["deepseek"]["model"]` | 核心求解模型 | `deepseek-v4-pro` |
| `GROUP_TIMEOUT` | 截图分组超时（秒） | `8.0` |
| `SOLUTION_STYLE` | 编程题风格 | `OPTIMAL` / `EXPLORATORY` |

**路径自动检测**：`ROOT_DIR` 默认为项目父目录（即 `OnlineTest/` 的上级）。如需自定义，设环境变量：

```powershell
$env:SOLVER_ROOT_DIR = "D:\MyWork"
```

**切换模型只需修改 `config.py` 中的模型名称常量，无需改动业务代码。**

---

## **使用方法**

> 所有命令在项目根目录执行，并确保已激活包含依赖的虚拟环境。
> 截图 / 热键工具需**以管理员身份**运行终端。

### 启动主 Agent

```powershell
python -m problem_solver_agent.main
```

**工作流程**:
1. 启动 Agent，看到健康检查通过。
2. 遇到长题目，快速连续截图。
3. 停止截图，等待几秒——Agent 自动分组并开始处理。
4. 解答保存在 `solutions/` 目录下。

### 运行独立工具

```powershell
# 静默热键截图（默认 Alt + X）
python tools/silent_screencapper.py

# 手机远程截图（扫描终端二维码）
python tools/remote_trigger.py

# 模拟真人打字（复制代码 → 聚焦输入框 → Ctrl + V）
python tools/human_typer.py
```

---

## **模型切换指南**

本项目的 `vision_client.py` 和 `solver_client.py` 是 **provider-agnostic** 的通用客户端，切换模型只需修改 `config.py` 中的常量：

```python
# 示例：切换到其他视觉模型
VISION_CLASSIFY_MODEL = "glm-4.6v-flashx"   # 更换分类/OCR 模型
VISION_REASONING_MODEL = "glm-4.6v"         # 更换视觉推理模型

# 示例：切换到其他求解模型
SOLVER_CONFIG = {
    "deepseek": {
        "model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com/v1"
    },
    # 添加新 provider
    "new_provider": {
        "model": "new-model",
        "base_url": "https://api.new-provider.com/v1"
    }
}

# 更新路由
SOLVER_ROUTING_CONFIG = {
    "CODING_SOLVER": "new_provider",
    "DEFAULT_SOLVER": "deepseek",
}
```

**添加新 provider 到 `SOLVER_CONFIG` 后，需在 `.env` 中配置对应的 API 密钥。密钥命名遵循约定：`{PROVIDER}_API_KEY`（全大写）。例如添加 `new_provider`，则需配置 `NEW_PROVIDER_API_KEY`。**
