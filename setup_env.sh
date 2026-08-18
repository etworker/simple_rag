#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    if [ "$PYTHON_BIN" = "python3" ] && command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    else
        echo "[错误] 未找到 Python，请先安装 Python 3.10+" >&2
        exit 1
    fi
fi

exec "$PYTHON_BIN" setup_env.py "$@"
