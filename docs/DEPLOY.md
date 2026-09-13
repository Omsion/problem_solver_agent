# 部署文档（Docker / Docker Compose）

本文档描述如何用 Docker 把「自动化解题 Agent」部署成**单容器**服务：
一个容器里同时跑 FastAPI 后端和它托管的 React 前端静态产物。

> 前端不单独跑容器。`frontend/` 在**镜像构建期**由 `npm run build` 编译
> （`frontend/vite.config.ts` 的 `build.outDir = ../webapp/static`），
> 产物由 `webapp/app.py` 通过 `StaticFiles` 挂在 `/static`，
> 未匹配的 GET 请求由 SPA fallback 返回 `index.html`。

相关文件：

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 三阶段构建：前端构建 → Python 依赖 → 运行时 |
| `.dockerignore` | 缩小构建上下文，并排除 `.env`（含密钥）与运行时数据 |
| `docker-compose.yml` | 单服务 `app` 的编排、端口、卷、健康检查 |
| `.env.example` | 环境变量清单，首次部署复制为 `.env` |

---

## 1. 前置要求

| 组件 | 最低版本 | 说明 |
| --- | --- | --- |
| Docker Engine | 20.10+ | 需要 BuildKit（默认开启）支持 `RUN --mount=type=cache` |
| Docker Compose | v2.20+（插件形式 `docker compose`） | 使用 v2 语法与 `healthcheck.start_period` 等字段 |
| 磁盘 | 约 3 GB 空闲 | Node 构建层 + Python 依赖层 + 镜像 |

说明：

- **构建期**需要能拉取 `node:20-alpine`、`python:3.10-slim`，并能访问 npm registry 与 PyPI。
  前端依赖要求 Node `^20.19.0 || >=22.12.0`（Vite 8 / `@vitejs/plugin-react` 6 的 `engines`），
  本 Dockerfile 用的 `node:20-alpine` 已满足。
- **运行期**只需要能访问大模型 API（DeepSeek / 智谱），不需要数据库、缓存或消息队列。
- Windows 上推荐 Docker Desktop（WSL2 后端）。
- 若使用老式 `docker-compose`（连字符、v1），命令请自行把 `docker compose` 换成 `docker-compose`，
  并注意 v1 对 `healthcheck.start_period` 之外的部分字段支持有限。

---

## 2. 首次部署

### 2.1 准备 `.env`

```bash
# Linux / macOS
cp .env.example .env

# Windows PowerShell
Copy-Item .env.example .env
```

然后编辑 `.env`，**至少**填好两个密钥（缺任一都会导致对应能力不可用，
启动日志里会有明确的“能力探测”告警）：

```dotenv
DEEPSEEK_API_KEY=sk-xxxxxxxx        # 求解器与辅助模型统一使用 deepseek-flash
ZHIPU_API_KEY=xxxxxxxx              # 视觉模型 GLM-4.6V（分类 / OCR / 视觉推理）
```

其余可选变量（`GROUP_TIMEOUT`、`MAX_CONCURRENT_TASKS`、`IMAGE_MAX_EDGE`、
`MONITOR_RESCAN_INTERVAL`（漏检补偿扫描）、`SOLVER_THINKING_DEFAULT`（思考模式首选与升级）、
`USE_COMBINED_VISION_CALL`（合并调用模式）等）见 `.env.example` 注释。

关于 `SOLVER_ROOT_DIR`：

- **不要在 `.env` 里设置成宿主 Windows 路径**。容器内统一由 `docker-compose.yml` 的
  `environment.SOLVER_ROOT_DIR=/data` 决定（`environment` 优先级高于 `env_file`），
  宿主的截图目录通过 `SOLVER_DATA_DIR` 这个**宿主编排变量**来选（见 2.2）。
- `.env` 已被 `.gitignore` 与 `.dockerignore` 排除，不会进仓库也不会进镜像。

### 2.2 选择宿主的截图工作目录（可选）

默认挂载 `./solver-data`。想换位置就设 `SOLVER_DATA_DIR`（compose 的变量插值，
从当前 shell 环境或同目录 `.env` 读取）：

```bash
# Linux / macOS
SOLVER_DATA_DIR=/srv/online-test-solver docker compose up -d --build
```

```powershell
# Windows PowerShell（路径可用正斜杠）
$env:SOLVER_DATA_DIR = 'D:/OnlineTestData/solver'
docker compose up -d --build
```

