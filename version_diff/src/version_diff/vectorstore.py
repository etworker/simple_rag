"""
向量存储与检索模块

职责：
1. 文档 embedding 计算 + FAISS 持久化（避免重复计算）
2. 高效相似度检索（替代 numpy 全量矩阵计算）

缓存策略：按文档内容哈希作为 key，同一文件内容只计算一次。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING, Callable

import numpy as np
from loguru import logger as log

from version_diff.paths import cache_subdir

if TYPE_CHECKING:
    import faiss

# 默认缓存目录（<root>/vector_cache/）
DEFAULT_CACHE_DIR = cache_subdir("vector_cache")


class VectorStore:
    """
    基于 FAISS 的向量存储

    功能：
    - 计算 embedding 后写入 FAISS index + 元数据文件
    - 下次遇到相同文档直接加载，零计算
    - 提供高效的 top-K 近邻检索

    Args:
        cache_dir: 缓存目录路径
        config_hash: 配置签名（用于缓存失效判断），
            传空则默认为固定值。应由调用方根据 embedding/parse 配置计算后传入。
    """

    def __init__(self, cache_dir: str = "", config_hash: str = ""):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self._config_hash = config_hash or self._default_config_hash()

    def get_or_compute(
        self,
        filepath: str,
        paragraphs: list,
        model,
        *,
        on_progress: Callable | None = None,
        batch_size: int = 64,
    ) -> tuple:
        """
        获取文档的 embedding（优先读缓存）。

        ``on_progress`` 是可选的 best-effort 回调，收到一个字典：
        ``phase/status/completed/total/batch_index/batch_total/pct/cached``。
        不影响原有三参数调用和二元返回值。
        """
        import faiss

        def notify(status, completed, total, batch_index=0, batch_total=0, cached=False):
            if on_progress is None:
                return
            event = {
                "phase": "embedding",
                "status": status,
                "completed": int(completed),
                "total": int(total),
                "batch_index": int(batch_index),
                "batch_total": int(batch_total),
                "pct": round(100 * completed / max(1, total)),
                "cached": bool(cached),
            }
            try:
                on_progress(event)
            except Exception as exc:
                # 进度展示不能影响 embedding 主流程。
                log.debug(f"embedding 进度回调失败（已忽略）: {exc}")

        if not paragraphs:
            log.info(f"  ⏭️ {filepath} 无段落，跳过嵌入计算")
            notify("empty", 0, 0)
            return np.empty((0, 0), dtype=np.float32), None

        total = len(paragraphs)
        cache_key = self._compute_cache_key(filepath, paragraphs)
        cached = self._load_cache(cache_key)

        if cached is not None:
            log.info(f"  💾 命中缓存 ({cache_key[:8]}...)，跳过 embedding 计算")
            notify("cached", total, total, batch_index=0, batch_total=0, cached=True)
            return cached["embeddings"], cached["index"]

        # 显式按批调用 encode，才能在模型计算期间产生可观测进度。
        texts = [self.embedding_text(p) for p in paragraphs]
        safe_batch_size = max(1, int(batch_size or 64))
        batch_total = (total + safe_batch_size - 1) // safe_batch_size
        log.info(f"  ⏳ 计算 {total} 段嵌入（{batch_total} 批，每批最多 {safe_batch_size} 段）...")
        notify("running", 0, total, batch_index=0, batch_total=batch_total)

        embedding_batches = []
        for batch_index, start in enumerate(range(0, total, safe_batch_size), start=1):
            batch_texts = texts[start : start + safe_batch_size]
            batch_embeddings = model.encode(
                batch_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            batch_array = np.asarray(batch_embeddings, dtype=np.float32)
            if batch_array.ndim == 1:
                batch_array = batch_array[np.newaxis, :]
            embedding_batches.append(batch_array)
            completed = min(start + len(batch_texts), total)
            notify(
                "done" if completed >= total else "running",
                completed,
                total,
                batch_index=batch_index,
                batch_total=batch_total,
            )

        embeddings = np.vstack(embedding_batches).astype(np.float32, copy=False)

        # 构建 FAISS index（内积，因为向量已归一化所以等价于余弦相似度）
        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)

        # 写入缓存
        self._save_cache(cache_key, embeddings, index, paragraphs)
        log.info(f"  💾 已缓存 ({cache_key[:8]}..., {total} 段, {dim}维)")

        return embeddings, index

    @staticmethod
    def embedding_text(paragraph) -> str:
        """构造用于 embedding 的文本，不改变段落原文。"""
        text = str(getattr(paragraph, "text", "") or "").strip()
        chapter = str(getattr(paragraph, "chapter", "") or "").strip()
        chapter_title = str(getattr(paragraph, "chapter_title", "") or "").strip()
        heading = " / ".join(part for part in (chapter, chapter_title) if part)
        if not heading:
            return text
        return f"章节：{heading}\n正文：{text}" if text else f"章节：{heading}"

    def search_similar(self, query_embeddings: np.ndarray, index: faiss.Index, top_k: int = 5):
        """
        在 FAISS index 中搜索最相似的 top-K

        Args:
            query_embeddings: 查询向量 (N, dim)
            index: 目标文档的 FAISS index
            top_k: 每个查询返回的最近邻数量

        Returns:
            (similarities: np.ndarray [N, K], indices: np.ndarray [N, K])
        """
        query_embeddings = np.array(query_embeddings, dtype=np.float32)
        similarities, indices = index.search(query_embeddings, top_k)
        return similarities, indices

    def clear_cache(self):
        """清除所有缓存"""
        import shutil

        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
            os.makedirs(self.cache_dir)
            log.info("  🗑️ 向量缓存已清除")

    # ================================================================
    # 内部方法
    # ================================================================

    def _compute_cache_key(self, filepath: str, paragraphs: list) -> str:
        """
        计算缓存 key：基于文件内容哈希 + 段落数量 + 配置哈希

        缓存失效条件（任一变化即自动失效）：
        - 文档内容变了（段落文本哈希变化）
        - 解析配置变了（config.yaml 的 extract 段变化）
        - embedding 模型变了（config.yaml 的 embedding 段变化）
        """
        # 文档内容哈希：必须与实际 embedding 输入一致，章节元数据变化时缓存也失效。
        content_str = "\n".join(self.embedding_text(p) for p in paragraphs)
        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()[:12]

        return f"{content_hash}_{len(paragraphs)}_{self._config_hash}"

    @staticmethod
    def _default_config_hash() -> str:
        """默认配置哈希（向后兼容：空配置的固定哈希）"""
        return hashlib.sha256(b"").hexdigest()[:8]

    @staticmethod
    def compute_config_hash(extract_config: dict, embedding_config: dict) -> str:
        """
        根据解析和嵌入配置计算缓存哈希。

        当配置变化时，哈希变化 → 缓存自动失效。
        应由 DiffEngine 调用并传入 VectorStore 构造函数。
        """
        config_sig = json.dumps(
            {"extract": extract_config, "embedding": embedding_config},
            sort_keys=True,
        )
        return hashlib.sha256(config_sig.encode("utf-8")).hexdigest()[:8]

    def _cache_path(self, cache_key: str) -> str:
        """缓存目录路径"""
        return os.path.join(self.cache_dir, cache_key)

    def _load_cache(self, cache_key: str) -> dict | None:
        """从磁盘加载缓存"""
        import faiss

        cache_path = self._cache_path(cache_key)
        index_file = os.path.join(cache_path, "index.faiss")
        emb_file = os.path.join(cache_path, "embeddings.npy")

        if not os.path.exists(index_file) or not os.path.exists(emb_file):
            return None

        try:
            index = faiss.read_index(index_file)
            embeddings = np.load(emb_file)
            return {"index": index, "embeddings": embeddings}
        except Exception as e:
            log.warning(f"  ⚠️ 缓存加载失败: {e}")
            return None

    def _save_cache(self, cache_key: str, embeddings: np.ndarray, index, paragraphs: list):
        """持久化到磁盘"""
        import faiss

        cache_path = self._cache_path(cache_key)
        os.makedirs(cache_path, exist_ok=True)

        # 保存 FAISS index
        faiss.write_index(index, os.path.join(cache_path, "index.faiss"))

        # 保存 embeddings（用于后续取出做矩阵运算）
        np.save(os.path.join(cache_path, "embeddings.npy"), embeddings)

        # 保存元数据
        meta = {
            "num_paragraphs": len(paragraphs),
            "dim": embeddings.shape[1],
            "cache_key": cache_key,
        }
        with open(os.path.join(cache_path, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
