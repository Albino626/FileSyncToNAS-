@echo off
chcp 65001 >nul
echo ========================================
echo 文件同步工具 - 安装程序
echo ========================================
echo.

cd /d "%~dp0"

REM 检查 Python 是否安装
echo [1/3] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo     错误: 未检测到 Python
    echo     请先安装 Python 3.7 或更高版本
    echo     下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo     Python 环境正常

REM 安装依赖
echo [2/3] 安装 Python 依赖包...
if not exist "requirements.txt" (
    echo     警告: 未找到 requirements.txt 文件
    pause
    exit /b 1
)
pip install -r requirements.txt
if errorlevel 1 (
    echo     警告: 依赖安装可能失败，请检查网络连接
    pause
    exit /b 1
)
echo     依赖包安装完成

REM 初始化配置文件
echo [3/3] 初始化配置文件...
if not exist "config.json" (
    if exist "config.json.example" (
        copy "config.json.example" "config.json" >nul
        echo     已从模板创建配置文件: config.json
        echo     请编辑 config.json 文件，填写您的 NAS 连接信息
    ) else (
        echo     警告: 未找到配置文件模板
    )
) else (
    echo     配置文件已存在，跳过初始化
)

echo.
echo ========================================
echo 安装完成！
echo ========================================
echo.
echo 下一步:
echo   1. 编辑 config.json 文件，填写您的配置信息
echo   2. 双击 start_sync.vbs 启动同步服务
echo.
pause

