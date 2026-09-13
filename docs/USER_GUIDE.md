# 使用说明（User Guide）

> **一句话**：手机拍题 → 自动同步到电脑的一个文件夹 → 程序识别并求解 → 在网页或 Markdown 文件里看答案。
>
> 本文档面向两类读者：**刚拿到代码的用户**，以及**几个月后忘了怎么用的自己**。
> 想了解内部实现请看 `docs/ARCHITECTURE.md`；接口细节看 `docs/API.md`。

---

## 0. 30 秒速览：三种典型用法

| 你想怎么用 | 启动什么 | 图片怎么进来 | 答案在哪 |
|---|---|---|---|
| **A. 手机拍完自动出答案**（推荐） | 网页模式：双击 `start_web.bat` | 手机 Syncthing 自动同步到 `Screenshots`，网页会自动弹提示 | 网页「解答」面板；文件在 `webapp/solutions/` |
| **B. 电脑上手动传图** | 网页模式：双击 `start_web.bat` | 网页拖拽/选择图片上传 | 同上 |
| **C. 只想要 Markdown 文件，不要界面** | `python -m problem_solver_agent.main` | 手机同步 / 手动把图丢进 `Screenshots` | `<工作根目录>/solutions/*.md`，原图归档到 `processed/` |

> **工作根目录**默认是**项目的父目录**。本项目在 `D:\Users\wzw\Pictures\OnlineTest`，所以工作根目录是 `D:\Users\wzw\Pictures`，监控目录就是 `D:\Users\wzw\Pictures\Screenshots`。
> 想改就设 `SOLVER_ROOT_DIR`。**启动日志第一条就会把四个实际路径打出来**，不确定时看日志。

---

## 1. 系统由哪几块组成？"后端"到底指什么？⭐

这是最容易搞混的地方，先讲清楚。

```
                      ┌──────────────────────────────┐
   手机拍照            │  D:\...\Pictures\Screenshots │  ← 监控目录（Syncthing 同步目标）
   （按时间顺序）  ───▶ │        （一个普通文件夹）      │
                      └──────────────┬───────────────┘
                                     │ 两个"监控者"二选一，别同时开
                 ┌───────────────────┴────────────────────┐
                 ▼                                        ▼
   ① 命令行 Agent                              ② 网页服务（自带监控）
   python -m problem_solver_agent.main         python run_web.py / start_web.bat
   · 只监控文件夹                                · 提供网页 + 手动上传
   · 答案写到 solutions/*.md                     · 也监控同一个文件夹
   · 原图归档到 processed/                       · 答案写到 webapp/solutions + 数据库
                 └───────────────────┬────────────────────┘
                                     ▼
                        同一套核心流水线（core_pipeline）
                分类 → 逐页 OCR → 按拍摄时间拼成题干 → 润色 → 求解
```

### 你问的"为什么网页上传后 `python -m` 没启动？"

**因为它本来就不该启动。** 这里面有两个不同的东西：

| 名字 | 是什么 | 干什么 |
|---|---|---|
| `python -m problem_solver_agent.main` | **命令行 Agent**，一个独立程序 | 盯着文件夹，自动处理新图片，产出 md 文件 |
| `python run_web.py`（`start_web.bat` 就是它） | **网页服务**（FastAPI + React SPA） | 提供网页界面、手动上传接口，**并且自己也会盯着同一个文件夹**（`AUTO_IMPORT_ENABLED=true` 时） |

所以：
- 你点"一键启动"，启动的是**②网页服务**；
- 在网页里上传图片，是**②网页服务自己**受理的（上传 → 建任务 → 在服务端进程里跑同一套流水线 → 推送到网页），**不会**去启动 ①；
- ①和②是**平级的两个入口**，各自都能独当一面。网页模式根本不需要 ① 在跑。

### ⚠️ 两条同时跑会怎样（重要）

两个监控者盯着同一个文件夹，**同一张照片会被处理两次**（两边都调 API、都出答案），而且 CLI 处理完会把原图**移走**到 `processed/`，可能让网页那边读不到图。

