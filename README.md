# **自动化多图解题Agent (Automated Multi-Image Problem Solver Agent)**

**一个通过实时监控、智能分组与多模型协同，将连续截图自动转化为结构化AI解答的强大生产力工具。**

本项目是一个功能完备的自动化AI代理，旨在解决一个核心痛点：当一个复杂问题（如数学题、逻辑题、编程挑战）因内容过长而无法容纳于单张屏幕截图时，如何高效地将其提交给多模态AI模型进行分析和解答。

该Agent通过实时监控截图文件夹，利用基于时间的智能分组策略，将用户连续截取的多张图片自动合并为单个任务，并驱动一个完整的工作流，从问题分类、文字识别、内容整合，到调用多个大模型进行求解，最终生成结构化的Markdown解答文档。

---

## **核心功能**

*   **📂 实时文件监控**: 采用 `watchdog` 库，以极低的延迟实时监控指定文件夹（默认为系统截图目录），一旦有新图片生成，立即触发处理流程。

*   **🧠 智能图片分组**: 内置基于 `threading.Timer` 的时间窗口分组逻辑。它能智能判断用户是否已完成连续截图，并将短时间内产生的一系列图片（例如，一个长问题的第1、2、3部分）归为同一组。

*   **🚀 高并发处理**: 采用经典的“生产者-消费者”架构。文件监控器作为生产者，将分组好的任务放入线程安全的队列中；多个后台工作线程作为消费者，并行从队列中取出任务进行处理，确保了系统在高负载下依然保持流畅响应。

*   **🤖 多模型协同工作流**:
    *   **视觉分析**: 利用通义千问`Qwen-VL-Max`多模态模型进行精准的问题类型**分类**与**文字识别(OCR)**。
    *   **智能路由**: 根据问题类型（如编程题、选择题），从配置文件中动态选择最合适的**核心求解器**（如 Zhipu `glm-4.5`, Dashscope `qwen3-max`, DeepSeek `deepseek-reasoner`）。
    *   **文本润色与命名**: 调用专用的辅助模型（如 `deepseek-chat`）对OCR结果进行合并、润色，并生成信息丰富的文件名。

*   **💪 健壮性设计**:
    *   **API自动重试**: 内置了网络请求的自动重试机制，能有效应对临时的网络波动和API服务不稳定。
    *   **失败日志**: 任何步骤失败都会生成详细的 `.md` 失败日志，包含错误原因和当时的上下文信息，便于快速诊断问题。
    *   **原子化操作**: 采用临时文件和重命名机制，确保即使程序意外中断，也不会产生损坏的或不完整的解答文件。

*   **🛠️ 丰富的辅助工具**:
    *   `silent_screencapper.py`: 一个通过全局热键进行**完全静默**截图的工具，专为在线考试等限制性环境设计。
    *   `remote_trigger.py`: 一个微型Web服务器，允许您通过手机等移动设备远程遥控PC进行截图。
    *   `human_typer.py`: 一个模拟真人打字的工具，可用于将AI生成的代码以自然的方式输入到IDE中。

---

## **项目架构**

本Agent采用事件驱动的设计模式，各个模块高度解耦，协同工作。

1.  **启动**: `main.py` 作为主入口，负责初始化所有配置、检查API健康状况，并启动 `ImageGrouper` 和 `FileMonitor`。
2.  **监控与生产**: `file_monitor.py` 在后台线程中监控截图目录。当新图片产生时，它会立即将图片路径传递给 `image_grouper.py` 的 `add_image` 方法。
3.  **分组与调度**: `image_grouper.py` 是项目的“调度核心”。
    - 它维护一个当前图片组 `current_group` 和一个定时器 `timer`。
    - 每当 `add_image` 被调用，它就将图片加入组中，并**重置**一个（例如8秒的）定时器。
    - 如果在定时器到期前没有新图片加入，定时器触发 `_submit_group_to_queue` 方法，将收集到的完整图片组作为一个“任务”放入全局的 `task_queue` 中。
4.  **消费与处理**: `ImageGrouper` 在初始化时会创建多个后台工作线程（消费者），它们持续阻塞等待 `task_queue` 中的新任务。
    - 一旦获得任务（一个图片路径列表），工作线程便开始执行 `_execute_pipeline` 方法。
