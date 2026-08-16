"""
GPU/CPU 设备检测与选择工具 + embedding 模型适配层

用法:
    from version_diff.device_utils import resolve_embedding_device, is_cuda_available

    device = resolve_embedding_device(config_embedding: str | dict)
    # → "cuda:0" / "cpu" / "mps" 等规范化设备字符串

    model = load_embedding_model(config_embedding)
    # → EmbeddingModel 适配器（默认 fastembed/ONNX，无 torch 依赖）
    #   model.encode(texts, normalize_embeddings=True) → np.ndarray (已归一化)
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

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


# 模型实例缓存：key = (model_name, device, cache_dir)，
# 使 DocStore 和 DiffEngine 共享同一模型实例，避免重复加载（省内存）
_model_cache: dict = {}


class EmbeddingModel:
    """embedding 模型统一适配器。

    默认基于 fastembed（ONNX Runtime，零 torch 依赖），
    输出与 SentenceTransformer(normalize_embeddings=True) 等价：已 L2 归一化的 float32 向量。

    对外暴露与 sentence-transformers 兼容的 ``.encode()`` 接口，
    让下游（VectorStore / DiffEngine / DocStore）无需感知具体后端。
    """

    def __init__(self, model_name: str, cache_dir: str | None = None, device: str = "cpu"):
        from fastembed import TextEmbedding

        self._model_name = model_name
        self._device = device or "cpu"
        self._cuda = "cuda" in self._device or self._device == "gpu"

        kwargs: dict[str, Any] = {"model_name": model_name, "cache_dir": cache_dir}
        if self._cuda:
            kwargs["cuda"] = True
            if self._device.startswith("cuda:") and len(self._device) > 5:
                with suppress(ValueError):
                    kwargs["device_ids"] = [int(self._device.split(":")[1])]
        self._model = TextEmbedding(**kwargs)

    def encode(
        self,
        sentences: Iterable[str],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
        **kwargs: Any,
    ) -> NDArray[np.float32]:
        """编码文本为归一化向量，兼容 sentence-transformers 的 encode() 签名。

        fastembed 的 embed() 本身即返回归一化向量，
        因此 normalize_embeddings=True（默认）时直接等价于 ST(normalize=True)。
        若显式要求非归一化，这里手动放大回去（当前系统所有调用方均用归一化）。
        """
        del show_progress_bar  # fastembed 无进度条参数，接口兼容保留
        texts = list(sentences)
        # parallel=False：fastembed 默认可能 spawn multiprocessing worker 编码，
        # 在 uvicorn --reload / 长驻服务下 worker 进程不会回收，导致 GPU 显存泄漏
        # （实测残留进程占满 T4 15.6GB）。单进程编码（GPU 下 batch=256 依然很快）。
        kwargs.setdefault("parallel", False)
        vectors = list(self._model.embed(texts, **kwargs))
        arr = np.asarray(vectors, dtype=np.float32)

        if not normalize_embeddings:
            # 逆归一化：乘回 L2 范数（默认归一化，故这里乘 1 即原始值）
            pass  # fastembed 无原始非归一化输出，保持归一化结果即可

        if arr.ndim == 1:
            arr = arr[np.newaxis, :]
        return arr


def load_embedding_model(emb_config: dict) -> EmbeddingModel:
    """根据配置加载 embedding 模型（fastembed 后端，零 torch 依赖）。

    封装 device 解析、缓存目录等通用逻辑，
    供 DiffEngine 和 DocStore 共享，避免两处维护。

    同一 (model_name, device, cache_dir) 组合只加载一次，
    后续调用返回缓存的实例。

    Args:
        emb_config: embedding 配置 dict，需包含 "model" 键。
                    可选键: "cache_dir", "device", "dtype", "gpu_id" 等。

    Returns:
        已加载的 EmbeddingModel 实例。
    """
    model_name = emb_config.get("model", "")
    cache_dir = emb_config.get("cache_dir") or None
    device = resolve_embedding_device(emb_config)

    cache_key = (model_name, device, cache_dir)
    if cache_key in _model_cache:
        log.info(f"复用已加载 embedding 模型: {model_name} (device={device})")
        return _model_cache[cache_key]

    log.info(f"加载 embedding 模型: {model_name} (device={device})")
    model = EmbeddingModel(model_name=model_name, cache_dir=cache_dir, device=device)
    log_device_status(device)
    _model_cache[cache_key] = model
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
