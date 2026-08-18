@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" goto use_venv

where py >nul 2>&1
if not errorlevel 1 goto use_py

where python >nul 2>&1
if errorlevel 1 goto no_python

python setup_env.py %*
if errorlevel 1 exit /b 1
exit /b 0

:use_venv
"%~dp0.venv\Scripts\python.exe" "%~dp0setup_env.py" %*
if errorlevel 1 exit /b 1
exit /b 0

:use_py
py -3 setup_env.py %*
if errorlevel 1 exit /b 1
exit /b 0

:no_python
echo [错误] 未找到 Python，请先安装 Python 3.10+
exit /b 1
