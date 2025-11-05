@echo off
REM 手动触发同步测试

cd /d "%~dp0"

echo 正在运行手动同步测试...
echo.

python manual_sync.py