**结论：同一时间只开一个。** 建议：

- 想要界面、想手动传图、想在手机上看 → **只开网页模式**；
- 只想要 md 文件、机器上不常开浏览器 → **只开命令行模式**；
- 如果你开了网页服务、又不想它自动处理文件夹（只想手动上传）→ 在 `.env` 里设 `AUTO_IMPORT_ENABLED=false`。

---

## 2. 前置条件

### 电脑端
| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows（脚本按 Windows 写） | Linux/macOS 也能跑，命令自行替换 |
| Python | 3.10+ | `python --version` 能出版本即可 |
| Node.js | 20+ | **仅当需要重新构建前端时**。`webapp/static/` 里已有构建产物时可跳过 |
| 依赖包 | `pip install -r requirements.txt` | `start_web.bat` 会自动检查并补装 |
| API 密钥 | DeepSeek + 智谱（GLM-4.6V） | 见下一节 |

### 手机端
| 项目 | 要求 |
|---|---|
| 系统 | Android / vivo 等（拍照即可） |
| 同步工具 | Syncthing（或 Syncthing-Fork，对热点模式更友好） |
| 网络 | 手机与电脑在同一局域网（家里 Wi-Fi，或手机开热点让电脑连） |

### 密钥配置（必做）

```powershell
cd D:\Users\wzw\Pictures\OnlineTest
copy .env.example .env     # 第一次
notepad .env
```

