"""缓存路径解析 — 应用内唯一来源。

缓存根目录解析优先级（由高到低）：
    1. 环境变量 SIMPLE_RAG_CACHE_ROOT
    2. 总配置 cache.base_dir（来自 ConfigStore / config.json）
    3. 默认 ~/.simple_rag

各子模块统一通过 cache_subdir(name) 拼子目录，不再硬编码 "~/.simple_rag"。
"""

import os

# 默认缓存根目录（总配置 cache.base_dir 的默认值也引用它，保证唯一来源）
DEFAULT_CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".simple_rag")

# 覆盖缓存根目录的环境变量名（与 SIMPLE_RAG_LOG_DIR / SIMPLE_RAG_LOG_LEVEL 约定一致）
ENV_CACHE_ROOT = "SIMPLE_RAG_CACHE_ROOT"

# 日志相关环境变量名
ENV_LOG_DIR = "SIMPLE_RAG_LOG_DIR"
ENV_LOG_LEVEL = "SIMPLE_RAG_LOG_LEVEL"


def resolve_cache_root(config_base_dir: str | None = None) -> str:
    """解析缓存根目录：环境变量 > 配置 > 默认。"""
    return os.environ.get(ENV_CACHE_ROOT) or config_base_dir or DEFAULT_CACHE_ROOT


def cache_subdir(name: str, root: str | None = None) -> str:
    """在缓存根目录下生成子目录路径（root 缺省时按 resolve_cache_root 解析）。"""
    return os.path.join(root or resolve_cache_root(), name)
