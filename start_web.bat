@echo off
chcp 65001 >nul
title Auto Problem Solver

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
if not exist ".env" (
    echo [WARNING] .env file not found!
    if exist ".env.example" (
        echo   Copying .env.example -^> .env
        copy /y ".env.example" ".env" >nul
        echo   Please edit .env and fill in DEEPSEEK_API_KEY / ZHIPU_API_KEY
        echo.
        echo Press any key to exit...
        pause >nul
        exit /b 1
    )
    echo   Please create .env with:
    echo     DEEPSEEK_API_KEY=sk-...
    echo     ZHIPU_API_KEY=...
    echo.
) else (
    echo   .env file OK
)

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
