@echo off
rem 双击启动视频分析服务（使用项目 .venv 的 Python）
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py
) else (
    echo 未找到 .venv，请先创建虚拟环境：
    echo   python -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt
)
echo.
echo 服务已停止（出错信息在上方）。
pause
