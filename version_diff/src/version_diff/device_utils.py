"""
GPU/CPU 设备检测与选择工具

用法:
    from version_diff.device_utils import resolve_embedding_device, is_cuda_available

    device = resolve_embedding_device(config_embedding: str | dict)
    # → "cuda:0" / "cpu" / "mps" 等可直接传给 SentenceTransformer 的 device 参数
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger as log


def is_cuda_available() -> bool:
    """检测当前环境是否可用 CUDA GPU。"""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def is_mps_available() -> bool:
    """检测当前环境是否可用 Apple MPS (M 系列芯片)。"""
    try:
        import torch

        return bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except ImportError:
        return False


def _normalize_device_str(device: str) -> str:
    """规范化 device 字符串。

    接受的输入:
      - "auto" → 自动检测 (cuda > mps > cpu)
      - "gpu"  → 第一个可用的 GPU (与 auto 等价)
      - "cpu"  → 强制 CPU
      - "cuda" → 第一个可用 GPU
      - "cuda:0" / "cuda:1" → 指定 GPU
      - "mps"  → Apple Silicon
    """
    d = (device or "auto").strip().lower()

    if d in ("auto", "gpu"):
        if is_cuda_available():
            return "cuda"
        if is_mps_available():
            return "mps"
        return "cpu"

    if d == "cuda":
        return "cuda" if is_cuda_available() else "cpu"

    if d.startswith("cuda:"):
        return d if is_cuda_available() else "cpu"

    if d == "mps":
        return "mps" if is_mps_available() else "cpu"

    # 其他未知值（包括 "cpu" 等）直接返回小写版本
    return d


def resolve_embedding_device(config_embedding: str | dict | None) -> str:
    """从配置中解析 embedding 设备。

    Args:
        config_embedding: 可以是:
            - dict: {"device": "auto", "dtype": "auto", ...}
            - str: 直接的设备字符串 "auto" / "cuda" / "cpu"
            - None: 默认 "auto"

    Returns:
        规范化后的 device 字符串，可直接传给 SentenceTransformer。

    优先级:
        1. 环境变量 SIMPLE_RAG_EMBEDDING_DEVICE（覆盖配置）
        2. config_embedding 中的 "device" 字段（或 config_str 本身）
        3. 默认 "auto"
    """
    # 1. 环境变量最高优先级
    env_device = os.environ.get("SIMPLE_RAG_EMBEDDING_DEVICE")
    if env_device:
        return _normalize_device_str(env_device)

    # 2. 从配置取
    if isinstance(config_embedding, dict):
        device = config_embedding.get("device", "auto")
    elif isinstance(config_embedding, str):
        device = config_embedding
    else:
        device = "auto"

    return _normalize_device_str(device)


def embedding_model_kwargs(config_embedding: dict | None) -> dict[str, Any]:
    """构建 SentenceTransformer 的额外关键字参数。

    返回 dict 可能包含:
      - "model_kwargs": {"torch_dtype": ...}   ← 传给底层 transformers 模型
      - 其他 SentenceTransformer 原生支持的顶层参数

    返回空 dict 表示无需额外参数。
    """
    if not isinstance(config_embedding, dict):
        return {}

    dtype = config_embedding.get("dtype", "")
    if not dtype:
        return {}

    dtype_lower = dtype.lower()

    # 规范化 dtype 字符串到 torch.dtype 或 "auto"
    dtype_map = {
        "auto": "auto",
        "float16": "torch.float16",
        "fp16": "torch.float16",
        "bfloat16": "torch.bfloat16",
        "float32": "torch.float32",
        "fp32": "torch.float32",
    }

    torch_dtype = dtype_map.get(dtype_lower)
    if torch_dtype is None:
        torch_dtype = dtype  # 保留原字符串

    if torch_dtype == "auto":
        return {"model_kwargs": {"torch_dtype": "auto"}}

    try:
        import torch as _torch

        # "torch.float16" → torch.float16
        attr_name = torch_dtype.split(".")[-1] if "." in torch_dtype else torch_dtype
        resolved = getattr(_torch, attr_name)
    except (AttributeError, ImportError):
        resolved = torch_dtype

    return {"model_kwargs": {"torch_dtype": resolved}}


def load_embedding_model(emb_config: dict) -> SentenceTransformer:
    """根据配置加载 SentenceTransformer embedding 模型。

    封装 device 解析、dtype 参数、缓存目录等通用逻辑，
    供 DiffEngine 和 DocStore 共享，避免两处维护。

    Args:
        emb_config: embedding 配置 dict，需包含 "model" 键。
                    可选键: "cache_dir", "device", "dtype", "gpu_id" 等。

    Returns:
        已加载的 SentenceTransformer 实例。
    """
    from sentence_transformers import SentenceTransformer

    model_name = emb_config.get("model", "")
    cache_dir = emb_config.get("cache_dir") or None
    device = resolve_embedding_device(emb_config)
    kwargs = embedding_model_kwargs(emb_config)
    log.info(f"加载 embedding 模型: {model_name} (device={device})")
    m_kwargs = {"cache_folder": cache_dir}
    m_kwargs.update(kwargs)
    model = SentenceTransformer(model_name, device=device, **m_kwargs)
    log_device_status(device)
    return model


def log_device_status(device: str, verbose: bool = True) -> None:
    """输出当前检测到的设备信息日志。"""
    if not verbose:
        return

    if "cuda" in device:
        try:
            import torch

            idx = torch.cuda.current_device()
            name = torch.cuda.get_device_name(idx)
            mem = torch.cuda.get_device_properties(idx).total_mem / (1024**3)
            log.info(f"🚀 Embedding 加速: GPU ({name}, {mem:.1f} GB), device={device}")
        except Exception:
            log.info(f"🚀 Embedding 加速: GPU, device={device}")
    else:
        log.info(f"💻 Embedding 加速: CPU, device={device}")


def maybe_index_to_gpu(index, gpu_id: int = 0):
    """尝试把 FAISS index 迁移到 GPU；若失败则返回原始 CPU index。

    返回: (index, is_gpu: bool)
    """
    if not is_cuda_available():
        return index, False
    try:
        import faiss

        res = faiss.StandardGpuResources()
        gpu_index = faiss.index_cpu_to_gpu(res, gpu_id, index)
        return gpu_index, True
    except Exception as e:
        log.warning(f"⚠️ FAISS GPU 不可用，回退到 CPU: {e}")
        return index, False
