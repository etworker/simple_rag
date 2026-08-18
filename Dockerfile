# syntax=docker/dockerfile:1

# DEVICE=cpu 用于本地/通用镜像；AWS NVIDIA GPU 镜像构建时传 --build-arg DEVICE=gpu。
ARG DEVICE=cpu

FROM python:3.12-slim AS build
ARG DEVICE
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgl1 libglib2.0-0 poppler-utils curl git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock /build/
COPY setup_env.py /build/setup_env.py
COPY rag_server /build/rag_server
COPY doc_parser /build/doc_parser
COPY version_diff /build/version_diff
COPY llm_chat /build/llm_chat

# 构建目标无法可靠自动探测运行时 GPU，因此通过 DEVICE build arg 显式选择。
RUN python setup_env.py --device "${DEVICE}" --python 3.12

# 构建期预热实际使用的 FastEmbed 模型。
ENV HF_HUB_OFFLINE=0 HF_HOME=/build/.cache/huggingface
RUN .venv/bin/python -c \
        "from fastembed import TextEmbedding; model = TextEmbedding('BAAI/bge-small-zh-v1.5'); next(model.embed(['warmup']))"

FROM python:3.12-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/build \
    HF_HOME=/build/.cache/huggingface \
    HF_HUB_OFFLINE=1 \
    CONFIG_PATH=/build/rag_server/config.json \
    PORT=8000 \
    PATH=/build/.venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /build /build
WORKDIR /build/rag_server
COPY rag_server/config.example.json /build/rag_server/config.json
RUN mkdir -p /build/.simple_rag

EXPOSE 8000
CMD ["/build/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