容器启动后会在该目录下创建 `Screenshots/`、`processed/`、`solutions/`。

### 2.3 构建并启动

```bash
docker compose up -d --build
```

首次构建通常需要几分钟（装 npm 依赖 + Python 依赖）。
构建成功后访问：<http://localhost:8000>

### 2.4 验证

```bash
# 健康检查（该端点不做任何外部 API 调用，适合探活）
curl http://localhost:8000/api/health     # Windows: Invoke-RestMethod http://localhost:8000/api/health

# 容器与健康状态
docker compose ps
```

`/api/health` 返回示例：

```json
{
  "status": "ok",
  "version": "…",
  "vision_configured": true,
  "solver_providers": ["deepseek"],
  "keys_configured": {"deepseek": true}
}
```

`vision_configured: false` 或某个 provider 为 `false`，说明 `.env` 里对应密钥没生效。

### 2.5 目录属主（Linux 宿主必读）

容器内以 **非 root** 用户运行，固定 `UID/GID = 10001`。绑定挂载（bind mount）时
容器的属主检查作用在**宿主目录**上，所以宿主目录必须让 UID 10001 可写：

```bash
mkdir -p webapp/data webapp/uploads webapp/solutions webapp/cache solver-data
sudo chown -R 10001:10001 webapp/data webapp/uploads webapp/solutions webapp/cache solver-data
```

不这样做时典型报错是 `sqlite3.OperationalError: unable to open database file`
或上传/写解答失败。Windows + Docker Desktop 一般不需要处理属主；若确实遇到权限问题，
可在 `docker-compose.yml` 的 `app` 服务下临时加 `user: "0:0"`（不推荐长期使用）。

---

## 3. 日常运维

### 3.1 查看日志

```bash
docker compose logs -f                 # 跟随全部日志
docker compose logs -f --tail=200 app  # 最近 200 行
docker compose logs --since 30m app    # 最近 30 分钟
```

`docker-compose.yml` 已配置 `json-file` 滚动日志（单文件 10 MB、保留 3 个），
不会无限增长。

### 3.2 停止 / 重启 / 更新

```bash
docker compose stop            # 停止容器，保留容器与卷
docker compose start           # 再次启动
docker compose restart         # 重启
docker compose down            # 停止并删除容器（绑定挂载的数据不受影响）

# 改了代码后重新构建并滚动替换
docker compose up -d --build
```

只改了 `.env` 时不需要重新构建：

```bash
docker compose up -d --force-recreate
```

### 3.3 进入容器排查

```bash
docker compose exec app sh
docker compose exec app python -c "import problem_solver_agent.config as c; print(c.ROOT_DIR, c.MONITOR_DIR)"
```

---

## 4. 数据备份与恢复

### 4.1 需要备份的内容

全部都在宿主目录里（绑定挂载），没有需要 `docker volume` 命令导出的命名卷：

| 宿主路径 | 容器内路径 | 内容 | 重要性 |
| --- | --- | --- | --- |
| `./webapp/data/tasks.db` | `/app/webapp/data/tasks.db` | **任务记录 + 账号/额度（SQLite）** | 必须备份 |
| `./webapp/uploads/` | `/app/webapp/uploads/` | 用户上传的原图 | 建议备份 |
| `./webapp/solutions/` | `/app/webapp/solutions/` | 生成的解答 Markdown | 建议备份 |
| `./webapp/cache/` | `/app/webapp/cache/` | 图片预处理缓存 | 可不备份（会自动重建） |
| `${SOLVER_DATA_DIR:-./solver-data}/Screenshots/` | `/data/Screenshots/` | 自动截图来源 | 按需 |
| `${SOLVER_DATA_DIR:-./solver-data}/solutions/` | `/data/solutions/` | 监控流水线的产物 | 建议备份 |
| `${SOLVER_DATA_DIR:-./solver-data}/processed/` | `/data/processed/` | 已处理截图归档 | 按需 |
| 宿主 `.env` | ——（通过 `env_file` 注入） | API 密钥与配置 | 必须单独保存 |

### 4.2 备份

```bash
# 停服务后整目录拷贝，SQLite 不会出现写到一半的文件
docker compose stop
tar -czf backup-$(date +%Y%m%d).tar.gz webapp/data webapp/uploads webapp/solutions solver-data .env
docker compose start
```

```powershell
# Windows PowerShell
docker compose stop
Compress-Archive -Path webapp\data, webapp\uploads, webapp\solutions, solver-data, .env `
  -DestinationPath "backup-$(Get-Date -Format yyyyMMdd).zip"
