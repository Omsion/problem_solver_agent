# 使用说明（User Guide）

> **一句话**：手机拍题（或电脑热键截图）→ 图片落到电脑的一个文件夹 → 程序自动识别并求解 → 在**手机文件管理器**或 Markdown 文件里看答案；网页端留给事后复盘。
>
> 本文档面向两类读者：**刚拿到代码的用户**，以及**几个月后忘了怎么用的自己**。
> 想了解内部实现看 `docs/ARCHITECTURE.md`；接口细节看 `docs/API.md`。

**两条入口、两种定位（先记住这个）：**

| 入口 | 定位 | 什么时候用 |
|---|---|---|
| **命令行模式** `python -m problem_solver_agent.main` | **日常主力**：安静地盯文件夹、出答案文件 | 考试/刷题时——不想开浏览器、只想拍完就看答案 |
| **网页模式** `start_web.bat` / `run_web.py` | **复盘工具**：历史任务、重新求解、核对答案、手机上远程看进度 | 事后回看、想换风格再解一遍、想核对答案时 |

---

## 0. 30 秒速览：三种典型用法

| 你想怎么用 | 启动什么 | 图片怎么进来 | 答案在哪 |
|---|---|---|---|
| **A. 手机拍完自动出答案**（日常主力） | `python -m problem_solver_agent.main` | 手机 Syncthing 自动同步到 `Screenshots` | `<工作根目录>/solutions/*.md`，**手机 SMB 直接看**（见 1.3） |
| **B. 电脑上热键截图**（在电脑上做题时） | 同上 + `python tools/silent_screencapper.py` | `Alt+X` 静默截图，直接落到 `Screenshots` | 同上 |
| **C. 事后复盘 / 手动传图** | `start_web.bat`（网页模式） | 网页拖拽上传、或自动导入 | 网页「解答」面板；文件在 `webapp/solutions/` |

> **工作根目录**默认是**项目的父目录**。本项目在 `D:\Users\wzw\Pictures\OnlineTest`，所以工作根目录是 `D:\Users\wzw\Pictures`，监控目录就是 `D:\Users\wzw\Pictures\Screenshots`。
> 想改就设 `SOLVER_ROOT_DIR`；**启动日志第一条就会打印四个实际路径**。
>
> 需要"手机看答案 → 电脑自动打进去"的完整链路（考试客户端场景），见 **1.5 节**。

---

## 1. 主流程：命令行模式（推荐日常使用）⭐

### 1.1 启动

```powershell
conda activate llm
cd D:\Users\wzw\Pictures\OnlineTest
python -m problem_solver_agent.main
```

启动日志长这样（关键行已标注）：

```
✓ 视觉层密钥配置正常（provider=deepseek，模型=deepseek-flash，思考=关闭）
✓ 核心求解器 'deepseek' API连接测试通过
监控目录: D:\Users\wzw\Pictures\Screenshots        ← 图片放这里
OCR 归档目录: D:\Users\wzw\Pictures\ocr            ← 逐页原始 OCR 落这里
视觉层: provider=deepseek 模型=deepseek-flash（回退：zhipu=GLM-4.6V-FlashX）
视觉推理模型: deepseek-flash
辅助模型: deepseek -> deepseek-flash
文件监控已启动，正在监视目录: ...                   ← 监控就绪，可以开始拍/截图了
```

停止：在该窗口按 **Ctrl+C**。重启后它会自动补上上次没处理完的照片（启动补偿扫描）。

### 1.2 图片怎么进来（4 种方式，可以混用）

#### 方式 A：手机拍照 → 自动同步（主要方式）

手机拍好，Syncthing 自动把照片同步到 `Screenshots`，程序自动接手。配置见 **第 4 节**。

- 同一道题请**连着拍完**（每来一张图，8 秒计时器会重置；停 8 秒才提交这一组）。
- **不必按顺序传**：程序会按**拍摄时间**把一组图重排成"第 1 页 → 第 N 页"（见第 6 节）。

#### 方式 B：电脑端热键静默截图（在电脑上做题时）

```powershell
# 需要管理员权限的终端（键盘钩子 + GDI 抓屏）
conda activate llm
cd D:\Users\wzw\Pictures\OnlineTest
python tools/silent_screencapper.py
```

- 热键：**`Alt+X`**（可在脚本里改 `HOTKEY_STRING`），**无闪屏**，用 GDI 直接抓屏；
- 截图保存到**监控目录**，文件名形如 `Screenshot_20260913-164123.png`；
- 一道题连按几次 `Alt+X`（间隔 < 8 秒）→ 自动归为一组，且**顺序按文件时间自动排好**；
- 脚本启动时会打印"截图成功! 已保存至: ..."，用来确认它工作正常。

> 若在考试客户端里 `Alt+X` 也被禁（键盘钩子被拦），改用下面的方式 C。

#### 方式 C：手机遥控截图（绕开电脑端的键盘限制）

```powershell
python tools/remote_trigger.py      # 默认端口 5555（可在 .env 里设 REMOTE_TRIGGER_PORT）
```

- 启动后按提示在手机上打开/扫码那个地址，页面上有按钮 → 点一下，**电脑端截图**落到监控目录；
- 手机与电脑需在同一局域网；
- 这是备用方案，日常优先用 `Alt+X`。

#### 方式 D：需要"模拟真人打字"时（把答案/代码输进去）

```powershell
# 需要管理员权限
python tools/human_typer.py
```

