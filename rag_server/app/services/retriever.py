"""向量检索抽象层 — 为 AWS 移植留缝。

当前实现：FaissRetriever（进程内 FAISS，全内存）。
目标：未来可替换为 pgvector / OpenSearch / Milvus 等托管检索后端，
只需实现 Retriever 接口（add / remove / search / rebuild / save / load / clear），
DocStore 检索逻辑零改动。

设计边界：
- Retriever 只管"向量 + 索引"，不管段落文本（段落由 DocStore 持有）
- search 返回 (scores, indices)，调用方用 indices 查段落
- add 接收"新向量 + 它在全局段落数组中的起始下标"（用于持久化恢复）
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

import numpy as np
from loguru import logger as log


class Retriever(ABC):
    """向量检索抽象接口。"""

    @abstractmethod
    def add(self, embeddings: np.ndarray, start_index: int = 0) -> None:
        """追加一批向量（start_index 是该批向量在全局段落数组中的起始下标）。"""

    @abstractmethod
    def remove(self, keep_mask: np.ndarray) -> None:
        """按 keep_mask（True=保留）移除向量。"""

    @abstractmethod
    def search(self, query_emb: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        """返回 (scores, indices)，均按相似度降序。"""

    @abstractmethod
    def rebuild(self) -> None:
        """重建索引（全量）。"""

    @abstractmethod
    def clear(self) -> None:
        """清空所有向量与索引。"""

    @abstractmethod
    def save(self, directory: str) -> None:
        """持久化向量与索引到目录。"""

    @abstractmethod
    def load(self, directory: str) -> None:
        """从目录恢复向量与索引。"""

    @property
    @abstractmethod
    def count(self) -> int:
        """当前向量数量。"""

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度。"""


class FaissRetriever(Retriever):
    """FAISS 实现：IndexFlatIP（内积，向量已归一化 → 等价余弦相似度）。

    - 全内存：适合中小库（<5 万段），单实例 Fargate/EC2 起步
    - 每次 add/remove 后全量 rebuild（O(n)），库大时可换 IndexIVFFlat 等
    """

    def __init__(self, index_factory: str = "flat"):
        import faiss

        self._faiss = faiss
        self._index: faiss.Index | None = None
        self._embeddings: np.ndarray | None = None
        self._index_factory = index_factory  # 预留：flat / IVF / HNSW

    # ---- 索引构建 ----

    def _build_index(self, embeddings: np.ndarray):
        """按 index_factory 构建索引（当前仅 flat）。"""
        dim = embeddings.shape[1]
        if self._index_factory == "flat":
            index = self._faiss.IndexFlatIP(dim)
            index.add(embeddings)
        else:
            # 预留扩展点：IVF / HNSW / PQ 等
            index = self._faiss.index_factory(dim, self._index_factory, self._faiss.METRIC_INNER_PRODUCT)
            index.add(embeddings)
        self._index = index

    # ---- Retriever 接口 ----

    def add(self, embeddings: np.ndarray, start_index: int = 0) -> None:
        del start_index  # flat 索引无需记录偏移（indices 即全局数组下标）
        embeddings = np.asarray(embeddings, dtype=np.float32)
        if self._embeddings is None:
            self._embeddings = embeddings
        else:
            self._embeddings = np.vstack([self._embeddings, embeddings])
        self.rebuild()

    def remove(self, keep_mask: np.ndarray) -> None:
        if self._embeddings is not None and len(self._embeddings) > 0:
            self._embeddings = self._embeddings[keep_mask]
        self.rebuild()

    def search(self, query_emb: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        if self._index is None or self.count == 0:
            return np.empty((0,), dtype=np.float32), np.empty((0,), dtype=np.int64)
        q = np.asarray(query_emb, dtype=np.float32)
        if q.ndim == 1:
            q = q[np.newaxis, :]
        actual_k = min(top_k, self.count)
        scores, indices = self._index.search(q, actual_k)
        return scores[0], indices[0]

    def rebuild(self) -> None:
        if self._embeddings is None or len(self._embeddings) == 0:
            self._index = None
            return
        self._build_index(self._embeddings)

    def clear(self) -> None:
        self._embeddings = None
        self._index = None

    def save(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        emb_path = os.path.join(directory, "embeddings.npy")
        idx_path = os.path.join(directory, "index.faiss")
        if self._embeddings is not None and len(self._embeddings) > 0:
            emb_tmp = f"{emb_path}.tmp.npy"
            idx_tmp = f"{idx_path}.tmp"
            try:
                np.save(emb_tmp, self._embeddings)
                if self._index is not None:
                    self._faiss.write_index(self._index, idx_tmp)
                os.replace(emb_tmp, emb_path)
                if self._index is not None:
                    os.replace(idx_tmp, idx_path)
                log.info(f"Retriever 持久化: {self.count} 向量 → {directory}")
            except Exception:
                for tmp_path in (emb_tmp, idx_tmp):
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                raise
        else:
            log.info("Retriever 持久化: 空（跳过）")

    def load(self, directory: str) -> None:
        emb_path = os.path.join(directory, "embeddings.npy")
        if not os.path.exists(emb_path):
            return
        try:
            self._embeddings = np.load(emb_path)
            self.rebuild()
            log.info(f"Retriever 恢复: {self.count} 向量 ← {directory}")
        except Exception as e:
            log.warning(f"Retriever 恢复失败: {e}")
            self._embeddings = None
            self._index = None

    @property
    def count(self) -> int:
        return 0 if self._embeddings is None else len(self._embeddings)

    @property
    def dim(self) -> int:
        return 0 if self._embeddings is None else self._embeddings.shape[1]