docker compose start
```

想不停机热备份，用 SQLite 自带的一致性备份：

```bash
docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/app/webapp/data/tasks.db'); d=sqlite3.connect('/app/webapp/data/backup.db'); s.backup(d); d.close(); s.close()"
```

### 4.3 恢复

```bash
docker compose down
# 把备份解回原位置，覆盖 webapp/data、webapp/uploads、webapp/solutions、solver-data
docker compose up -d
```

恢复后确认属主（Linux）：`sudo chown -R 10001:10001 webapp/data ...`。

---

## 5. 常见问题

### 5.1 端口 8000 被占用

现象：`docker compose up` 报 `Bind for 0.0.0.0:8000 failed: port is already allocated`。

做法：

1. 查占用者：Linux/macOS `lsof -i :8000`；Windows `netstat -ano | findstr :8000`。
2. 换端口。**必须同时改两处**，否则容器内仍监听 8000、映射却指向别的端口：

```yaml
# docker-compose.yml
ports:
  - "18000:8000"      # 只改冒号左边（宿主侧）
```

宿主机侧端口只是映射，容器内保持 8000 即可；`PORT` 环境变量只在你想改**容器内**监听端口时
才需要同步修改（例如把 `ports` 写成 `"18000:9000"` 并把 `PORT` 设为 `9000`）。
若在本机开发（非容器）运行，可用 `python run_web.py 9000`，或设置 `PORT=9000`。

### 5.2 Windows 上挂载路径怎么写

- 分两类变量，别混：

  | 变量 | 给谁用 | 写法 |
  | --- | --- | --- |
  | `SOLVER_DATA_DIR` | compose 做**宿主路径**插值 | 宿主路径，如 `D:/OnlineTestData/solver` |
  | `SOLVER_ROOT_DIR` | 容器内应用 | 固定 `/data`，不要改成宿主路径 |

- 反斜杠要转义或改用正斜杠，推荐正斜杠：`D:/OnlineTestData/solver`；
  含空格、中文的路径用引号包住：`SOLVER_DATA_DIR="D:/我的 数据/solver"`。
- 只从 `C:\Users\...` 之外的盘符挂载时，需要在 Docker Desktop →
  Settings → Resources → File sharing 里把该盘符加入共享。
- 绑定挂载的是 `D:\...` 的路径时，注意 WSL2 跨盘访问性能较低；截图目录频繁读写时建议放在
  WSL2 文件系统内或用命名卷。
- 相对路径（如 `./solver-data`）相对的是 `docker-compose.yml` 所在目录，即项目根目录。

### 5.3 截图目录看不到内容

按顺序排查：

1. **确认容器内实际路径**：

   ```bash
   docker compose exec app python -c "import problem_solver_agent.config as c; print(c.ROOT_DIR, c.MONITOR_DIR, c.MONITOR_DIR.exists())"
   ```

   应输出 `/data /data/Screenshots True`。
2. **确认宿主目录里放的是截图，而不是放到了旧位置**。历史上不设 `SOLVER_ROOT_DIR` 时，
   目录会建在项目的**父目录**（例如 `D:\Users\wzw\Pictures\Screenshots`，见
   `problem_solver_agent/config.py` 的 `_resolve_root_dir`）。容器里已固定成 `/data`，
   所以老的 `Screenshots/` 不会被监控。
3. **确认挂载生效**：`docker compose exec app ls -la /data` 与宿主目录对比；
   若宿主 `solver-data` 是空的而容器里也空，说明你放截图放错了宿主目录
   （确认 `SOLVER_DATA_DIR` 的值：`docker compose config | findstr /i solver-data`）。
4. **确认自动导入没被关掉**：`.env` 里 `AUTO_IMPORT_ENABLED=false` 会停止导入。
5. **确认文件扩展名**在允许集合内：`.png/.jpg/.jpeg/.bmp/.webp`
   （`webapp/config.py` 的 `ALLOWED_EXTENSIONS`）。
6. **权限**：宿主 `Screenshots/` 目录需要 UID 10001 可写（watchdog 会移动文件到 `processed/`），
   参见 2.5。

### 5.4 时区问题

- 镜像内 `TZ=Asia/Shanghai`，`docker-compose.yml` 也显式设置了 `TZ`，
  并已安装 `tzdata`，日志时间应为北京时间。
- 若日志仍是 UTC：确认容器内的值 `docker compose exec app date`；
  compose 的 `environment.TZ` 会覆盖 Dockerfile 的 `ENV TZ`，改那里即可。
- 时区只影响**日志显示**，不影响业务数据：数据库里的时间戳是 Unix 秒
  （`webapp/models.py` 用 `time.time()`），跨时区不会错乱。
- Windows 宿主与容器时区不一致时，绑定挂载目录里的**文件修改时间**以容器时区解释，
  排查“截图分组超时”问题时注意这一点。

### 5.5 前端页面白屏 / 404

- 先确认静态产物存在：`docker compose exec app ls /app/webapp/static/index.html`。
- 访问根路径 `http://localhost:8000/`，不要直接访问 `/static/index.html`：
  应用使用 `base: '/static/'` 构建，SPA 路由（`/task/:id` 等）依赖后端的 fallback。
