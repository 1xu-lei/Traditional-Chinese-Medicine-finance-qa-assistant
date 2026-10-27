@echo off
chcp 65001 >nul
echo ========================================
echo   环境诊断
echo ========================================
echo.

echo 1. 检查当前Python
echo.
where python
echo.

echo 2. 检查Python版本
echo.
python --version
echo.

echo 3. 检查torch安装
echo.
python -c "import torch; print('Torch:', torch.__file__, torch.__version__)"
echo.

echo 4. 检查pytorch_cpu环境的torch
echo.
C:\Users\14725\.conda\envs\pytorch_cpu\python.exe -c "import torch; print('Torch:', torch.__file__, torch.__version__)"
echo.

echo 5. 尝试加载Agent
echo.
C:\Users\14725\.conda\envs\pytorch_cpu\python.exe -c "import sys; sys.path.insert(0, r'd:\python-leanrn\codex'); from agent.agent import FinanceAgent; print('Agent OK')"
echo.

pause