- 启动后它**接管 `Ctrl+V`**：先把要输入的内容复制到剪贴板，然后在目标窗口（例如考试网页的代码框）按 **Ctrl+V**，它就会像真人一样**逐字打进去**（可制造轻微错误/停顿，更像人工）；
- 顶部开关可调：`PERFECT_CODE_MODE`（精确粘贴还是带错模拟）、`STRIP_COMMENTS_MODE` / `STRIP_DOCSTRINGS_MODE`（自动剥掉注释/文档字符串）、`HIDE_MOUSE_CURSOR`、`ERROR_RATE`、打字速度；
- ⚠️ 因为 `Ctrl+V` 被占用，**运行期间无法正常粘贴**；不想要了就 `Ctrl+C` 退出（会自动恢复鼠标光标与键盘钩子）。
- 与手机联动的完整链路（手机看答案 → 跨设备剪贴板 → 电脑自动打字）见 **1.5 节**。

### 1.3 手机上看答案：把 `solutions` 用 SMB（Samba）共享出去

答案生成后是 Markdown 文件，**不需要网页也能在手机上看**——把电脑的解答目录做成 Windows 共享，手机用文件管理器直接打开。

**电脑端（一次配置，长期有效）**

1. 找到解答目录：`D:\Users\wzw\Pictures\solutions`（即 `<工作根目录>\solutions`）；
2. 右键该文件夹 → **属性 → 共享 → 高级共享** → 勾选「共享此文件夹」；
   - 共享名保持 `solutions`；
   - 「权限」里给 `Everyone` **读取**（想从手机往里放东西就给「更改」）；
3. 「安全」标签里确认当前用户有读取权限；
4. 记下访问地址：`\\<电脑名>\solutions`，以及电脑的局域网 IP（`python tools/diag.py` 会打印，形如 `192.168.1.23`）；
5. 控制面板 → Windows Defender 防火墙 → 允许应用通过防火墙 → 勾选 **文件和打印机共享**。

**手机端**

1. 打开手机「文件管理」→ 找 **网络邻居 / 局域网 / 远程管理**（vivo/小米/华为自带文件管理器都有）；
2. 添加 SMB/共享：服务器填 `192.168.1.23`（电脑 IP），共享名 `solutions`，输入电脑的 **Windows 账号 + 密码**（用微软账户登录 Windows 的，用户名填邮箱）；
3. 打开后就能看到 `22_xxx.md` 等解答，点开即可阅读（支持 Markdown 的阅读器更好看）。

**顺带一个好处：手机也能"喂图"**
把 `Screenshots` 也共享出去（权限给「更改」），手机上就能直接从相册**复制照片**到 `Screenshots` —— 效果等同于 Syncthing，命令行 Agent 会立刻处理。不想装 Syncthing 时，这条路更简单（代价是要手动复制）。

| SMB 常见问题 | 处理 |
|---|---|
| 手机搜不到电脑 | 确认同一局域网；开启「网络发现」；防火墙放行「文件和打印机共享」 |
| 提示无权限 / 密码错误 | 用 Windows 账号密码；微软账户填邮箱；共享权限别只给"读取"（要写入时） |
| 能读不能写 | 「权限」里把 Everyone 改成「更改」 |
| 手机文件管理器没有 SMB | 装 **Cx File Explorer** / Solid Explorer 等支持 SMB 的 App |

### 1.4 停止、重开与产物

