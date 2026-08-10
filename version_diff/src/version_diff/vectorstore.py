"""
向量存储与检索模块

职责：
1. 文档 embedding 计算 + FAISS 持久化（避免重复计算）
2. 高效相似度检索（替代 numpy 全量矩阵计算）

缓存策略：按文档内容哈希作为 key，同一文件内容只计算一次。
"""

import hashlib
import json
import logging
import os

import faiss
import numpy as np

log = logging.getLogger("version_diff.vectorstore")


# 默认缓存目录（~/.simple_rag/vector_cache/）
DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".simple_rag", "vector_cache")


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

    def get_or_compute(self, filepath: str, paragraphs: list, model) -> tuple:
        """
        获取文档的 embedding（优先读缓存）

        Args:
            filepath: 文档路径（用于计算内容哈希）
            paragraphs: Paragraph 对象列表
            model: SentenceTransformer 模型实例

        Returns:
            (embeddings: np.ndarray, faiss_index: faiss.Index)
        """
        if not paragraphs:
            log.info(f"  ⏭️ {filepath} 无段落，跳过嵌入计算")
            return np.empty((0, 0), dtype=np.float32), None

        cache_key = self._compute_cache_key(filepath, paragraphs)
        cached = self._load_cache(cache_key)

        if cached is not None:
            log.info(f"  💾 命中缓存 ({cache_key[:8]}...)，跳过 embedding 计算")
            return cached["embeddings"], cached["index"]

        # 缓存未命中，计算 embedding
        texts = [p.text for p in paragraphs]
        log.info(f"  ⏳ 计算 {len(texts)} 段嵌入...")
        embeddings = model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        embeddings = np.array(embeddings, dtype=np.float32)

        # 单句 encode 时 model 返回 (dim,) 而非 (1, dim)，统一升维避免下游 shape[1] 报错
        if embeddings.ndim == 1:
            embeddings = embeddings[np.newaxis, :]

        # 构建 FAISS index（内积，因为向量已归一化所以等价于余弦相似度）
        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)

        # 写入缓存
        self._save_cache(cache_key, embeddings, index, paragraphs)
        log.info(f"  💾 已缓存 ({cache_key[:8]}..., {len(texts)} 段, {dim}维)")

        return embeddings, index

    def search_similar(
        self, query_embeddings: np.ndarray, index: faiss.Index, top_k: int = 5
    ):
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
        # 文档内容哈希
        content_str = "\n".join(p.text for p in paragraphs)
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

    def _save_cache(
        self, cache_key: str, embeddings: np.ndarray, index, paragraphs: list
    ):
        """持久化到磁盘"""
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
