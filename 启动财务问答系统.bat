@echo off
chcp 65001 >nul

echo ========================================
echo   财务问答系统启动中...
echo ========================================
echo.

REM 设置环境变量，确保使用pytorch_cpu的所有依赖
set PYTHONPATH=d:\python-leanrn\codex
set PYTHONHOME=C:\Users\14725\.conda\envs\pytorch_cpu
set CONDA_DEFAULT_ENV=pytorch_cpu
set VIRTUAL_ENV=

REM 优先使用pytorch_cpu的DLL
set PATH=C:\Users\14725\.conda\envs\pytorch_cpu;C:\Users\14725\.conda\envs\pytorch_cpu\Scripts;C:\Users\14725\.conda\envs\pytorch_cpu\Library\bin;%PATH%

echo 环境配置完成
echo Python路径: %PYTHONHOME%
echo.

REM 启动应用
C:\Users\14725\.conda\envs\pytorch_cpu\python.exe app.py

pause