- `Ctrl+C` 停止；重新运行即可（会自动补投漏掉的照片）。
- 解答：`<工作根目录>\solutions\*.md`；原图处理完会**移动**到 `<工作根目录>\processed\`。
- 更详细的产物位置见 **第 7 节**。

### 1.5 完整闭环：手机看答案 → 跨设备剪贴板 → 电脑自动打字 ⭐

这是"手机拍题 → 电脑交卷"的完整链路，适合在**考试客户端里作答**：题目在手机上拍，答案在手机上看，最后把代码"假装手打"进考试客户端的输入框。

```
手机拍照 ──Syncthing──▶ Screenshots ──Agent──▶ solutions/*.md
                                                  │
                                     手机 SMB 打开 .md，复制代码
                                                  │
                                   跨设备剪贴板（手机助手/多屏协同）
                                                  ▼
                                     电脑剪贴板里就有这段代码了
                                                  │
                            把鼠标停在考试客户端的输入框上 → 按 Ctrl+V
                                                  ▼
                            tools/human_typer.py 逐字"打"进去（光标隐藏）
```

**步骤**

1. **电脑端开一个手机品牌的桌面助手，并打开"跨设备剪贴板/剪贴板共享"**：
   华为「电脑管家 / 多屏协同」、小米「互联服务 / 妙享」、vivo「互传」、OPPO「跨屏互联」、三星「Samsung Flow」等都有这个能力。
   *没有这类助手时的替代*：用微信/QQ 的「文件传输助手」把代码从手机发到电脑，再在电脑上复制一次。
2. **手机上看答案**：用 SMB 打开 `solutions` 里对应的 `.md`（第 1.3 节），选中里面的代码/答案**复制**。
3. 剪贴板会同步到电脑（没有共享就在电脑上手动复制一次）。
4. **电脑端启动打字器**（需要管理员权限）：

   ```powershell
   conda activate llm
   cd D:\Users\wzw\Pictures\OnlineTest
   python tools/human_typer.py
   ```

5. **把鼠标停在考试客户端的输入框上**（脚本一开始会 `click()` 一下，把焦点交给鼠标当前位置的目标窗口）。
6. 在目标窗口按 **`Ctrl+V`** → 脚本接管，开始逐字模拟输入（期间会隐藏系统鼠标光标，结束后自动恢复）。

**⚠️ 打字期间不要动鼠标**

- **不要点击、不要切换窗口**：焦点一跑，字符就打进别的窗口了；
- **不要移动鼠标**，尤其**别移到屏幕左上角**——pyautogui 默认开启"失效保护"（鼠标进角落即中断），会把这次输入打断在半途；
- 想中途放弃就直接在打字器窗口 `Ctrl+C` 退出（会恢复鼠标光标与键盘钩子）。

**让它更像"手打"的开关**（`tools/human_typer.py` 顶部）：

| 开关 | 默认 | 作用 |
|---|---|---|
| `PERFECT_CODE_MODE` | False | True=精确粘贴式（快）；False=带轻微错误与停顿的模拟 |
| `ERROR_RATE` | 0.2 | 模拟模式的出错概率 |
| `STRIP_COMMENTS_MODE` | True | 自动剥掉 `#` 注释 |
| `STRIP_DOCSTRINGS_MODE` | True | 自动剥掉 `"""..."""` 文档字符串 |
| `HIDE_MOUSE_CURSOR` | True | 输入期间隐藏鼠标光标 |

---

## 2. 系统由哪几块组成？"后端"到底指什么？

```
                      ┌──────────────────────────────┐
   手机拍照 / 电脑截图  │  D:\...\Pictures\Screenshots │  ← 监控目录（Syncthing 同步目标）
   （按时间顺序）  ───▶ │        （一个普通文件夹）      │
                      └──────────────┬───────────────┘
                                     │ 两个"监控者"二选一，别同时开
                 ┌───────────────────┴────────────────────┐
                 ▼                                        ▼
   ① 命令行 Agent（主力）                      ② 网页服务（复盘用）
   python -m problem_solver_agent.main         python run_web.py / start_web.bat
   · 只监控文件夹                                · 提供网页 + 手动上传
   · 答案写到 solutions/*.md                     · 也监控同一个文件夹
   · 原图归档到 processed/                       · 答案写到 webapp/solutions + 数据库
                 └───────────────────┬────────────────────┘
                                     ▼
                        同一套核心流水线（core_pipeline）
        分类 + 多图合并转录（1 次请求） → 按拍摄时间拼成题干 →（合并成功则跳过润色）→ 求解
```

**"为什么网页上传后 `python -m` 没启动？"** —— 因为它本来就不该启动。这是两个平级的程序：

| 名字 | 是什么 | 干什么 |
|---|---|---|
| `python -m problem_solver_agent.main` | **命令行 Agent**，独立程序 | 盯文件夹 → 出 md 文件（不提供网页） |
| `python run_web.py`（`start_web.bat` 就是它） | **网页服务** | 提供网页 + 手动上传接口，**并且自己也会盯同一个文件夹**（`AUTO_IMPORT_ENABLED=true` 时） |

网页里上传图片，是**网页服务自己**受理的（上传 → 建任务 → 在服务端进程跑同一套流水线 → 推送到网页），不会去启动命令行 Agent。

### ⚠️ 两个同时开会怎样（重要）

两个监控者盯同一个文件夹 → **同一张照片被处理两次**（两边都调 API、都出答案）；而且命令行处理完会把原图**移走**到 `processed/`，可能让网页那边读不到图。

**结论：同一时间只开一个。**

- 日常刷题 → 只开命令行（按 1.1）；
- 想复盘/重解/核对 → 关掉命令行，改开网页模式（第 5 节）；
- 网页模式下只想手动上传、不想它自动处理文件夹 → `.env` 里设 `AUTO_IMPORT_ENABLED=false`。

---

## 3. 前置条件

### 电脑端
| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows（脚本按 Windows 写） | Linux/macOS 也能跑，命令自行替换 |
| Python | 3.10+ | `python --version` |
| Node.js | 20+ | **仅当需要重新构建前端时**（`webapp/static/` 已有产物可跳过） |
| 依赖包 | `pip install -r requirements.txt` | 已包含界面与全部小工具的依赖（`keyboard`/`pywin32`/`pyautogui`/`pyperclip`/`flask` 等） |
| API 密钥 | DeepSeek（默认唯一必填） | 视觉层默认也用 DeepSeek；智谱仅在回退时需要，见下 |

### 手机端
| 项目 | 要求 |
|---|---|
| 同步工具 | Syncthing（推荐 Syncthing-Fork，热点模式更稳）**或** 只用 SMB 手动复制照片 |
| 看答案工具 | 任意支持 SMB 的文件管理器（vivo 自带「文件管理」即可） |
| 网络 | 手机与电脑同一局域网（家里 Wi-Fi，或手机开热点让电脑连） |

### 密钥配置（必做）

```powershell
cd D:\Users\wzw\Pictures\OnlineTest
copy .env.example .env     # 第一次
notepad .env
```

```dotenv
DEEPSEEK_API_KEY=sk-xxxxxxxx        # 求解 + 辅助 + 视觉层（分类 / OCR / 视觉推理 / 核对）
# ZHIPU_API_KEY=xxxxxxxx           # 可选：仅当 VISION_PROVIDER=zhipu 回退时取消注释
```

`.env.example` 与代码默认值都是 `VISION_PROVIDER=deepseek`：分类 / OCR / 视觉推理 /
核对全部用 `deepseek-flash`，和求解共用同一个密钥与 `https://api.deepseek.com`。
**不想用 DeepSeek 做视觉**时，把 `VISION_PROVIDER` 改成 `zhipu` 并填上 `ZHIPU_API_KEY`
即可一键回退（见第 9 节）。默认值是在双 provider 的 A/B 闸门判定通过后（2026-09-21：
题目正文要素缺失 0、分类一致率 97.2%）才切过来的。

---

## 4. 手机 → 电脑：让照片自动落到 `Screenshots`（Syncthing）

> 不想装 Syncthing 的话，可以用 1.3 的 SMB 手动复制照片，效果一样（只是要手动）。

### 4.1 安装

- **电脑**：https://syncthing.net/ 下载 Windows 版，运行 `syncthing.exe`，浏览器打开 `http://localhost:8384`；
- **手机**：商店 / F-Droid 安装 **Syncthing**（推荐 **Syncthing-Fork**），允许后台运行，电池优化设为「无限制」。

### 4.2 配对设备

1. 电脑端 → 操作 → 显示 ID → 复制电脑设备 ID；
2. 手机端 → 设备 → 添加设备 → 粘贴电脑 ID；
3. 两端互相确认添加。

### 4.3 配置文件夹（方向别搞反）

| 端 | 路径 | 类型 | 共享给 |
|---|---|---|---|
| **手机** | `/storage/emulated/0/DCIM/Camera` | **仅发送 Send Only** | 电脑 |
| **电脑** | `D:\Users\wzw\Pictures\Screenshots` | **仅接收 Receive Only** | 手机 |

### 4.4 手机热点模式（没 Wi-Fi、没路由器时）

- 手机开「移动热点」，电脑连上它（手机即路由器）；
- 手机端 Syncthing 的「运行条件」勾上**移动数据**与 **Wi-Fi**；
- 电脑端发现不了手机时，在手机设备的「高级」里把地址改为 `tcp://192.168.43.1:22000`（IP 换成热点网关）；
- 防火墙放行 Syncthing（端口 `22000` TCP/UDP）；
- 局域网直传，**不消耗手机流量**。

### 4.5 验证

拍一张照片 → 电脑 `Screenshots` 里 10–30 秒内出现该文件 → Syncthing 界面显示 **TCP LAN**（不是 WAN 中继）。

### 4.6 Syncthing 故障排查

| 现象 | 处理 |
|---|---|
| 互相发现不了 | 热点是否允许多设备通信；改手动填 IP |
| 热点下不启动 | 手机端勾"运行在移动数据上" |
| 显示 WAN 中继 | 手动设地址为手机热点 IP（4.4） |
| 同步慢 | 确认没走中继；用 5GHz 热点 |
| 锁屏后停止 | 关电池优化、允许后台活动 |

> 备选 **LocalSend**（https://localsend.org/）：免配对、手动发送，适合偶尔用一次。

---

## 5. 复盘模式：网页端

> 定位：**答案出来之后的复盘**——看历史任务、换个风格重解、核对答案、在手机上远程看进度。
> 日常做题不必开它（开它就不能同时开命令行，见第 2 节）。

### 5.1 启动

```powershell
# 最省事：双击 start_web.bat（会检查 Python/.env/依赖/前端产物）
# 等价：
cd D:\Users\wzw\Pictures\OnlineTest
python run_web.py            # 默认 8000；python run_web.py 9000 换端口
```

浏览器打开 **http://localhost:8000**。开发热重载用 `python run_web_dev.py`。

### 5.2 界面导览

| 位置 | 作用 |
|---|---|
| **解题台** | 左：拖拽/选择图片上传；右：「解答」面板（流式答案、思考过程、答案卡、核对按钮） |
| **任务** | 历史任务列表（读 `webapp/data/tasks.db`），点进去看旧答案 |
| **设置** | 自动导入是否在跑、监控目录、模型与版本 |
| **手机扫码** | 显示局域网地址二维码，手机扫码即可远程查看/上传 |
| **用量 / 管理** | 仅 `AUTH_ENABLED=true` 的多用户模式有意义 |

### 5.3 两个入口都在网页里

1. **自动导入**：Syncthing 同步到 `Screenshots` → 网页右上角弹「新任务」提示 → 点「查看」（勾"自动切换"则自动跳转）；
2. **手动上传**：拖拽/选择图片 → 开始 → 立刻建任务并处理（一次最多 50 MB）。

### 5.4 复盘时常用的三个动作

- **重新求解**（「解答」面板右上角 ⟳）：
  - 用最优解风格重解 / 用讲解风格重解；
  - **开启思考模式重解**（想让它多想一会儿，慢但更稳）；
  - **关闭思考模式重解**（最快）。
- **核对答案**：用视觉推理模型（默认 `deepseek-flash`，回退时是 `GLM-4.6V`）对照原图复核，给出「通过 / 发现疑点 / 无法判定」。
- **阅读模式 / 复制全文**：长答案阅读与导出。

---

## 6. 图片顺序与分组（决定题目对不对）

### 6.1 顺序：按"拍摄时间"，不是"到达时间"

Syncthing 按数据块同步，**照片落盘顺序是随机的**（日志里就出现过 164126 → 164128 → 164123 → 164131 这种乱序）。而多图题目是「逐页 OCR → 按顺序拼成题干 → 求解」，顺序错了整道题就废了。

程序在 OCR 之前统一重排，时间来源三级回退：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | **EXIF `DateTimeOriginal`** | 相机原始拍摄时间；微信/QQ 转存改名后仍保留 |
| 2 | **文件名时间戳** | `IMG_20260913_164123.jpg`、`Screenshot_20260913-164131.png`（**热键截图就是这种**）、`屏幕截图 2025-11-20 093340.png`、`IMG-20260913-WA0001.jpg` |
| 3 | **文件 mtime** | Syncthing 保留手机端时间，通常也等于拍摄时间 |

- 时间相同保持原顺序（稳定排序）；
- **手动上传也一样**：上传保留原始字节（EXIF 在），所以网页里"全选 8 张一起传"也会自动重排；
- 所以**拍照/截图不必刻意按顺序**，按顺序拍就行。

### 6.2 分组：每来一张图，8 秒计时器就重置

```dotenv
GROUP_TIMEOUT=8      # 连续 8 秒没有新图片 → 提交这一组
```

- 连着拍/截的 4 张 → 一组 → 当成一道 4 页的题；
- 隔了一分钟再拍 → 新的一组 → 另一道题。

**建议**：同一道题连着拍完；拍完等十来秒程序就开始处理了。

### 6.3 漏检兜底（现在不怕"没检测到"）

| 变量 | 默认 | 作用 |
|---|---|---|
| `MONITOR_RESCAN_INTERVAL` | 15 | 每 15 秒扫一遍目录兜底（0=关闭） |
| `MONITOR_CATCHUP_MAX_AGE_MINUTES` | 120 | 只补投 mtime 在 120 分钟内的文件（0=不限） |
| `MONITOR_STARTUP_SCAN` | true | 启动时先扫一遍（补上上次没处理完的） |

去重账本在 `<工作根目录>/solutions/.monitor_seen.json`（**不在**同步目录里，避免同步回手机）。

### 6.4 "该图片已投递过，跳过重复事件" —— 图片被永久跳过怎么办

**症状**：日志里每一张图都是这两行，目录里图片明明在，却什么都不发生：

```
检测到新图片（新建）: IMG_xxx.jpg
该图片已投递过，跳过重复事件: IMG_xxx.jpg
```

**原因**：账本记的是**"投递过"**，不是**"解出来了"**。如果上一次运行在解题中途被
强杀（任务管理器结束进程、崩溃、直接关掉终端），图片已经进了账本、解答却没生成，
于是之后每次启动都跳过它们。2026-09-22 遇到过这个情况。

**恢复**（一条命令）：

```powershell
# 先看不改动任何东西的预演
py -3.10 -m tools.requeue --dry-run

# 确认后重投（会真的调用 API 解题）
py -3.10 -m tools.requeue                      # 重投监控目录里的全部图片
py -3.10 -m tools.requeue --pattern "IMG_20260916_19*.jpg"   # 只重投某一批
```

> 为什么不能只删账本就算了：启动扫描有**年龄闸门**
> （`MONITOR_CATCHUP_MAX_AGE_MINUTES=120`，只补投 2 小时内的文件，防止把历史截图
> 全重跑一遍），而被中断的图片往往已经放了好几天 —— 删了账本照样会被闸门挡下。
> `tools/requeue` 因此是**直接投递**，不受年龄限制。

**防止再次发生**（2026-09-22 已修）：启动时会自动清掉上一次进程残留的处理锁
（`recover_stale_locks`），且"正在处理中"的图片**不再**被记入账本 —— 被中断的任务
下次启动能自动重投，不需要手跑上面的命令。

---

## 7. 产物在哪

| 产物 | 命令行模式 | 网页模式 |
|---|---|---|
| 解答 Markdown | `<工作根目录>/solutions/*.md`（如 `D:\Users\wzw\Pictures\solutions`）← **手机 SMB 看这里** | `webapp/solutions/*.md` |
| 原始 OCR 归档 | `<工作根目录>/ocr/<日期>/<task_id>.md`（逐页转录全文 + 页数/失败页） | 同（两层入口共用同一份目录） |
| 原图归档 | `<工作根目录>/processed/`（处理完从 Screenshots **移走**） | 原图留在 `webapp/uploads/<任务ID>/`（不移动） |
| 任务数据库 | — | `webapp/data/tasks.db`（网页历史/用量） |
| 失败报告 | `solutions/<任务>_FAILED.md` | 网页错误提示 |
| 图片处理缓存 | `webapp/cache/images/`（可安全删除） | 同 |
| 监控去重账本 | `<工作根目录>/solutions/.monitor_seen.json` | 同 |

> CLI 产出的解答还会**复制一份**到 `webapp/solutions/`（网页服务把该目录挂在 `/solutions`，可按文件名直接访问），但**不会**出现在网页「任务」列表里——那个列表读的是网页自己的数据库，两边记录分开。

---

## 8. 配置速查（`.env`）

只列最常改的；完整清单见 `.env.example`（每项都有注释）。

### 路径与密钥
| 变量 | 默认 | 说明 |
|---|---|---|
| `SOLVER_ROOT_DIR` | 项目父目录 | 工作根目录；其下是 `Screenshots/`、`processed/`、`ocr/`、`solutions/` |
| `DEEPSEEK_API_KEY` | — | 求解 + 辅助 + 视觉层（**默认必填**） |
| `VISION_PROVIDER` | deepseek | 视觉层供应商：`deepseek`（分类/OCR/推理/核对全用 `deepseek-flash`）/ `zhipu`（回退；分类/OCR 用 `GLM-4.6V-FlashX`，推理/核对用 `GLM-4.6V`）。未知取值回落代码默认 provider（当前 `deepseek`） |
| `ZHIPU_API_KEY` | — | 仅 `VISION_PROVIDER=zhipu` 时需要 |

### 进图与分组
| 变量 | 默认 | 说明 |
|---|---|---|
| `GROUP_TIMEOUT` | 8 | 多少秒没有新图就提交当前一组 |
| `AUTO_IMPORT_ENABLED` | true | 网页模式是否也监控文件夹（只手动上传就设 false） |
| `MONITOR_RESCAN_INTERVAL` | 15 | 漏检补偿扫描间隔（秒），0=关闭 |
| `MONITOR_CATCHUP_MAX_AGE_MINUTES` | 120 | 补投的最大文件年龄（分钟），0=不限 |
| `MONITOR_STARTUP_SCAN` | true | 启动时先扫一遍 |
| `REMOTE_TRIGGER_PORT` | 5555 | 手机遥控截图服务端口 |
| `NUM_WORKERS` | 4 | **命令行**并发任务数（每个任务都花钱，别设太大） |
| `MAX_CONCURRENT_TASKS` | 2 | **网页**并发上限 |

### 解题质量与花费
| 变量 | 默认 | 说明 |
|---|---|---|
| `SOLVER_THINKING_DEFAULT` | false | 是否一上来就用"思考模式" |
| `SOLVER_ESCALATE_TO_THINKING` | true | 答案不合格（空/被截断/编程题过短）时自动升级到思考档重跑一次 |
| `SOLVER_MAX_TOKENS` | 16000 | 首选档输出上限 |
| `SOLVER_ESCALATE_MAX_TOKENS` | 32000 | 升级档输出上限（实测 API 接受 32768/65536） |
| `SOLVER_REASONING_EFFORT` | medium | 思考深度 low/medium/high |
| `IMAGE_MAX_EDGE` | 1600 | 发送前图片最长边（越小越快越省） |
| `IMAGE_JPEG_QUALITY` | 80 | 发送前 JPEG 质量 |

### 视觉层与 OCR（多图提速）
| 变量 | 默认 | 说明 |
|---|---|---|
| `USE_COMBINED_VISION_CALL` | auto | 合并视觉调用：`auto`=图数 ≤ `COMBINED_VISION_MAX_IMAGES` 时尝试，`true`=总是，`false`=从不 |
| `COMBINED_VISION_MAX_IMAGES` | 8 | 合并调用的适用图数上限；超出就整体走「分类 + 并行 OCR」回退路径 |
| `VISION_BATCH_SIZE` | 4 | 单次合并请求最多带几张图；超过就**分批并发**（8 图 → 2 批 → 2 次请求，实测比一次带完更快） |
| `VISION_BATCH_WORKERS` | 4 | 批间并行度（批与批互相独立，可并发） |
| `VISION_DISABLE_THINKING` | true | 关闭视觉层思考模式（DeepSeek 思考默认开启且 effort=high，不关会吃光 `max_tokens`） |
| `VISION_MAX_TOKENS` | 32768 | 视觉输出上限（旧值写死 8192，多图合并转录必被截断） |
| `VISION_COMBINED_TIMEOUT` | 300 | 合并调用专用超时（秒）；逐页 OCR 仍是 `VISION_TIMEOUT=120` |
| `VISION_INLINE_MERGE` | true | 合并成功后本地拼接并**跳过润色**；发现 CONT 页被多删内容时置 false 回退 |
| `OCR_PARALLEL_WORKERS` | 4 | 回退路径的逐页 OCR 并行度（合并调用成功时用不到） |
| `AUX_TIMEOUT` | 300 | 辅助调用（润色 / 文件名生成）超时（秒），旧值硬编码 120 会撞超时 |
| `FILENAME_MODE` | auto | 文件名生成：`auto`=求解正文首行 `FILE:` → 本地题号 → 才调模型；`local`=从不调模型；`model`=旧行为 |
| `OCR_DIR` | `<工作根目录>/ocr` | 逐页原始 OCR 归档目录（只读产物，可安全删除） |

### 访问控制（多人用时）
| 变量 | 默认 | 说明 |
|---|---|---|
| `AUTH_ENABLED` | false | false=单用户本地模式，不登录、不校验额度 |
| `AUTH_SECRET_KEY` | — | 开 `AUTH_ENABLED=true` 时**必须**显式设置 |
| `DEFAULT_USER_BUDGET` | 10.0 | 新用户赠送额度（元，估算口径） |
| `SMS_PROVIDER` | console | 验证码直接回显在接口响应里（仅本地自测） |

---

## 9. 视觉层一键回退到智谱

视觉层默认走 **DeepSeek**（`deepseek-flash`，与求解共用 `DEEPSEEK_API_KEY`）。
DeepSeek 侧限流/故障，或者你发现 OCR 质量在自家题库上退化时，改**两行 `.env`** 就能切回
智谱的 GLM-4.6V 系列，**代码一行不用改**：

```dotenv
VISION_PROVIDER=zhipu
ZHIPU_API_KEY=xxxxxxxx        # 只有这一行是新增；DEEPSEEK_API_KEY 保留不动
```

切完之后：

| 环节 | deepseek（默认） | zhipu（回退） |
|---|---|---|
| 分类 / OCR（多图合并） | `deepseek-flash` | `GLM-4.6V-FlashX` |
| 视觉推理 / 核对 | `deepseek-flash` | `GLM-4.6V` |
| 密钥 | `DEEPSEEK_API_KEY` | `ZHIPU_API_KEY` |
| 思考模式开关 | 默认关闭（`VISION_DISABLE_THINKING`） | 该 provider 不支持，开关不生效 |
| 求解 / 辅助 | 不变，仍是 `deepseek-flash` | 不变，仍是 `deepseek-flash` |

注意几点：

- 求解层**不跟着切** —— 它是另一张 provider 表（`SOLVER_CONFIG`），所以回退只影响读图；
- 重试旧任务时，阶段缓存里记了模型名，切换 provider 后旧缓存**自动失效**，不会混用；
- 想确认切成功了：启动日志（或 `python tools/diag.py`）里会打印
  `视觉层: provider=zhipu 模型=GLM-4.6V-FlashX`；
- 切之前建议先用 A/B 工具量一次：`python -m tools.vision_ab check -i <图或目录>`
  （同一组图跑两个 provider 的 OCR 并出差异报告）。**耗时与质量结论以实测为准，
  不要在没跑之前断言哪个更快**；
- 回退分支**长期保留**，不是临时过渡：单一供应商意味着 DeepSeek 挂了整条流水线都挂，
  而回退的代价只是一个环境变量。

---

## 10. 常见问题（FAQ）

**Q1：网页里上传了图片，为什么命令行那个 `python -m` 没反应？**
见第 2 节：两个是平级入口，网页上传由网页服务自己处理，不需要也不会启动命令行 Agent。

**Q2：手机拍了照，程序一点动静都没有？**
1. 照片进 `Screenshots` 了吗（Syncthing 是否同步完成）？
2. 命令行窗口还在吗？日志里有「文件监控已启动」吗？
3. 扩展名对不对（`.png/.jpg/.jpeg/.bmp/.webp`；`.syncthing.*.tmp` 会被有意跳过）？
4. 等一轮补偿扫描（默认 15 秒）——事件丢了也会被扫回来；
5. 还不行跑 `python tools/diag.py`。

**Q3：手机上怎么直接看答案？**
用 SMB 共享 `solutions` 目录，手机文件管理器打开即可（第 1.3 节有完整步骤）。

**Q4：`Alt+X` 按了没反应？**
① 必须用**管理员**终端启动 `silent_screencapper.py`；② 命令行 Agent 要在跑（否则图没人处理）；③ `Alt+X` 可能被别的软件占用，改脚本里的 `HOTKEY_STRING`；④ 看脚本自身打印的"截图成功! 已保存至:"。

**Q5：自动打字工具（human_typer）用了之后，Ctrl+V 不能粘贴了？**
它**故意接管**了 `Ctrl+V`（`suppress=True`）用来触发模拟输入。不需要时 `Ctrl+C` 退出即可恢复正常。

**Q5b：自动打字打到一半乱了 / 中途断掉？**
多半是**动了鼠标或切换了窗口**：脚本一开始会用 `pyautogui.click()` 把焦点交给"鼠标所在位置"的窗口，之后字符是往当前焦点窗口里送的；另外把鼠标移到屏幕角落会触发 pyautogui 的失效保护而中断。打字期间请**别点、别切窗口、别把鼠标甩到角落**（详见 1.5 节）。

**Q6：题目顺序会不会乱？**
不会。所有入口都按 **EXIF → 文件名时间 → mtime** 把一组图重排成拍摄顺序（第 6 节）。

**Q7：一道题的照片为什么变成两个任务了？**
两次拍照/截图间隔超过 `GROUP_TIMEOUT`（默认 8 秒）。调大它（如 15），或同一道题连着拍完。

**Q8：出答案时先等很久又说"只产出思考过程没有正文"？**
那是"思考模式"把输出配额写满了。现在的默认是**先不开思考快跑**，答案不合格才自动升级到「开思考 + 32000 token」重跑；升级档也没写出来就沿用第一版。想强制/禁止思考，用网页 ⟳ 菜单里的「开启/关闭思考模式重解」。

**Q9：怎么更省钱 / 更快？**
只开一个入口；保持 `SOLVER_THINKING_DEFAULT=false`；图多时把 `IMAGE_MAX_EDGE` 降到 1280；不需要自动导入就 `AUTO_IMPORT_ENABLED=false`。

**Q10：原图怎么不见了？**
命令行模式处理完会把原图**移动**到 `processed/`（设计如此）；网页模式不移动。

**Q11：想换模型？**
视觉层换供应商走 `.env` 的 `VISION_PROVIDER`（`deepseek` / `zhipu`，见第 9 节）；
换求解模型改 `problem_solver_agent/config.py` 的 `SOLVER_CONFIG`。
两张 provider 表互相独立：视觉层换 provider 不会动求解。新增 provider 的密钥命名规则是 `{PROVIDER}_API_KEY`（全大写）。

**Q12：网页打不开？**
看启动窗口报错；确认 `webapp/static/index.html` 存在（否则 `cd frontend && npm install && npm run build`）；端口被占用就 `python run_web.py 9000`；手机访问用局域网 IP（`tools/diag.py` 会打印）。

**Q13：多图任务现在要等多久？还会一页一页慢慢 OCR 吗？**
不会。最多 8 张图按 `VISION_BATCH_SIZE`（默认 4）**分批并发**，每批用
`<<<PAGE n|NEW/CONT>>>` 分隔符协议一次拿回"题型 + 该批全部逐页转录"：8 图 = 2 次请求
（旧行为是 9 次：1 次分类 + 8 次串行 OCR），合并成功时还会跳过润色调用。
2026-09-21 的真实实测：8 图分批合并 ≈6.4 s，一次带完 8 张 ≈8.4 s（同轮对照），
端到端（含求解）19.5 s。合并失败/图数超过 `COMBINED_VISION_MAX_IMAGES` 时自动回退到
「分类 + 并行 OCR」路径（`OCR_PARALLEL_WORKERS=4`），缺页按页补做，不会整批作废。

---

## 11. 排障工具箱

```powershell
# 一键自检：路径、密钥、Web 可达性、图片预处理收益、局域网 IP（做 SMB/扫码时要用）
python tools/diag.py
python tools/diag.py --api            # 额外做一次真实 API 探活（极小额花费）
python tools/diag.py --port 8000

# 视觉层 A/B 评测：同一组图跑两个 provider 的 OCR，出差异报告（换 provider 前先跑它）
python -m tools.vision_ab check -i "D:\Users\wzw\Pictures\Screenshots\test_images"

# 图片被"已投递过，跳过重复事件"永久跳过时，重投（先 --dry-run 看清单）
python -m tools.requeue --dry-run
python -m tools.requeue

# 健康检查（网页服务在跑时）
Invoke-RestMethod http://localhost:8000/api/health
Invoke-RestMethod http://localhost:8000/api/status
# 按关键词搜历史任务（题目文本 / 原始 OCR / 文件名）
Invoke-RestMethod "http://localhost:8000/api/tasks?q=洛必达"

# 看最近任务（SQLite 只读）
python -c "import sqlite3;c=sqlite3.connect('webapp/data/tasks.db');print(c.execute('select id,status,problem_type from tasks order by created_at desc limit 5').fetchall())"
```

日志关键字速查（在命令行窗口搜这些词）：

| 关键字 | 含义 |
|---|---|
| `文件监控已启动` | 监控正常起来了 |
| `检测到新图片（改名就位）` | Syncthing「临时文件+改名」的投递被识别了 |
| `补偿扫描：补投 N 张图片` | 兜底扫描捡回了漏掉的文件 |
| `补偿扫描跳过过早的文件` | 超过 `MONITOR_CATCHUP_MAX_AGE_MINUTES` 的老图被跳过（被中断的图用 `tools.requeue` 重投，见 6.4） |
| `该图片已投递过，跳过重复事件` | 账本认为投递过了；若其实没解出来，见 6.4 |
| `发现 N 个上次运行残留的处理锁` | 上次是被强杀的，这些图会被重新处理（正常的一次性提示） |
| `图片已按拍摄时间重排` | 到达顺序是乱的，已自动纠正 |
| `视觉层: provider=` | 当前视觉 provider 与模型（切 provider 后先看这一行） |
| `合并调用（分类 + 转录），协议=PAGE` | 8 图走的是 1 次合并请求 |
| `跳过合并调用` | 图数超上限或开关关闭，走「分类 + 并行 OCR」两步流程 |
| `PAGE 协议解析失败` / `回退 JSON 协议` | 合并结果没解析出来，正在逐级回退 |
| `求解画像:` | 每次求解的模型/思考/配额/字符数/finish_reason |
| `流水线失败 task=` | 该任务失败，后面跟原因 |

---

## 12. 目录结构

```
OnlineTest/
├─ problem_solver_agent/          # 核心逻辑（CLI 与 Web 共用）
│  ├─ main.py                    # ★ 命令行入口：python -m problem_solver_agent.main
│  ├─ config.py                  # 全部配置常量（.env 覆盖）+ 视觉 provider 表
│  ├─ file_monitor.py            # 文件夹监控：新建 + 改名就位 + 补偿扫描 + 去重账本
│  ├─ image_grouper.py           # 时间窗分组 + 工作线程池
│  ├─ image_order.py             # 按拍摄时间排序（EXIF → 文件名 → mtime）
│  ├─ vision_client.py           # 分类 / 多图合并转录（PAGE 协议）/ 逐页 OCR / 视觉推理
│  ├─ solver_client.py           # 求解（流式、思考模式、按需升级）+ 辅助调用（润色/文件名）
│  ├─ core_pipeline.py           # 流水线编排：识别 → 拼接 → 润色 → 求解 → 答案卡 → OCR 归档
│  └─ prompts.py                 # 全部 Prompt 模板
├─ tools/                        # 小工具
│  ├─ diag.py                    # 一键自检（含视觉 provider / 模型 / 思考状态）
│  ├─ vision_ab.py               # ★ 视觉层 A/B 评测（两个 provider 的 OCR 差异报告）
│  ├─ silent_screencapper.py     # ★ 静默热键截图（Alt+X，需管理员）
│  ├─ human_typer.py             # 真实打字模拟器（接管 Ctrl+V）
│  └─ remote_trigger.py          # 手机遥控触发截图（默认端口 5555）
├─ webapp/                       # 网页服务（复盘用）
│  ├─ app.py / routes.py / auto_import.py
│  ├─ data/tasks.db              # 任务 + 账号 + 用量（含 problem_text / ocr_raw_text / vision_mode）
│  ├─ solutions/ uploads/ static/
├─ frontend/                     # React 源码（改完要 npm run build）
├─ tests/ docs/                  # 测试与文档
├─ run_web.py / start_web.bat     # 网页模式启动
└─ .env                          # 你的密钥与配置（不进版本库）
```

工作根目录（默认项目父目录）下：

```
Pictures/
├─ Screenshots/     ← 监控目录（Syncthing 目标；也可手机 SMB 直接放照片）
├─ processed/       ← 处理完的原图归档
├─ ocr/             ← 逐页原始 OCR 归档（<日期>/<task_id>.md）
└─ solutions/       ← 解答（手机 SMB 看这里）+ .monitor_seen.json 账本
```

---

## 13. 已知限制与后续可改进

| 限制 | 说明 / 改进方向 |
|---|---|
| 两个入口同时跑会重复处理 | 靠自觉二选一；可改进：让网页自动导入复用 CLI 的锁文件，做到互斥 |
| 分组只按时间窗 | 同一道题分两批拍（间隔 > 8 秒）会被拆成两个任务；可改进：按拍摄时间聚类 |
| 顺序依赖 EXIF/文件名 | 都被清掉时只能退化成 mtime（某些聊天软件转发会丢 EXIF） |
| "对错"无法自动判断 | 升级/重跑只看"答案是否完整"；判对错要用「核对答案」 |
| 核对结果不跨刷新保留 | 刷新后已跑过的核对结论不显示（库里只存 `verified` 标志） |
| 超过 8 图的图组不分批 | 超过 `COMBINED_VISION_MAX_IMAGES`（默认 8）的图组整体退回「分类 + 并行 OCR」，调用次数重新变多（8 张以内已是分批并发，无此问题） |
| 历史搜索用 LIKE，未上 FTS5 | `TASK_RETENTION_COUNT=100` 是百行表，LIKE 是微秒级；FTS5 默认分词器对中文无效，要 `tokenize='trigram'` 还得多维护一张虚表，收益为零。等任务表涨到万级时再换 |
| 隐私没有因为迁移而改善 | 图片以 base64 **编码**（不是加密）发送，模型看到的就是完整原图；换 provider 只是换了接收方，暴露面不变。唯一根治手段是本机跑视觉模型 |

---

### 附：最短上手清单（可以打印贴墙）

```
【一次性准备】
1. pip install -r requirements.txt
2. copy .env.example .env → 填 DEEPSEEK_API_KEY（视觉层默认也用它；ZHIPU_API_KEY 按需）
3. 手机装 Syncthing：DCIM/Camera（仅发送）→ D:\Users\wzw\Pictures\Screenshots（仅接收）
4. （想在手机看答案）把 solutions 文件夹做成共享（SMB），手机文件管理器添加

【每天这样用】
5. 终端：conda activate llm → cd D:\Users\wzw\Pictures\OnlineTest
            python -m problem_solver_agent.main
6. 手机按顺序拍题（同一道题连着拍完），或用电脑端 Alt+X 静默截图
7. 停 8 秒后自动处理；答案出现在 solutions/，手机上直接打开看
8. 想在考试客户端里作答 → 手机复制代码（跨设备剪贴板同步到电脑）
   → 鼠标停在输入框上 → python tools/human_typer.py → Ctrl+V（期间别动鼠标）
9. 想复盘/重解/核对 → 关掉它，改开 start_web.bat（别同时开）
10. （可选）视觉层想回退智谱：.env 里 VISION_PROVIDER=zhipu + ZHIPU_API_KEY
```
