@echo off
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m src.postgres_mcp.autonomous.app
