@echo off
chcp 65001 >nul
title PostgreSQL MCP Autonomous — MCP Server

cd /d "%~dp0"

if not exist ".venv" (
    echo Virtual environment not found. Run setup first.
    pause & exit /b 1
)

call .venv\Scripts\activate.bat
echo Starting PostgreSQL MCP Server (stdio)...
echo Configure your MCP client with:
echo   command: python
echo   args: -m src.postgres_mcp.autonomous.mcp_server
echo.
python -m src.postgres_mcp.autonomous.mcp_server
pause
