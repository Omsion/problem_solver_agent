# ==============================================================================
# 自动化解题 Agent —— 生产镜像（多阶段构建）
#
# 架构说明：
#   阶段 1 (frontend-builder)  node 镜像里编译 React 前端
#                             vite.config.ts 的 build.outDir = ../webapp/static
#                             => 产物落在 /build/webapp/static
#   阶段 2 (python-deps)       python 镜像里把 requirements.txt 装进 /opt/venv
#                             （独立成一层，源码改动不会导致重装依赖）
#   阶段 3 (runtime)           只拷贝 venv + 源码 + 阶段 1 的静态产物
#
# 前端不需要单独跑容器：构建产物由 FastAPI 的 StaticFiles 挂在 /static，
# 未匹配的 GET 请求由 app.py 的 SPA fallback 返回 index.html。
#
# 构建：
#   docker build -t online-test-agent .
# 运行（推荐用 docker compose，见 docker-compose.yml）：
#   docker run -d -p 8000:8000 --env-file .env \
#     -v "$PWD/webapp/data:/app/webapp/data" \
#     -v "$PWD/webapp/uploads:/app/webapp/uploads" \
#     -v "$PWD/webapp/solutions:/app/webapp/solutions" \
#     -v "$PWD/webapp/cache:/app/webapp/cache" \
#     -v "$PWD/solver-data:/data" \
#     online-test-agent
# ==============================================================================


# ------------------------------------------------------------------------------
# 阶段 1：构建前端静态产物
# ------------------------------------------------------------------------------
# 前端依赖要求 Node ^20.19.0 || >=22.12.0：
#   vite 8 / @vitejs/plugin-react 6 的 engines 字段即为此约束，
#   node:20-alpine 的当前 20.x 已满足（>= 20.19.0）。
FROM node:20-alpine AS frontend-builder

WORKDIR /build

# 先只拷贝依赖清单，命中 npm ci 的层缓存；源码改动不会触发重新安装依赖。
COPY frontend/package.json frontend/package-lock.json ./

# package-lock.json 是 lockfileVersion 3，用 npm ci 保证可复现安装。
# 构建需要 devDependencies（构建脚本是 `tsc -b && vite build`），所以不加 --omit=dev。
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

# 再拷贝前端其余源码（node_modules / dist 已在 .dockerignore 中排除）
COPY frontend/ ./

# 产物输出到 ../webapp/static（相对 frontend/），即 /build/webapp/static
RUN npm run build

# 构建结果自检：index.html 必须存在，否则镜像直接构建失败
RUN test -s /build/webapp/static/index.html


# ------------------------------------------------------------------------------
# 阶段 2：安装 Python 运行依赖到独立 venv
# ------------------------------------------------------------------------------
# 项目 Python 版本为 3.10（requirements.txt / webapp 代码均按 3.10 编写）
FROM python:3.10-slim AS python-deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

# 建立独立虚拟环境，便于整目录拷进最终镜像
RUN python -m venv /opt/venv

# 用虚拟环境的 pip 安装，避免落到系统 site-packages
ENV PATH="/opt/venv/bin:$PATH"

# requirements.txt 含 pywin32（仅 Windows，带 sys_platform 标记，Linux 上自动跳过），
# keyboard / pyautogui / pyperclip 虽然只在 tools/ 下的 Windows 脚本里用到，
# 但仍然被 requirements.txt 声明，这里照单全装以保证清单一致。
#
# 只装了 pip，没有装 gcc/编译链：清单里的包（Pillow、watchdog、openai、uvicorn[standard]
# 等）在 PyPI 上都有 manylinux x86_64/arm64 wheel，无需本地编译。
# 如果哪天某个包需要从源码编译（构建日志里出现 "Building wheel for ..." 后失败），
# 在这一层之前加一条：
#   RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
#    && rm -rf /var/lib/apt/lists/*
# 依赖清单单独一层（先于任何源码拷贝）：只改源码时这一层仍然命中缓存。
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install -r /tmp/requirements.txt


# ------------------------------------------------------------------------------
# 阶段 3：运行时镜像
# ------------------------------------------------------------------------------
FROM python:3.10-slim AS runtime

# 中文日志与横幅需要 UTF-8；stdout 不缓冲，否则 docker logs 看不到实时输出
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    PATH="/opt/venv/bin:$PATH"

# 时区：Debian slim 默认 UTC，日志时间会比北京时间早 8 小时。
# 需要本地时间时改这里（并保证镜像内 tzdata 已安装，见下一条 RUN），
# 也可以在 docker-compose.yml 里用 environment.TZ 覆盖。
ENV TZ=Asia/Shanghai

WORKDIR /app

# tzdata 用于 TZ 生效（Debian slim 默认不带时区库）
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata; \
    rm -rf /var/lib/apt/lists/*

# 非 root 用户。固定 UID/GID = 10001，便于宿主目录按需 chown（见 docs/DEPLOY.md）
RUN groupadd --gid 10001 appuser \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

# 运行依赖（整目录拷贝，venv 由 python -m venv 创建，可重定位）
COPY --from=python-deps --chown=appuser:appuser /opt/venv /opt/venv

# 后端源码：入口 run_web.py + 两个包。
# tools/ 只在 Windows 上手动运行（依赖 pywin32/keyboard），Web 链路不 import 它，
# 因此不进镜像；tests/ 同样不进。
COPY --chown=appuser:appuser run_web.py /app/run_web.py
COPY --chown=appuser:appuser webapp/ /app/webapp/
COPY --chown=appuser:appuser problem_solver_agent/ /app/problem_solver_agent/

# 阶段 1 的静态产物（vite 已输出成 webapp/static/ 结构）
COPY --from=frontend-builder --chown=appuser:appuser /build/webapp/static/ /app/webapp/static/

# 运行时会写入的目录：先以 appuser 身份建好，这样用镜像内的匿名卷/未挂载时也可写。
# 用 compose/run 挂载宿主目录时，宿主目录的属主才起作用（见 docs/DEPLOY.md）。
RUN set -eux; \
    mkdir -p /app/webapp/data /app/webapp/uploads /app/webapp/solutions /app/webapp/cache /data; \
    chown -R appuser:appuser /app/webapp/data /app/webapp/uploads /app/webapp/solutions /app/webapp/cache /data

# 截图监控根目录：其下会创建 Screenshots/、processed/、solutions/
# （problem_solver_agent/config.py 的 ROOT_DIR 解析顺序：SOLVER_ROOT_DIR > 项目父目录）
# 镜像内给一个安全默认值，compose 会显式覆盖成 /data。
ENV SOLVER_ROOT_DIR=/data

USER appuser

EXPOSE 8000

# 健康检查直接打 /api/health（webapp/routes.py 中该端点不做任何外部 API 调用）。
# slim 镜像没有 curl，用 Python 标准库探测，避免额外装包。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"]

# run_web.py 会通过 webapp.config.HOST/PORT 绑定 0.0.0.0:8000，
# 并用 uvicorn factory 模式加载 webapp.app:create_app（无 --reload）。
CMD ["python", "run_web.py"]