`.env` 里至少要填两项（[platform.deepseek.com](https://platform.deepseek.com) 与 [open.bigmodel.cn](https://open.bigmodel.cn) 注册获取）：

```dotenv
DEEPSEEK_API_KEY=sk-xxxxxxxx        # 求解模型：deepseek-flash
ZHIPU_API_KEY=xxxxxxxx             # 视觉模型：GLM-4.6V 系列（分类 / OCR / 核对）
```

> 只想在电脑上手动传图、不用手机同步的用户，可以**跳过第 3 节**。

---

## 3. 手机 → 电脑：让照片自动落到 `Screenshots`

目标：手机拍的照片，几十秒内自动出现在 `D:\Users\wzw\Pictures\Screenshots`（也就是监控目录）。

### 3.1 安装 Syncthing

- **电脑**：https://syncthing.net/ 下载 Windows 版，解压运行 `syncthing.exe`，浏览器打开 `http://localhost:8384` 进入管理界面。
- **手机**：应用商店 / F-Droid 安装 **Syncthing**（推荐 **Syncthing-Fork**，热点模式兼容性更好），并设置：
  - 允许后台运行；
  - 电池优化设为「无限制」（设置 → 应用管理 → Syncthing → 电池）。

### 3.2 配对设备

1. 电脑端管理界面 → 操作 → 显示 ID → 复制**电脑设备 ID**；
2. 手机端 → 设备 → 添加设备 → 粘贴电脑 ID；
3. 电脑端会弹确认框 → 添加设备；手机端同理添加电脑。

### 3.3 配置文件夹（方向别搞反）

| 端 | 路径 | 文件夹类型 | 共享给 |
|---|---|---|---|
| **手机** | `/storage/emulated/0/DCIM/Camera`（相机目录） | **仅发送 Send Only** | 电脑 |
| **电脑** | `D:\Users\wzw\Pictures\Screenshots` | **仅接收 Receive Only** | 手机 |

> 目录必须是**监控目录**。不确定就把 `.env` 里的 `SOLVER_ROOT_DIR` 显式设成 `D:\Users\wzw\Pictures`，然后监控目录固定为 `<它>\Screenshots`。

### 3.4 手机热点模式（没有 Wi-Fi、没有路由器时）

- 手机开启「移动热点」，电脑连上该热点（此时手机就是路由器，两者处于同一局域网）；
- 手机端 Syncthing：运行条件里勾上**移动数据**和 **Wi-Fi**；
- 电脑端若发现不了手机：在手机设备的「高级」里把地址从"默认"改为手动 `tcp://192.168.43.1:22000`（IP 换成手机热点网关，一般是 `192.168.43.1`）；
- Windows 防火墙放行 Syncthing，端口 `22000`（TCP/UDP）；
- 热点模式下走局域网直传，**不消耗手机流量**。

### 3.5 验证同步

1. 手机拍一张照片；
2. 电脑上打开 `Screenshots` 文件夹，等 10–30 秒，照片应出现；
3. Syncthing 界面显示连接类型为 **TCP LAN**（不是 WAN 中继）即为直连。

### 3.6 Syncthing 故障排查

| 现象 | 处理 |
|---|---|
| 互相发现不了 | 检查热点是否允许多设备通信；改为手动填 IP 地址 |
| 热点下 Syncthing 不启动 | 手机端勾选"运行在移动数据上" |
| 显示 WAN 中继而不是 LAN | 手动把地址设为手机热点 IP（见 3.4） |
| 同步很慢 | 确认没走中继；频繁传大图建议 5GHz 热点 |
| 锁屏后停止同步 | 关闭电池优化，允许后台活动 |

> 备选方案 **LocalSend**（https://localsend.org/）：免配对，手机选照片手动发送到电脑；缺点是需要手动点一下，不是全自动。

---

## 4. 方式 A：命令行模式（只要 md 文件）

```powershell
cd D:\Users\wzw\Pictures\OnlineTest
conda activate llm            # 若使用 conda 环境（按你自己的环境来）
python -m problem_solver_agent.main
```

启动日志会依次打印：密钥检查 → 求解器健康检查 → **四个实际路径** → "文件监控已启动"。

运行期间它会：
1. 监听 `Screenshots`：图片一到位（含 Syncthing「改名就位」）就收进当前分组；
2. 连续 8 秒没有新图（`GROUP_TIMEOUT`）→ 把这一组提交处理；
3. 跑完流水线 → 解答写到 `<工作根目录>/solutions/*.md`，原图归档到 `processed/`。

停止：在该窗口按 **Ctrl+C**。

典型日志（正常工作时长这样）：

```
文件监控已启动，正在监视目录: D:\Users\wzw\Pictures\Screenshots
检测到新图片（改名就位）: IMG_20260913_164123.jpg
图片已添加到组: IMG_20260913_164123.jpg (当前组共 1 张)
...
超时! 包含 4 张图片的组已提交到处理队列。 顺序: IMG_...164123(16:41:23/filename) → IMG_...164126(...) → ...
[Worker-1] 正在分析 4 张图片…
```

---

## 5. 方式 B：网页模式（推荐）

### 5.1 启动

```powershell
# 最省事：双击 start_web.bat（会检查 Python/.env/依赖/前端产物，然后启动）
# 等价命令：
cd D:\Users\wzw\Pictures\OnlineTest
python run_web.py            # 默认 8000 端口；python run_web.py 9000 可换端口
```

浏览器打开 **http://localhost:8000**。手机在同一局域网时，点顶部「手机扫码」用手机浏览器打开同一地址即可远程看进度。

> 开发模式（改 Python 代码自动重启）：`python run_web_dev.py`。

### 5.2 界面导览

| 位置 | 作用 |
|---|---|
| **解题台**（首页） | 左边拖拽/选择图片上传；右边是「解答」面板（流式出答案 + 思考过程 + 答案卡 + 核对） |
| **任务** | 历史任务列表，点进去看旧答案 |
| **设置** | 路径、模型、监控状态等 |
| **用量 / 管理** | 仅在开启 `AUTH_ENABLED=true` 的多用户模式下有意义 |
| **手机扫码** | 显示局域网地址二维码，手机扫码即可远程查看/上传 |

### 5.3 两种进图方式（网页模式同时具备）

1. **自动导入**：手机 Syncthing 同步到 `Screenshots` → 网页右上角弹出「新任务」提示 → 点「查看」（勾"自动切换"则自动跳转）。
2. **手动上传**：直接拖拽/选择图片 → 点开始 → 立刻建任务并开始处理。

> 网页服务也会监控文件夹（`AUTO_IMPORT_ENABLED=true` 默认）。若你 **同时** 开着命令行 Agent，就会重复处理——请二选一；只想要手动上传就把 `AUTO_IMPORT_ENABLED` 设为 `false`。

---

## 6. 图片顺序与分组（决定题目对不对）

### 6.1 顺序：按"拍摄时间"，不是"到达时间"

Syncthing 是按数据块同步的，**照片落到文件夹的顺序是随机的**。而多图题目的处理是
「逐页 OCR → 按顺序拼成题干 → 求解」，顺序错了整道题就废了。

程序在 OCR 之前会统一重排，时间来源按可靠性三级回退：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | **EXIF `DateTimeOriginal`** | 相机原始拍摄时间；微信/QQ 转存改名后仍然保留 |
| 2 | **文件名时间戳** | `IMG_20260913_164123.jpg`、`Screenshot_20260913-164131.png`、`屏幕截图 2025-11-20 093340.png`、`IMG-20260913-WA0001.jpg`（只有日期也能按天排） |
| 3 | **文件 mtime** | Syncthing 会保留手机端时间，通常也等于拍摄时间 |

- 时间相同时保持原顺序（稳定排序）。
- **手动上传也一样**：上传保留原始文件字节（EXIF 在），所以网页里"全选 8 张一起上传"同样会自动重排。
- 因此**拍照时不需要刻意按顺序传**，拍的时候按顺序拍就行；但为了保险，建议**同一道题的照片连续拍**（见下条）。

### 6.2 分组：每来一张图，8 秒计时器就重置

```dotenv
GROUP_TIMEOUT=8      # 连续 8 秒没有新图片 → 这一组提交处理
```

- 连着拍的 4 张（间隔几秒）→ 一组 → 当成一道 4 页的题；
- 拍完过了 1 分钟再拍 → 新的一组 → 当成另一道题。

**建议**：同一道题的照片**连着拍完**，别中途去拍别的题；拍完等十来秒，程序就开始处理了。

### 6.3 漏检兜底（为什么现在不怕"没检测到"）

监控除了监听"新建文件"和"改名就位"，还有**补偿扫描**：

```dotenv
MONITOR_RESCAN_INTERVAL=15            # 每 15 秒扫一遍目录兜底（0=关闭）
MONITOR_CATCHUP_MAX_AGE_MINUTES=120   # 只补投 mtime 在 120 分钟内的文件（0=不限）
MONITOR_STARTUP_SCAN=true             # 启动时先扫一遍（补上上次没处理完的照片）
```

去重账本记在 `<工作根目录>/solutions/.monitor_seen.json`（**不在**同步目录里，避免把账本同步到手机）。

---

## 7. 产物在哪（别到处找）

| 产物 | 命令行模式 | 网页模式 |
|---|---|---|
| 解答 Markdown | `<工作根目录>/solutions/*.md`（如 `D:\Users\wzw\Pictures\solutions`） | `webapp/solutions/*.md` |
| 原图归档 | `<工作根目录>/processed/`（处理完会从 Screenshots **移走**） | 原图留在 `webapp/uploads/<任务ID>/`（不移动） |
| 任务数据库 | — | `webapp/data/tasks.db`（SQLite，网页历史/用量都在这） |
| 失败报告 | `solutions/<任务>_FAILED.md` | 网页上显示错误信息 |
| 图片处理缓存 | `webapp/cache/images/`（自动清理） | 同 |
| 监控去重账本 | `<工作根目录>/solutions/.monitor_seen.json` | 同 |

> 网页模式启动时若检测到 CLI 生成的解答，会把它**复制一份**到 `webapp/solutions/`（网页服务把该目录挂在 `/solutions` 下，可直接按文件名访问）。
> 注意：CLI 产出**不会**出现在网页的「任务」列表里——那个列表读的是网页自己的数据库 `webapp/data/tasks.db`，两边记录是分开的。

---

## 8. 配置速查（`.env`）

只列最常改的；完整清单见 `.env.example`（每一项都有注释）。

### 路径与密钥
| 变量 | 默认 | 说明 |
|---|---|---|
| `SOLVER_ROOT_DIR` | 项目父目录 | 工作根目录；其下是 `Screenshots/`、`processed/`、`solutions/` |
| `DEEPSEEK_API_KEY` | — | 求解模型（必填） |
| `ZHIPU_API_KEY` | — | 视觉模型 GLM-4.6V（必填） |

### 进图与分组
| 变量 | 默认 | 说明 |
|---|---|---|
| `GROUP_TIMEOUT` | 8 | 多少秒没有新图就把当前一组提交 |
| `AUTO_IMPORT_ENABLED` | true | 网页模式是否也监控文件夹（只手动上传就设 false） |
| `MONITOR_RESCAN_INTERVAL` | 15 | 漏检补偿扫描间隔（秒），0=关闭 |
| `MONITOR_CATCHUP_MAX_AGE_MINUTES` | 120 | 补投的最大文件年龄（分钟），0=不限 |
| `MONITOR_STARTUP_SCAN` | true | 启动时是否先扫一遍 |
| `NUM_WORKERS` | 4 | **命令行**的并发任务数（每个任务都会花钱，别设太大） |
| `MAX_CONCURRENT_TASKS` | 2 | **网页**的并发上限 |

### 解题质量与花费
| 变量 | 默认 | 说明 |
|---|---|---|
| `SOLVER_THINKING_DEFAULT` | false | 是否一上来就用"思考模式" |
| `SOLVER_ESCALATE_TO_THINKING` | true | 答案不合格（空/被截断/编程题过短）时自动升级到思考档重跑一次 |
| `SOLVER_MAX_TOKENS` | 16000 | 首选档输出上限 |
| `SOLVER_ESCALATE_MAX_TOKENS` | 32000 | 升级档输出上限（实测 API 接受 32768/65536） |
| `SOLVER_REASONING_EFFORT` | medium | 思考深度 low/medium/high |
| `USE_COMBINED_VISION_CALL` | auto | 合并视觉调用：`auto`=仅单图尝试，`true`=总是，`false`=从不 |
| `IMAGE_MAX_EDGE` | 1600 | 发送前把图片最长边缩到多少像素（越小越快越省钱） |
| `IMAGE_JPEG_QUALITY` | 80 | 发送前 JPEG 质量 |

### 访问控制（多人用时才需要）
| 变量 | 默认 | 说明 |
|---|---|---|
| `AUTH_ENABLED` | false | false=单用户本地模式，不登录、不校验额度 |
| `AUTH_SECRET_KEY` | — | 开 `AUTH_ENABLED=true` 时**必须**显式设置，否则每次重启都会让登录失效 |
| `DEFAULT_USER_BUDGET` | 10.0 | 新用户赠送额度（元，估算口径） |
| `SMS_PROVIDER` | console | `console` 表示验证码直接回显在接口响应里（仅本地自测） |

---

## 9. 常见问题（FAQ）

**Q1：网页里上传了图片，为什么命令行那个 `python -m` 没反应？**
见第 1 节：网页上传由**网页服务自己**处理，不需要也不会启动命令行 Agent。两个是平级入口。

**Q2：手机拍了照，程序一点动静都没有？**
按顺序排查：
1. 照片到底进了 `Screenshots` 吗？（Syncthing 界面是否显示已完成；`Screenshots` 里能看到文件吗）
2. 程序在跑吗？（那个窗口还在吗；日志里有没有"文件监控已启动"）
3. 文件名/后缀对不对？（只处理 `.png/.jpg/.jpeg/.bmp/.webp`；`.syncthing.*.tmp` 这类临时文件会被有意跳过）
4. 等一轮补偿扫描（默认 15 秒）——即使事件丢了也会被扫回来；
5. 还不行就跑 `python tools/diag.py` 看路径与连通性。

**Q3：题目顺序会不会乱？**
不会。所有入口都会按 **EXIF → 文件名时间 → mtime** 把一组图重排成拍摄顺序（第 X 页）。若你的相机既不写 EXIF、文件名也没有时间戳，才会退化成 mtime。

**Q4：一道题拍了两张以上，为什么变成两个任务了？**
两次拍照间隔超过了 `GROUP_TIMEOUT`（默认 8 秒没新图就提交）。想更宽容就把它调大（如 15）；同一道题的照片尽量连着拍。

**Q5：出答案时先等很久又说"只产出思考过程没有正文"？**
那是"思考模式"把输出配额写满了（思考与正文共享配额）。现在的默认策略是**先不开思考快跑**，只有答案不合格才自动升级到「开思考 + 32000 token」重跑；升级档也没写出来就沿用第一版。想强制思考/不思考，网页「解答」面板右上角 ⟳ 菜单里有「开启/关闭思考模式重解」。

**Q6：怎么更省钱 / 更快？**
- 只开一个入口（别同时开命令行和网页监控）；
- 保持 `SOLVER_THINKING_DEFAULT=false`、`SOLVER_ESCALATE_TO_THINKING=true`；
- 图多时把 `IMAGE_MAX_EDGE` 降到 1280；
- 不需要自动导入时设 `AUTO_IMPORT_ENABLED=false`，只在需要时手动传。

**Q7：原图怎么不见了？**
命令行模式处理完会把原图**移动**到 `processed/`（这是设计行为）；网页模式不移动，留在 `webapp/uploads/`。

**Q8：想换模型？**
改 `problem_solver_agent/config.py` 的 `SOLVER_CONFIG`（求解模型）与 `VISION_*_MODEL`（视觉模型），或在 `.env` 里覆盖相关变量。新增 provider 时密钥命名规则为 `{PROVIDER}_API_KEY`（全大写）。

**Q9：网页打不开？**
- 看启动窗口有没有报错；确认 `webapp/static/index.html` 存在（不存在就 `cd frontend && npm install && npm run build`）；
- 端口被占用就 `python run_web.py 9000`；
- 手机访问用电脑的**局域网 IP**（`python tools/diag.py` 会打印），不是 `localhost`。

**Q10：换端口/多开会不会冲突？**
网页服务用同一个 `webapp/data/tasks.db`，不建议同时跑多个实例；换端口只影响访问地址。

---

## 10. 排障工具箱

```powershell
# 一键自检：路径、密钥、Web 可达性、图片预处理收益、局域网 IP
python tools/diag.py
python tools/diag.py --api                 # 额外做一次真实 API 探活（极小额花费）
python tools/diag.py --port 8000           # 指定网页端口

# 健康检查（网页服务在跑时）
Invoke-RestMethod http://localhost:8000/api/health
Invoke-RestMethod http://localhost:8000/api/status

# 看最近的任务与用量（SQLite，只读）
python -c "import sqlite3;c=sqlite3.connect('webapp/data/tasks.db');print(c.execute('select id,status,problem_type from tasks order by created_at desc limit 5').fetchall())"
```

日志关键字速查（搜索启动窗口里的这些词）：

| 关键字 | 含义 |
|---|---|
| `文件监控已启动` | 监控正常起来了 |
| `检测到新图片（改名就位）` | Syncthing 那种"临时文件+改名"的投递被识别了 |
| `补偿扫描：补投 N 张图片` | 兜底扫描捡回漏掉的文件 |
| `图片已按拍摄时间重排` | 到达顺序是乱的，已自动纠正 |
| `跳过合并调用` | 多图直接用两步流程（更快，不会白等） |
| `求解画像:` | 每次求解的模型/思考/配额/字符数/finish_reason |
| `流水线失败 task=` | 该任务失败，后面跟原因 |

---

## 11. 目录结构（主要部分）

```
OnlineTest/
├─ problem_solver_agent/          # 核心逻辑（CLI 与 Web 共用）
│  ├─ main.py                    # 命令行入口：python -m problem_solver_agent.main
│  ├─ config.py                  # 全部配置常量（.env 覆盖）
│  ├─ file_monitor.py            # 文件夹监控：新建 + 改名就位 + 补偿扫描 + 去重账本
│  ├─ image_grouper.py           # 时间窗分组 + 工作线程池
│  ├─ image_order.py             # 按拍摄时间排序（EXIF → 文件名 → mtime）
│  ├─ vision_client.py           # 分类 / 逐页 OCR / 视觉推理
│  ├─ solver_client.py           # 求解（流式、思考模式、按需升级）
│  ├─ core_pipeline.py           # 流水线编排：识别 → 拼接 → 润色 → 求解 → 答案卡
│  └─ prompts.py                 # 全部 Prompt 模板
├─ webapp/                       # 网页服务
│  ├─ app.py                     # FastAPI 入口（run_web.py 调它）
│  ├─ routes.py                  # 所有 HTTP/SSE 接口
│  ├─ auto_import.py             # 网页模式自带的文件夹监控
│  ├─ data/tasks.db              # 任务 + 账号 + 用量（SQLite）
│  ├─ solutions/                 # 网页模式产出的解答
│  ├─ uploads/<任务ID>/          # 上传/自动导入的图片
│  └─ static/                    # 前端构建产物
├─ frontend/                     # React 源码（改完要 npm run build）
├─ tests/                        # pytest 测试
├─ docs/                         # 文档（本文档、ARCHITECTURE、API、DEPLOY、DEVELOPMENT）
├─ tools/diag.py                 # 一键自检
├─ run_web.py / start_web.bat     # 网页模式启动
└─ .env                          # 你的密钥与配置（不进版本库）
```

工作根目录（默认项目父目录）下：

```
Pictures/
├─ Screenshots/     ← 监控目录（Syncthing 同步目标）
├─ processed/       ← 命令行模式处理完的原图归档
└─ solutions/       ← 命令行模式产出的解答 + .monitor_seen.json 账本
```

---

## 12. 已知限制与后续可改进

| 限制 | 说明 / 可能的改进方向 |
|---|---|
| 两个入口同时跑会重复处理 | 目前靠"自觉二选一"。可改进：让网页的自动导入也复用 CLI 的锁文件，做到互斥 |
| 分组只按时间窗 | 若同一道题的照片被拍成两批（间隔 > 8 秒）会被拆成两个任务；可改进：按"拍摄时间聚类"而不是仅按到达时间 |
| 顺序依赖 EXIF/文件名 | 都被清掉时只能退化成 mtime（例如经过某些聊天软件压缩转发） |
| 答案"对错"无法自动判断 | 升级/重跑只看"答案是否存在且完整"；判对错要用「核对答案」（GLM-4.6V 对照原图复核） |
| 核对结果不跨刷新保留 | 刷新页面后已跑过的核对结论不显示（数据库只存了 `verified` 标志） |
| 并发都是"整任务级" | 同一任务内部 OCR 串行（`OCR_PARALLEL_WORKERS=1`），多图时较慢 |

---

### 附：最短上手清单（打印出来贴墙）

```
1. pip install -r requirements.txt
2. copy .env.example .env  → 填 DEEPSEEK_API_KEY / ZHIPU_API_KEY
3. 手机装 Syncthing，把 DCIM/Camera（仅发送）同步到 D:\Users\wzw\Pictures\Screenshots（仅接收）
4. 双击 start_web.bat  → 浏览器打开 http://localhost:8000
5. 手机按顺序拍题（同一道题连着拍完），等约 10 秒
6. 网页右上角弹「新任务」→ 查看 → 出答案；文件在 webapp/solutions/
   （不要在同时再开 python -m problem_solver_agent.main）
```
