@echo off
chcp 65001 >nul
title PostgreSQL MCP Autonomous — Gradio UI

cd /d "%~dp0"

if not exist ".venv" (
    echo Virtual environment not found. Run setup first.
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause & exit /b 1
)

call .venv\Scripts\activate.bat
echo Starting PostgreSQL MCP Autonomous...
echo Open http://127.0.0.1:7862 in your browser
echo.
python -m src.postgres_mcp.autonomous.app
pause
