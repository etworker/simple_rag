@echo off
chcp 65001 >nul
echo ========================================
echo   RAG 文档问答系统 - 启动脚本 (Windows)
echo ========================================
echo.

cd /d "%~dp0"

REM 创建虚拟环境、安装并校验 CPU/GPU 依赖
echo [1/3] 配置环境...
call setup_env.bat
if %errorlevel% neq 0 (
    echo [错误] 环境配置失败
    pause
    exit /b 1
)

cd rag_server

REM 加载环境变量
if exist ".env" (
    echo [2/3] 加载 .env 环境变量...
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "%%a=%%b"
    )
) else if exist "..\.env" (
    echo [2/3] 加载上级目录 .env 环境变量...
    for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do (
        set "%%a=%%b"
    )
) else (
    echo [2/3] 未找到 .env 文件，跳过（请确保环境变量已配置）
)

REM 启动服务
echo [3/3] 启动服务...
echo.
echo   访问地址: http://localhost:8000
echo   API 文档: http://localhost:8000/docs
echo   按 Ctrl+C 停止
echo.
REM 使用已同步的 workspace 虚拟环境启动，避免 uv run 再次按无 extra 状态同步
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
