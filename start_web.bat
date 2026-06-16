@echo off
chcp 65001 >nul
title 自动化解题 Agent

echo ============================================
echo   自动化解题 Agent — 一键启动
echo ============================================
echo.

cd /d "%~dp0"

:: ---- 尝试激活 Conda 环境 ----
:: 优先尝试 pytorch_271_env（用户配置），不存在则 fallback 到 llm
call conda activate pytorch_271_env 2>nul
if errorlevel 1 (
    echo [*] Conda 环境 "pytorch_271_env" 不存在，尝试 "llm"...
    call conda activate llm 2>nul
    if errorlevel 1 (
        echo [*] Conda 不可用，尝试直接使用 llm 环境的 Python...
        set PYTHON=E:\ProgramData\anaconda3\envs\llm\python.exe
        if not exist "%PYTHON%" (
            echo [错误] 找不到 Conda 环境或 Python 解释器！
            echo   请先手动激活 Conda 环境后运行：conda activate llm ^&^& run_web.py
            pause
            exit /b 1
        )
        goto :env_ok
    ) else (
        echo [✓] 已激活 Conda 环境: llm
    )
) else (
    echo [✓] 已激活 Conda 环境: pytorch_271_env
)
set PYTHON=python
goto :env_ok

:env_skip_conda
:: 直接路径 fallback
if exist "E:\ProgramData\anaconda3\envs\llm\python.exe" (
    set PYTHON=E:\ProgramData\anaconda3\envs\llm\python.exe
    echo [✓] 直接使用 Python: %PYTHON%
    goto :env_ok
)
echo [错误] 找不到可用 Python
pause
exit /b 1

:env_ok

:: ---- 检查 Python ----
%PYTHON% --version
if errorlevel 1 (
    echo [错误] Python 不可用
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
    %PYTHON% -m pip install -r requirements.txt -q
    if errorlevel 1 (
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
    if errorlevel 1 (
        echo [警告] 未找到 Node.js，跳过前端构建
        echo   后端启动后将从 webapp/static/ 读取静态文件
        goto :start_server
    )

    pushd frontend

    :: 安装前端依赖
    if not exist "node_modules" (
        echo   安装前端依赖 (npm install)...
        call npm install
        if errorlevel 1 (
            popd
            echo [警告] 前端依赖安装失败，跳过前端构建
            goto :start_server
        )
    )

    :: 构建前端
    if not exist "..\webapp\static\index.html" (
        echo   构建前端 (npm run build)...
        call npm run build
        if errorlevel 1 (
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
echo   服务启动中，即将自动打开浏览器...
echo   若未自动打开，请手动访问 http://localhost:8000
echo   按 Ctrl+C 停止服务
echo ============================================
echo.

%PYTHON% run_web.py

pause
