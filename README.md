# **自动化多图解题Agent (Automated Multi-Image Problem Solver Agent)**

**一个通过实时监控、智能分组与多模型协同，将连续截图自动转化为结构化AI解答的强大生产力工具。**

本项目是一个功能完备的自动化AI代理，旨在解决一个核心痛点：当一个复杂问题（如数学题、逻辑题、编程挑战）因内容过长而无法容纳于单张屏幕截图时，如何高效地将其提交给多模态AI模型进行分析和解答。

该Agent通过实时监控截图文件夹，利用基于时间的智能分组策略，将用户连续截取的多张图片自动合并为单个任务，并驱动一个完整的工作流，从问题分类、文字识别、内容整合，到调用多个大模型进行求解，最终生成结构化的Markdown解答文档。

---

## **核心功能**

*   **📂 实时文件监控**: 采用 `watchdog` 库，以极低的延迟实时监控指定文件夹，一旦有新图片生成，立即触发处理流程。

*   **🧠 智能图片分组**: 内置基于 `threading.Timer` 的时间窗口分组逻辑。它能智能判断用户是否已完成连续截图，并将短时间内产生的一系列图片归为同一组。

*   **🚀 高并发处理**: 采用经典的“生产者-消费者”架构。文件监控器作为生产者，将分组好的任务放入线程安全的队列中；多个后台工作线程作为消费者，并行从队列中取出任务进行处理，确保了系统在高负载下依然保持流畅响应。

*   **🤖 多模型协同工作流**:
    *   **视觉分析**: 利用通义千问`Qwen-VL-Max`多模态模型进行精准的问题类型**分类**与**文字识别(OCR)**。
    *   **智能路由**: 根据问题类型（如编程题），从配置文件中动态选择最合适的**核心求解器**（如 Zhipu `glm-4.5`, Dashscope `qwen3-max`）。
    *   **文本润色与命名**: 调用专用的辅助模型（如 `deepseek-chat`）对OCR结果进行合并、润色，并生成信息丰富的文件名。

*   **💪 健壮性设计**:
    *   **API自动重试**: 内置了网络请求的自动重试机制，能有效应对临时的网络波动和API服务不稳定。
    *   **失败日志**: 任何步骤失败都会生成详细的 `.md` 失败日志，包含错误原因和上下文信息，便于快速诊断问题。
    *   **原子化操作**: 采用临时文件和重命名机制，确保即使程序意外中断，也不会产生损坏的解答文件。

*   **🛠️ 丰富的辅助工具集**:
    *   `silent_screencapper.py`: 通过全局热键进行**完全静默**截图，专为在线考试等限制性环境设计。
    *   `remote_trigger.py`: 一个微型Web服务器，允许通过手机等移动设备远程遥控PC进行截图。
    *   `human_typer.py`: 一个模拟真人打字的工具，可用于将AI生成的代码以自然的方式输入到IDE中。

---

## **项目架构**

本Agent采用事件驱动的设计模式，各个模块高度解耦，协同工作。其核心流程如下：

1.  **启动**: `main.py` 作为主入口，负责初始化所有配置、检查API健康状况，并启动 `ImageGrouper` 和 `FileMonitor`。
2.  **监控与生产**: `file_monitor.py` 在后台线程中监控截图目录。当新图片产生时，它会立即将图片路径传递给 `image_grouper.py` 的 `add_image` 方法。
3.  **分组与调度**: `image_grouper.py` 是项目的“调度核心”。它通过一个可重置的定时器，将短时间内连续产生的截图归为一组，然后作为一个“任务”放入全局的 `task_queue` 中。
4.  **消费与处理**: 多个后台工作线程（消费者）持续等待 `task_queue` 中的新任务。一旦获得任务，便开始执行 `_execute_pipeline` 方法。
5.  **执行流水线**: `_execute_pipeline` 是一个包含问题分类、文本化、多模型求解、结果保存、智能命名和文件归档的完整处理流程。

