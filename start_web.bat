@echo off
chcp 65001 >nul
title 自动化解题 Agent

echo ============================================
echo   自动化解题 Agent — 一键启动
echo ============================================
echo.

cd /d "%~dp0"

:: ---- 检查 Python ----
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.9+
    pause
    exit /b 1
)

:: ---- 检查 .env 文件 ----
if not exist ".env" (
    echo [警告] 未找到 .env 文件！
    echo   请在项目根目录创建 .env 文件，配置以下 API Key:
    echo     DEEPSEEK_API_KEY=sk-...
    echo     ZHIPU_API_KEY=...
    echo.
)

:: ---- 检查/安装 Python 依赖 ----
if not exist ".deps_ok" (
    echo [1/3] 安装 Python 依赖...
    pip install -r requirements.txt -q
    if %errorlevel% neq 0 (
        echo [错误] Python 依赖安装失败
        pause
        exit /b 1
    )
    type nul > .deps_ok
    echo   Python 依赖安装完成
) else (
    echo [1/3] Python 依赖已安装，跳过
)

:: ---- 检查/构建前端 ----
if exist "frontend\package.json" (
    echo [2/3] 检查前端构建...

    :: 检查 Node.js
    node --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo [警告] 未找到 Node.js，跳过前端构建
        echo   后端启动后将从 webapp/static/ 读取静态文件
        goto :start_server
    )

    pushd frontend

    :: 安装前端依赖
    if not exist "node_modules" (
        echo   安装前端依赖 (npm install)...
        call npm install
        if %errorlevel% neq 0 (
            popd
            echo [警告] 前端依赖安装失败，跳过前端构建
            goto :start_server
        )
    )

    :: 构建前端
    if not exist "..\webapp\static\index.html" (
        echo   构建前端 (npm run build)...
        call npm run build
        if %errorlevel% neq 0 (
            popd
            echo [警告] 前端构建失败，后端仍可通过 API 工作
            goto :start_server
        )
        echo   前端构建完成 → webapp/static/
    ) else (
        echo   前端已构建，跳过
    )

    popd
) else (
    echo [2/3] 未找到前端项目，跳过
)

:start_server
:: ---- 启动后端 ----
echo [3/3] 启动 Web 服务...
echo.
echo ============================================
echo   服务启动中，请在浏览器中打开:
echo   http://localhost:8000
echo.
echo   手机访问（同一局域网）:
echo   请查看终端输出的二维码地址
echo ============================================
echo.
echo 按 Ctrl+C 停止服务
echo.

python run_web.py

pause
