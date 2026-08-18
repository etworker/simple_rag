"""缓存路径解析 — version_diff 内的默认来源（独立库也可单独使用）。

缓存根目录解析优先级（由高到低）：
    1. 环境变量 SIMPLE_RAG_CACHE_ROOT
    2. 调用方显式传入的根目录（由宿主应用的总配置 cache.base_dir 提供）
    3. 默认 ~/.simple_rag

被 rag_server 调用时，宿主会把总配置 cache.base_dir 作为 root 传入，
从而覆盖本库的默认值（"子模块默认，总配置覆盖"）。
"""

import os

# 默认缓存根目录（仅在此一处出现字面量）
DEFAULT_CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".simple_rag")

# 覆盖缓存根目录的环境变量名（与 SIMPLE_RAG_LOG_DIR / SIMPLE_RAG_LOG_LEVEL 约定一致）
ENV_CACHE_ROOT = "SIMPLE_RAG_CACHE_ROOT"

# Embedding 相关环境变量名（集中常量，避免裸字面量散落多处）
ENV_EMBEDDING_MODEL = "EMBEDDING_MODEL"
ENV_EMBEDDING_DEVICE = "SIMPLE_RAG_EMBEDDING_DEVICE"
ENV_EMBEDDING_DTYPE = "SIMPLE_RAG_EMBEDDING_DTYPE"

# 默认 embedding 模型
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


def resolve_cache_root(config_base_dir: str | None = None) -> str:
    """解析缓存根目录：环境变量 > 配置 > 默认。"""
    return os.environ.get(ENV_CACHE_ROOT) or config_base_dir or DEFAULT_CACHE_ROOT


def cache_subdir(name: str, root: str | None = None) -> str:
    """在缓存根目录下生成子目录路径（root 缺省时按 resolve_cache_root 解析）。"""
    return os.path.join(root or resolve_cache_root(), name)
