@echo off
REM Windows文件同步服务启动脚本
REM 后台静默运行（不显示窗口）

REM 获取脚本所在目录
cd /d "%~dp0"

REM 检查Python是否安装（静默检查）
pythonw --version >nul 2>&1
if errorlevel 1 (
    REM 如果 pythonw 不可用，尝试 python
    python --version >nul 2>&1
    if errorlevel 1 (
        REM Python未安装，静默退出（可以记录到日志文件）
        exit /b 1
    )
    set "PYTHON_CMD=python"
) else (
    set "PYTHON_CMD=pythonw"
)

REM 检查配置文件（静默检查）
if not exist "config.json" (
    REM 如果配置文件不存在，静默退出
    exit /b 1
)

REM 检查主程序文件（静默检查）
if not exist "sync_to_nas.py" (
    REM 如果主程序不存在，静默退出
    exit /b 1
)

REM 使用 pythonw 启动Python脚本（完全不显示窗口）
REM 如果 pythonw 不可用，使用 python 并最小化窗口运行
if "%PYTHON_CMD%"=="pythonw" (
    REM 使用 pythonw，完全静默
    start "" "%PYTHON_CMD%" "%~dp0sync_to_nas.py"
) else (
    REM 使用 python，最小化窗口运行
    start /min "" "%PYTHON_CMD%" "%~dp0sync_to_nas.py"
)

REM 静默退出，不显示任何提示
exit /b 0

