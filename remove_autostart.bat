@echo off
REM 移除开机自启动

echo ========================================
echo 移除开机自启动
echo ========================================
echo.

REM 检查管理员权限
net session >nul 2>&1
if errorlevel 1 (
    echo 错误: 需要管理员权限才能移除开机自启动
    echo 请右键点击此文件，选择"以管理员身份运行"
    pause
    exit /b 1
)

set "REG_KEY=HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
set "REG_NAME=FileSyncToNAS"

REM 删除注册表项
reg delete "%REG_KEY%" /v "%REG_NAME%" /f >nul 2>&1

if errorlevel 1 (
    echo 移除开机自启动失败或启动项不存在
) else (
    echo 开机自启动已移除
)

echo.
pause