- 若页面报“前端尚未构建”，说明镜像里 `webapp/static` 为空——
  重新 `docker compose build --no-cache app`，并检查 `.dockerignore` 是否误排除了
  `frontend/` 源码（本仓库的 `.dockerignore` 只排除 `frontend/node_modules`、`frontend/dist`）。

### 5.6 健康检查一直 unhealthy

- `docker compose ps` 看状态，`docker compose logs app` 看启动异常。
- `start_period: 20s` 内不会计入失败次数；启动更慢的机器可适当调大。
- 手动探测：`docker compose exec app python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read())"`。
- 注意 `/api/health` 只反映进程存活与密钥是否配置，**不会**真的调用外部 API，
  所以它 healthy 不代表大模型网络可达。

---

## 6. 关于外部依赖：为什么要刻意“少”

本项目的持久化是**单文件 SQLite**（`webapp/data/tasks.db`），任务调度是**进程内线程**，
事件推送是**进程内 SSE 总线**。因此编排里只有一个服务，且明确**不包含**以下组件：

| 未引入的组件 | 原因 |
| --- | --- |
| PostgreSQL / MySQL | `webapp/models.py`、`webapp/accounts.py` 直接用 Python 内置 `sqlite3` 打开 `webapp/data/tasks.db`，代码里没有任何数据库驱动与连接串配置；引入外部数据库需要改代码并做数据迁移，部署上没有任何收益 |
| Redis | 任务状态在 `webapp/jobs.py` 的 `TaskRegistry`、事件在 `webapp/routes.py` 的 `TaskEventBus`，都是进程内对象；单容器单进程模型下没有跨进程共享状态的需求。只有在要跑多个副本做负载均衡时才需要它 |
| Celery / RabbitMQ 等消息队列 | 解题流水线用线程池在进程内调度（`webapp/pipeline.py`、`problem_solver_agent/image_grouper.py`、`MAX_CONCURRENT_TASKS`），任务本身是“提交→流式增量→落库”的单进程流程 |
| LiteLLM 等模型网关 | 代码通过 `openai` SDK 直连各家 OpenAI 兼容接口（`problem_solver_agent/solver_client.py`、`vision_client.py`），provider 与密钥由 `.env` 的 `{PROVIDER}_API_KEY` 决定，无需中间代理 |
| Nginx / 反向代理容器 | FastAPI 同时提供 API 与静态资源，单机自用场景下 uvicorn 直接监听 8000 即可。若需要 HTTPS、域名或统一入口，再在容器前加一层反代 |
| 单独的前端容器 | 前端是构建期产物（见文首说明），运行期不需要 Node 进程 |

这样做的直接好处：备份就是拷目录（见第 4 节）、部署没有组件依赖顺序、
机器上少几个常驻进程。

**将来什么情况下需要重新评估？** 如果要同时跑多个 `app` 副本（水平扩容），
`TaskRegistry`/`TaskEventBus` 的进程内假设和 SQLite 的单写者特性都会成为瓶颈，
那时才需要引入 Redis（共享状态）和 PostgreSQL（并发写），
并相应改造 `webapp/models.py`。

---

## 7. 附：从零到可用的最短路径

```bash
cp .env.example .env          # 填入 DEEPSEEK_API_KEY、ZHIPU_API_KEY
mkdir -p webapp/data webapp/uploads webapp/solutions webapp/cache solver-data
sudo chown -R 10001:10001 webapp/data webapp/uploads webapp/solutions webapp/cache solver-data  # Linux
docker compose up -d --build
docker compose ps             # 等到 healthy
# 浏览器打开 http://localhost:8000
```
