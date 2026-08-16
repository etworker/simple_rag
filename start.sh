#!/usr/bin/env bash
set -e

echo "========================================"
echo "  RAG 文档问答系统 - 启动脚本 (Linux/Mac)"
echo "========================================"
echo

cd "$(dirname "$0")"

# 检查 uv 是否可用
if ! command -v uv &> /dev/null; then
    echo "[错误] 未找到 uv，请先安装: https://docs.astral.sh/uv/"
    exit 1
fi

# 同步依赖
echo "[1/3] 同步依赖..."
cd rag_demo
uv sync

# 加载环境变量（CRLF 兼容：Windows 编辑的 .env 行尾带 \r，直接 source 会报错/污染 token）
_load_env() {
    local env_file="$1"
    if [ ! -f "$env_file" ]; then
        return 1
    fi
    # 去除行尾回车后 source（临时文件，避免 process substitution 兼容性问题）
    local clean_file
    clean_file="$(mktemp)"
    tr -d '\r' < "$env_file" > "$clean_file"
    set -a
    source "$clean_file"
    set +a
    rm -f "$clean_file"
    return 0
}

if [ -f ".env" ]; then
    echo "[2/3] 加载 .env 环境变量..."
    _load_env ".env" || echo "[warn] .env 加载失败"
elif [ -f "../.env" ]; then
    echo "[2/3] 加载上级目录 .env 环境变量..."
    _load_env "../.env" || echo "[warn] .env 加载失败"
else
    echo "[2/3] 未找到 .env 文件，跳过（请确保环境变量已配置）"
fi
unset -f _load_env

# 设置离线模式（避免 HuggingFace 联网）
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

# 启动服务
echo "[3/3] 启动服务..."
echo
echo "  访问地址: http://localhost:8000"
echo "  API 文档: http://localhost:8000/docs"
echo "  按 Ctrl+C 停止"
echo
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
