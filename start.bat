@echo off
chcp 65001 >nul
echo ========================================
echo   RAG 文档问答系统 - 启动脚本 (Windows)
echo ========================================
echo.

cd /d "%~dp0"

REM 检查 uv 是否可用
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 uv，请先安装: https://docs.astral.sh/uv/
    pause
    exit /b 1
)

REM 同步依赖
echo [1/3] 同步依赖...
cd rag_demo
uv sync
if %errorlevel% neq 0 (
    echo [错误] 依赖同步失败
    pause
    exit /b 1
)

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
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
