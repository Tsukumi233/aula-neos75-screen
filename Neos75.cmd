@echo off
chcp 65001 >nul
rem Neos75 屏幕：启动桌面应用（连接真实键盘屏幕）。第一次请先运行 setup.ps1。
set "APP=%~dp0app"
set "ELECTRON=%APP%\node_modules\electron\dist\electron.exe"
if not exist "%ELECTRON%" (
    echo 还没有安装：请先右键 setup.ps1 选择“使用 PowerShell 运行”。
    pause
    exit /b 1
)
if not exist "%~dp0sdk\.venv\Scripts\python.exe" (
    echo 还没有安装 Python 环境：请先运行 setup.ps1。
    pause
    exit /b 1
)
start "" "%ELECTRON%" "%APP%"
