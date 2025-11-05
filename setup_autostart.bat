@echo off
REM 设置开机自启动
REM 需要管理员权限运行

echo ========================================
echo Windows文件同步服务 - 开机自启动设置
echo ========================================
echo.

REM 检查管理员权限
net session >nul 2>&1
if errorlevel 1 (
    echo 错误: 需要管理员权限才能设置开机自启动
    echo 请右键点击此文件，选择"以管理员身份运行"
    pause
    exit /b 1
)

REM 获取脚本所在目录（完整路径）
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM 检查启动脚本（优先使用start_portable.bat，如果没有则使用start_sync.bat）
if exist "%SCRIPT_DIR%\start_portable.bat" (
    set "START_SCRIPT=%SCRIPT_DIR%\start_portable.bat"
) else (
    set "START_SCRIPT=%SCRIPT_DIR%\start_sync.bat"
)

REM 创建启动项
set "REG_KEY=HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
set "REG_NAME=FileSyncToNAS"
set "REG_VALUE=%START_SCRIPT%"

REM 写入注册表
reg add "%REG_KEY%" /v "%REG_NAME%" /t REG_SZ /d "%REG_VALUE%" /f >nul 2>&1

if errorlevel 1 (
    echo 设置开机自启动失败！
    pause
    exit /b 1
)

echo 开机自启动设置成功！
echo.
echo 启动项名称: %REG_NAME%
echo 启动命令: %REG_VALUE%
echo.
echo 如需移除自启动，请运行 remove_autostart.bat
echo.
pause

