@echo off
chcp 65001 >nul
title Auto Problem Solver
setlocal

rem ---------------------------------------------------------------------------
rem 视觉层密钥检查的说明（放在括号块之外的注释里：CMD 把多行 if/for 括号块当作
rem 一个整体解析，块内的中文全角标点与引号在解析期就可能把块拆坏）。
rem
rem 本脚本**按 provider 判定**所需密钥：当前安全基线（代码默认）是 zhipu，
rem 但本仓库 .env 通常显式写着 VISION_PROVIDER=deepseek（迁移目标，视觉与求解
rem 共用 DEEPSEEK_API_KEY）。只有需要回退到 GLM-4.6V 时才用 ZHIPU_API_KEY。
rem 旧版本写死 ZHIPU_API_KEY，在 deepseek 配置下会误报"密钥缺失"。
rem
rem 另一个坑：括号块内的 %VAR% 在**解析期**就展开，块里刚 set 的值在同一块里读不到。
rem 因此下面改用 goto 标签而不是大块 if/else。
rem ---------------------------------------------------------------------------

echo ============================================
echo   Auto Problem Solver - Quick Start
echo ============================================
echo.

cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"

echo [1/4] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found, please install Python 3.10+
    echo.
    echo Press any key to exit...
    pause >nul
    exit /b 1
)
python --version

echo.
echo [2/4] Checking config...
if exist ".env" goto config_check_key
if not exist ".env.example" goto no_env_at_all
echo [WARNING] .env file not found!
rem 这里**故意不自动 copy**：历史上一次 `copy /y .env.example .env` 把用户填好的
rem 密钥整份覆盖掉了（.env 不在 git 里，丢了就找不回来）。缺失时只打印指引，
rem 由用户自己决定怎么建 —— 少一步便利，换掉一个不可逆的事故面。
echo   .env is missing. Create it yourself:
echo     copy .env.example .env
echo   then fill in the key required by VISION_PROVIDER:
echo     VISION_PROVIDER=deepseek ^(migration target^) -^> DEEPSEEK_API_KEY=sk-...
echo     VISION_PROVIDER=zhipu ^(code default / fallback^) -^> ZHIPU_API_KEY=...
echo   DEEPSEEK_API_KEY is shared with the solver, so it is required either way.
echo.
echo Press any key to exit...
pause >nul
exit /b 1

:no_env_at_all
echo [WARNING] .env file not found!
echo   Please create .env with:
echo     VISION_PROVIDER=deepseek
echo     DEEPSEEK_API_KEY=sk-...
echo   ^(set VISION_PROVIDER=zhipu + ZHIPU_API_KEY to fall back to GLM-4.6V^)
echo.
goto deps

:config_check_key
rem 与 config.DEFAULT_VISION_PROVIDER 保持一致（安全基线 zhipu）：
rem .env 里没写 VISION_PROVIDER 时，代码也用 zhipu，两边不能各说各话
set "VISION_PROVIDER=zhipu"
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="VISION_PROVIDER" set "VISION_PROVIDER=%%B"
)
set "VISION_KEY_ENV=DEEPSEEK_API_KEY"
if /i "%VISION_PROVIDER%"=="zhipu" set "VISION_KEY_ENV=ZHIPU_API_KEY"
echo   .env file OK
echo   Vision provider: %VISION_PROVIDER% ^(required key: %VISION_KEY_ENV%^)
findstr /b /c:"%VISION_KEY_ENV%=" ".env" >nul 2>&1
if %errorlevel% equ 0 goto deps
echo   [WARNING] %VISION_KEY_ENV% is not set in .env - visual steps will fail

:deps
echo.
echo [3/4] Checking dependencies...
rem 只在真正缺包时才调用 pip，避免每次启动都重装一遍
python -c "import importlib,sys;mods=('openai','PIL','watchdog','dotenv','fastapi','uvicorn','multipart','qrcode');missing=[m for m in mods if importlib.util.find_spec(m) is None];sys.exit(1 if missing else 0)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Missing packages detected, installing...
    python -m pip install -r requirements.txt -q
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies
        echo.
        echo Press any key to exit...
        pause >nul
        exit /b 1
    )
    echo   Dependencies installed
) else (
    echo   Dependencies OK
)

echo.
echo [4/4] Checking frontend build...
if exist "webapp\static\index.html" (
    echo   Frontend build OK
) else (
    echo   [WARNING] Frontend not built - the web page will not load!
    echo   Run these commands once:
    echo     cd frontend ^&^& npm install ^&^& npm run build
    echo   Continuing with API only...
)

echo.
echo ============================================
echo   Starting server...
echo.
echo   Open in browser: http://localhost:8000
echo   (startup log below shows the actual working directories)
echo.
echo   Press Ctrl+C to stop
echo ============================================
echo.

python run_web.py

echo.
echo Server stopped, press any key to exit...
pause >nul