5.  **执行流水线**: `_execute_pipeline` 是一个定义清晰的处理流程：
    - **步骤1 (分类)**: 调用 `qwen_client.py` 对图片组进行内容分类。
    - **步骤2 (文本化)**: 再次调用 `qwen_client.py` 对每张图片进行OCR，然后调用 `solver_client.py` (使用辅助模型) 将分散的文本合并、润色成通顺的最终问题描述。
    - **步骤3 (求解)**: 根据分类结果和 `config.py` 中的路由规则，调用 `solver_client.py` 将问题发送给最合适的LLM（如 `glm-4.5`）进行流式求解。
    - **步骤4 (保存)**: 将求解结果写入一个临时的 `.md` 文件。
    - **步骤5 (命名)**: 调用 `solver_client.py` (使用辅助模型) 根据问题内容生成文件名，然后将临时文件重命名。
    - **步骤6 (归档)**: 将处理过的截图移动到 `processed` 文件夹，完成整个流程。

---

## **文件结构**

```
problem_solver_agent/
│
├── .env                  # 存储API密钥等敏感信息 (需手动创建)
├── config.py             # 项目主配置文件，用于调整所有参数和路径
├── main.py               # ✨ 主程序入口
│
├── file_monitor.py       # 文件系统监控模块
├── image_grouper.py      # 核心调度器：图片分组、任务队列和处理流水线
│
├── qwen_client.py        # 封装所有与通义千问视觉大模型相关的API调用
├── solver_client.py      # 封装所有与核心求解器、辅助语言模型相关的API调用
│
├── prompts.py            # 存放所有高质量、结构化的Prompt模板
├── utils.py              # 通用工具函数（日志、Base64编码、文件名清理等）
│
├── silent_screencapper.py# 辅助工具：静默热键截图
├── remote_trigger.py     # 辅助工具：网络远程截图
└── human_typer.py        # 辅助工具：模拟真人打字
```

---

## **安装与配置**

**1. 环境准备**
*   Python 3.10+
*   `conda` 或 `venv` 等虚拟环境管理工具

**2. 安装依赖**
```bash
# 建议在conda或venv虚拟环境中执行
pip install watchdog python-dotenv openai keyboard pyperclip pyautogui Pillow pywin32 qrcode Flask
```

**3. 配置API密钥**
*   在项目根目录下，创建一个名为 `.env` 的文件。
*   复制以下内容到 `.env` 文件中，并填入你自己的API密钥：
  ```
  DASHSCOPE_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  ZHIPU_API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  ```

**4. 配置 `config.py`**
*   打开 `config.py` 文件。
*   **（重要）** 修改 `ROOT_DIR` 变量，将其指向您希望存放 `Screenshots`, `processed`, `solutions` 这几个文件夹的根目录。
  ```python
  # --- 6. 核心文件路径配置 ---
  ROOT_DIR = Path(r"D:\Your\Preferred\Path") # 例如: D:\AI_Solutions
  ```
*   其他参数（如 `GROUP_TIMEOUT`, `SOLVER_ROUTING_CONFIG` 等）可根据需求自行调整。

---

## **使用方法**

**确保您已激活包含所有依赖的Python虚拟环境，并且终端具有管理员权限（特别是对于截图和热键工具）。**

### **1. 运行主Agent**

这是核心功能。启动后，它会在后台持续监控您的截图文件夹。

```bash
python main.py
```
**工作流程**:
1. 启动 `main.py`。
2. 遇到一个长问题，快速连续按下截图键（例如，使用 `silent_screencapper.py` 或系统自带的 `Win+Shift+S`）。
3. 截取完所有部分后，停止操作。
4. 等待 `GROUP_TIMEOUT`（默认8秒）后，Agent会自动开始处理。
5. 处理完成后，解答将以 `.md` 格式出现在 `solutions` 文件夹中。

---
### **2. 使用静默热键截图 (推荐)**

此工具专为需要无任何界面干扰（如闪屏）的场景设计。

```bash
python silent_screencapper.py
```
*   默认热键为 `Alt + X`（可在 `config.py` 中修改）。
*   按下热键即可截取当前屏幕，并自动保存到受监控的 `Screenshots` 文件夹。
*   **必须以管理员身份运行**，否则无法监听全局热键。

---
### **3. 使用网络远程截图**

当PC键盘被限制时，可通过此工具用手机遥控截图。

```bash
python remote_trigger.py
```
1. **以管理员身份运行**此脚本。
2. 确保您的手机和PC连接到**同一个Wi-Fi网络**。
3. 在手机浏览器中扫描终端显示的二维码，或直接访问显示的IP地址。
4. 点击手机屏幕上的“截图”按钮即可。

---
### **4. 使用模拟真人打字**

可用于将AI生成的代码自然地输入到编辑器中。

```bash
python human_typer.py
```
1. 复制您想输入的代码文本。
2. 将鼠标光标定位在目标输入框（如VS Code, PyCharm等）。
3. 按下 `Ctrl + V` 触发模拟输入。
4. 按 `Esc` 可随时退出此脚本。