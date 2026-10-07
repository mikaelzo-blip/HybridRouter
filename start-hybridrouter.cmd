@echo off
setlocal
cd /d "%~dp0"
title HybridRouter Proxy (Port 20250)

echo ===================================================
echo Starting HybridRouter 3-Tier Multi-Model Proxy
echo Port: 20250 ^| Host: 127.0.0.1
echo Upstream Antigravity Target: 127.0.0.1:20128
echo ===================================================

where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    uv run uvicorn src.server.app:create_app --factory --port 20250 --host 127.0.0.1 >> router.log 2>&1
) else if exist ".venv\Scripts\uvicorn.exe" (
    ".venv\Scripts\uvicorn.exe" src.server.app:create_app --factory --port 20250 --host 127.0.0.1 >> router.log 2>&1
) else (
    echo [ERROR] Neither 'uv' nor '.venv\Scripts\uvicorn.exe' was found!
    echo Please install dependencies first with 'uv sync'.
    pause
)