---

## **文件结构**

```
OnlineTest/
│
├── .env                  # 存储API密钥等敏感信息
├── .gitignore            # Git忽略配置
├── LICENSE               # 项目许可证
├── README.md             # ✨ 项目主文档
├── requirements.txt      # 项目依赖
│
├── problem_solver_agent/ # 核心源码包
│   ├── __init__.py       # 将此目录标记为Python包
│   ├── main.py           # 主程序入口
│   ├── config.py         # 项目配置
│   ├── file_monitor.py   # 文件监控模块
│   ├── image_grouper.py  # 核心调度器
│   ├── prompts.py        # Prompt模板
│   ├── qwen_client.py    # Qwen-VL客户端
│   ├── solver_client.py  # 统一求解器客户端
│   └── utils.py          # 通用工具函数
│
├── scripts/              # 开发与辅助脚本
│   └── aggregate_for_gemini.py
│
└── tools/                # 独立工具集
    ├── __init__.py       # 将此目录标记为Python包
    ├── human_typer.py    # 模拟打字工具
    ├── remote_trigger.py # 远程截图工具
    └── silent_screencapper.py # 静默截图工具
```

---

## **安装与配置**

**1. 环境准备**
*   Python 3.10+
*   `conda` 或 `venv` 等虚拟环境管理工具

**2. 安装依赖**
```bash
# 建议在conda或venv虚拟环境中执行
pip install -r requirements.txt
```

**3. 配置API密钥**
*   在项目根目录下 (`OnlineTest/`)，创建一个名为 `.env` 的文件。
*   复制以下内容到 `.env` 文件中，并填入你自己的API密钥：
  ```
  DASHSCOPE_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  ZHIPU_API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  ```

**4. 配置 `config.py`**
*   打开 `problem_solver_agent/config.py` 文件。
*   **（重要）** 修改 `ROOT_DIR` 变量，将其指向您希望存放 `Screenshots`, `processed`, `solutions` 这几个文件夹的根目录。
  ```python
  # --- 6. 核心文件路径配置 ---
  ROOT_DIR = Path(r"D:\Your\Preferred\Path") # 例如: D:\AI_Solutions
  ```
*   其他参数（如 `GROUP_TIMEOUT`, `SOLVER_ROUTING_CONFIG` 等）可根据需求自行调整。

---

## **使用方法**

**⚠️ 重要提示**: 
*   所有命令都应在**项目根目录** (`D:\Users\wzw\Pictures\OnlineTest`) 下的终端中执行。
*   请确保您已激活包含所有依赖的Python虚拟环境（如 `conda activate llm`）。
*   运行截图和热键相关的工具时，请**以管理员身份**运行您的终端。

### **1. 启动主Agent**

这是核心功能。启动后，它会在后台持续监控您的截图文件夹。

```powershell
# 在 PowerShell 中运行
python -m problem_solver_agent.main
```
**工作流程**:
1. 启动上述命令。
2. 遇到一个长问题，快速连续按下截图键。
3. 截取完所有部分后，停止操作。
4. 等待片刻，Agent会自动开始处理，并将最终解答保存在 `solutions` 文件夹中。

### **2. 运行独立工具**

这些工具可以与主Agent同时运行，也可以独立使用。

*   **静默热键截图 (`silent_screencapper.py`)**:
    ```powershell
    python tools/silent_screencapper.py
    ```
    *(默认热键为 `Alt + X`)*

*   **网络远程截图 (`remote_trigger.py`)**:
    ```powershell
    python tools/remote_trigger.py
    ```
    *(启动后用手机扫描终端中的二维码即可访问)*

*   **模拟真人打字 (`human_typer.py`)**:
    ```powershell
    python tools/human_typer.py
    ```
    *(复制文本，将光标置于输入框，按 `Ctrl + V` 触发)*