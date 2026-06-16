@echo off
chcp 65001 >nul
title Auto Problem Solver

echo ============================================
echo   Auto Problem Solver - Quick Start
echo ============================================
echo.

cd /d "%~dp0"

echo [1/4] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found, please install Python 3.9+
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
    echo   Please create .env file with:
    echo     DEEPSEEK_API_KEY=sk-...
    echo     ZHIPU_API_KEY=...
    echo.
) else (
    echo   .env file OK
)

echo.
echo [3/4] Checking dependencies...
if not exist ".deps_ok" (
    echo   Installing dependencies...
    pip install -r requirements.txt -q
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies
        echo.
        echo Press any key to exit...
        pause >nul
        exit /b 1
    )
    type nul > .deps_ok
    echo   Dependencies installed
) else (
    echo   Dependencies OK
)

echo.
echo [4/4] Checking frontend...
if exist "webapp\static\index.html" (
    echo   Frontend OK
) else (
    echo   Frontend not found, API only
)

echo.
echo ============================================
echo   Starting server...
echo.
echo   Open in browser: http://localhost:8000
echo.
echo   Press Ctrl+C to stop
echo ============================================
echo.

python run_web.py

echo.
echo Server stopped, press any key to exit...
pause >nul
