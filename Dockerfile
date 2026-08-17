# syntax=docker/dockerfile:1
#
# Simple-RAG 容器化构建（用于 AWS ECR / ECS Fargate / EKS）
# 关键约定：
#   - 依赖 + embedding 模型(BAAI/bge-small-zh-v1.5) 在 build 阶段预置
#   - 运行时 HF_HUB_OFFLINE=1，不联网
#   - ~/.simple_rag -> /build/.simple_rag（持久化卷挂载点）
#   - config.json 由 Secrets Manager 注入，镜像内仅留 example 作为兜底

# ---------- build: 解析依赖 + 预下载 embedding 模型 ----------
FROM python:3.12-slim AS build
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgl1 libglib2.0-0 poppler-utils curl git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY rag_server /build/rag_server
COPY doc_parser /build/doc_parser
COPY version_diff /build/version_diff
COPY llm_chat /build/llm_chat

# 构建期需要联网拉依赖与模型
ENV HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 HF_HOME=/build/.cache/huggingface
RUN cd /build/rag_server && (uv sync --frozen --no-dev || uv sync --no-dev)
RUN cd /build/rag_server && uv run python -c \
        "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"

# ---------- runtime ----------
FROM python:3.12-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/build \
    HF_HOME=/build/.cache/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    CONFIG_PATH=/build/rag_server/config.json \
    PORT=8000
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*

# 保持与 build 阶段一致的绝对路径，使 editable 安装依然有效
COPY --from=build /build /build
RUN mkdir -p /build/.simple_rag
ENV PATH=/build/.venv/bin:$PATH

WORKDIR /build/rag_server
# config.json 运行时由 Secrets Manager 挂载；此处放 example 作为兜底
COPY rag_server/config.example.json /build/rag_server/config.json

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
