"""
文档管理服务 — 入库/查询/删除/检索

职责:
  - 管理已入库文档的元数据
  - 维护全局向量索引（FAISS）
  - 提供语义检索接口（给问答引擎调用）
  - 集成 version_diff 进行预审核
"""

import json
import os
import time
from dataclasses import asdict, dataclass

import faiss
import numpy as np
from doc_parser import Paragraph
from loguru import logger as log
from sentence_transformers import SentenceTransformer
from version_diff.vectorstore import VectorStore

from app.services.parse_cache import cached_parse as parse
from app.services.utils import compute_sha256


@dataclass
class DocMeta:
    """文档元数据"""

    filename: str  # 原始文件名（显示用）
    filepath: str
    doc_id: str = ""  # 内部唯一 key: filename#sha256[-8:].upper()
    paragraph_count: int = 0
    table_count: int = 0
    added_at: str = ""  # 入库时间 ISO
    status: str = "active"  # active / rejected / deleted
    page_count: int = 0
    char_count: int = 0
    file_hash: str = ""  # 文件 SHA-256


@dataclass
class RetrievedChunk:
    """检索到的段落"""

    text: str
    source_file: str
    location: str
    score: float
    paragraph_index: int


class DocStore:
    """
    文档存储与检索

    Example:
        store = DocStore(config)
        store.add_document("a.pdf")
        store.add_document("b.pdf")
        chunks = store.search("备份频率是多少？", top_k=5)
    """

    def __init__(self, config: dict):
        """
        Args:
            config: 包含 embedding/retrieval/pre_review 配置段的字典
        """
        self._config = config
        self._model: SentenceTransformer | None = None
        self._documents: dict = {}  # {filename: DocMeta}
        self._paragraphs: list[Paragraph] = []
        self._embeddings: np.ndarray | None = None
        self._index: faiss.Index | None = None

        # 持久化目录（默认 ~/.simple_rag/doc_store/）
        self._persist_dir = config.get("persist_dir") or os.path.join(
            os.path.expanduser("~"), ".simple_rag", "doc_store"
        )
        os.makedirs(self._persist_dir, exist_ok=True)
        self._load_from_disk()

        # 解析缓存目录（默认 ~/.simple_rag/parse_cache/）
        self._parse_cache_dir = config.get("parse_cache_dir") or os.path.join(
            os.path.expanduser("~"), ".simple_rag", "parse_cache"
        )
        os.makedirs(self._parse_cache_dir, exist_ok=True)

        # 向量缓存（复用 version_diff 的 VectorStore，避免重复计算 embedding）
        vector_cache_dir = config.get("vector_cache_dir", "")
        cfg_hash = VectorStore.compute_config_hash(
            config.get("embedding", {}).get("parse_config", {}),
            config.get("embedding", {}),
        )
        self._vector_store = VectorStore(
            cache_dir=vector_cache_dir, config_hash=cfg_hash
        )

    @staticmethod
    def _compute_file_hash(filepath: str) -> str:
        """计算文件 SHA-256"""
        return compute_sha256(filepath)

    def _get_model(self) -> SentenceTransformer:
        """懒加载 embedding 模型"""
        if self._model is None:
            from version_diff.device_utils import embedding_model_kwargs, resolve_embedding_device

            emb_config = self._config.get("embedding", {})
            model_name = emb_config.get("model", "")
            cache_dir = emb_config.get("cache_dir") or None
            device = resolve_embedding_device(emb_config)
            kwargs = embedding_model_kwargs(emb_config)
            log.info(f"加载 embedding 模型: {model_name} (device={device})")
            m_kwargs = {"cache_folder": cache_dir}
            m_kwargs.update(kwargs)
            self._model = SentenceTransformer(model_name, device=device, **m_kwargs)
            if device and device != "cpu":
                log.info(f"🚀 Embedding 加速: GPU, device={device}")
        return self._model

    def add_document(self, filepath: str, original_filename: str = "") -> DocMeta:
        """
        入库文档（解析 + embedding + 加入索引）

        Args:
            filepath: 文件路径（可能是安全文件名，如 SHA256.pdf）
            original_filename: 原始文件名（用于显示，如 用户上传时的文件名）

        Returns:
            DocMeta 文档元数据
        """
        # 解析（带缓存，使用配置的 parse_cache_dir，保证与 /clear 清理的目录一致）
        doc = parse(filepath, cache_dir=self._parse_cache_dir)
        # 用原始文件名覆盖（parse 返回的是磁盘文件名，可能是 SHA-256 安全名）
        if original_filename:
            doc.filename = original_filename
        log.info(
            f"解析完成: {doc.filename} ({len(doc.paragraphs)} 段, {len(doc.tables)} 表)"
        )

        # 计算 embedding（VectorStore 内部有缓存，自动复用）
        model = self._get_model()
        embeddings, _index = self._vector_store.get_or_compute(
            doc.filename, doc.paragraphs, model
        )
        embeddings = np.array(embeddings).astype(np.float32)

        # 追加到全局
        self._paragraphs.extend(doc.paragraphs)
        if self._embeddings is None:
            self._embeddings = embeddings
        else:
            self._embeddings = np.vstack([self._embeddings, embeddings])

        # 重建 FAISS index
        self._rebuild_index()

        # 计算页数
        page_count = 0
        if filepath.lower().endswith(".pdf"):
            try:
                import fitz

                with fitz.open(filepath) as pdf_doc:
                    page_count = pdf_doc.page_count
            except Exception:
                pass

        # 计算字数
        char_count = sum(len(p.text) for p in doc.paragraphs)

        # 计算文件 SHA-256
        file_hash = self._compute_file_hash(filepath)
        doc_id = f"{doc.filename}#{file_hash[-8:].upper()}"

        # 更新段落的 source_file 为 doc_id（保证唯一性）
        for p in doc.paragraphs:
            p.source_file = doc_id

        # 记录元数据
        meta = DocMeta(
            filename=doc.filename,
            filepath=filepath,
            doc_id=doc_id,
            paragraph_count=len(doc.paragraphs),
            table_count=len(doc.tables),
            added_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            page_count=page_count,
            char_count=char_count,
            file_hash=file_hash,
        )
        self._documents[doc_id] = meta
        log.info(f"已入库: {doc.filename}")
        self._save_to_disk()
        return meta

    def remove_document(self, doc_id: str) -> bool:
        """从库中移除文档（按 doc_id）"""
        if doc_id not in self._documents:
            # 兼容：尝试用 filename 匹配
            matches = [k for k, v in self._documents.items() if v.filename == doc_id]
            if not matches:
                return False
            doc_id = matches[0]

        # 找到该文档段落的范围
        indices_to_remove = [
            i for i, p in enumerate(self._paragraphs) if p.source_file == doc_id
        ]
        if not indices_to_remove:
            del self._documents[doc_id]
            return True

        # 移除段落和 embedding
        keep_mask = np.ones(len(self._paragraphs), dtype=bool)
        keep_mask[indices_to_remove] = False

        self._paragraphs = [p for i, p in enumerate(self._paragraphs) if keep_mask[i]]
        if self._embeddings is not None and len(self._embeddings) > 0:
            self._embeddings = self._embeddings[keep_mask]

        self._rebuild_index()
        self._documents[doc_id].status = "deleted"
        log.info(f"已移除: {doc_id}")
        self._save_to_disk()
        return True

    def clear_all(self):
        """
        清空知识库（删除所有文档、段落、索引、缓存）

        用于重置整个知识库。
        """
        count = len(self._documents)
        self._documents = {}
        self._paragraphs = []
        self._embeddings = None
        self._index = None
        # 清空磁盘持久化文件（保留目录）
        for f in os.listdir(self._persist_dir):
            fp = os.path.join(self._persist_dir, f)
            if os.path.isfile(fp):
                os.remove(fp)
        log.info(f"🗑️ 知识库已清空: 移除 {count} 篇文档")
        return count

    def search(self, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
        """
        语义检索

        Args:
            query: 用户问题
            top_k: 返回结果数（默认从配置读取）

        Returns:
            按相关度排序的段落列表
        """
        if self._index is None or len(self._paragraphs) == 0:
            return []

        retrieval_config = self._config.get("retrieval", {})
        top_k = top_k or retrieval_config.get("top_k", 5)
        threshold = retrieval_config.get("similarity_threshold", 0.3)

        # 查询 embedding
        model = self._get_model()
        q_emb = model.encode([query], normalize_embeddings=True).astype(np.float32)

        # FAISS 检索
        actual_k = min(top_k, len(self._paragraphs))
        scores, indices = self._index.search(q_emb, actual_k)

        # 封装结果
        results = []
        for i in range(actual_k):
            idx = int(indices[0][i])
            score = float(scores[0][i])
            if score < threshold:
                continue
            para = self._paragraphs[idx]
            results.append(
                RetrievedChunk(
                    text=para.text,
                    source_file=para.source_file,
                    location=para.location,
                    score=score,
                    paragraph_index=idx,
                )
            )

        return results

    def list_documents(self) -> list[DocMeta]:
        """列出所有已入库文档（按入库时间排序）"""
        docs = [m for m in self._documents.values() if m.status == "active"]
        docs.sort(key=lambda d: d.added_at)
        return docs

    def get_document(self, doc_id: str) -> DocMeta | None:
        """获取文档元数据（按 doc_id 或 filename）"""
        if doc_id in self._documents:
            return self._documents[doc_id]
        # 兼容：尝试用 filename 匹配
        for _k, v in self._documents.items():
            if v.filename == doc_id:
                return v
        return None

    @property
    def total_paragraphs(self) -> int:
        return len(self._paragraphs)

    @property
    def total_documents(self) -> int:
        return len([d for d in self._documents.values() if d.status == "active"])

    def _rebuild_index(self):
        """重建 FAISS index"""
        if self._embeddings is None or len(self._embeddings) == 0:
            self._index = None
            return
        dim = self._embeddings.shape[1]
        self._index = faiss.IndexFlatIP(dim)
        self._index.add(self._embeddings)

    # ============================================================
    # 磁盘持久化
    # ============================================================

    def _save_to_disk(self):
        """将元数据、段落、embeddings 保存到磁盘"""
        try:
            # 1. 元数据
            meta_path = os.path.join(self._persist_dir, "documents.json")
            meta_list = {k: asdict(v) for k, v in self._documents.items()}
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta_list, f, ensure_ascii=False, indent=2)

            # 2. 段落（序列化为 JSON）
            paras_path = os.path.join(self._persist_dir, "paragraphs.json")
            paras_data = [p.to_dict() for p in self._paragraphs]
            with open(paras_path, "w", encoding="utf-8") as f:
                json.dump(paras_data, f, ensure_ascii=False)

            # 3. Embeddings
            if self._embeddings is not None and len(self._embeddings) > 0:
                emb_path = os.path.join(self._persist_dir, "embeddings.npy")
                np.save(emb_path, self._embeddings)

            log.info(
                f"持久化完成: {len(self._documents)} 文档, {len(self._paragraphs)} 段落"
            )
        except Exception as e:
            log.error(f"持久化失败: {e}", exc_info=True)

    def _load_from_disk(self):
        """从磁盘恢复状态"""
        meta_path = os.path.join(self._persist_dir, "documents.json")
        paras_path = os.path.join(self._persist_dir, "paragraphs.json")
        emb_path = os.path.join(self._persist_dir, "embeddings.npy")

        if not os.path.exists(meta_path):
            return  # 首次启动，无数据

        try:
            # 1. 元数据
            with open(meta_path, encoding="utf-8") as f:
                meta_dict = json.load(f)
            self._documents = {k: DocMeta(**v) for k, v in meta_dict.items()}

            # 2. 段落
            if os.path.exists(paras_path):
                with open(paras_path, encoding="utf-8") as f:
                    paras_data = json.load(f)
                self._paragraphs = [Paragraph.from_dict(p) for p in paras_data]

            # 3. Embeddings + FAISS
            if os.path.exists(emb_path):
                self._embeddings = np.load(emb_path)
                self._rebuild_index()

            # 回填缺失的 page_count / char_count（所有数据加载完毕后）
            need_save = False
            for fname, meta in self._documents.items():
                if (
                    meta.page_count == 0
                    and meta.filepath
                    and meta.filepath.lower().endswith(".pdf")
                ):
                    try:
                        import fitz

                        with fitz.open(meta.filepath) as pdf_doc:
                            meta.page_count = pdf_doc.page_count
                            need_save = True
                    except Exception:
                        pass
                if meta.char_count == 0:
                    chars = sum(
                        len(p.text) for p in self._paragraphs if p.source_file == fname
                    )
                    if chars > 0:
                        meta.char_count = chars
                        need_save = True
            if need_save:
                self._save_to_disk()

            log.info(
                f"从磁盘恢复: {len(self._documents)} 文档, {len(self._paragraphs)} 段落"
            )
        except Exception as e:
            log.error(f"从磁盘恢复失败: {e}", exc_info=True)

    # ============================================================
    # 段落查询公共接口
    # ============================================================

    def get_paragraphs_by_file(self, doc_id: str) -> list:
        """获取指定文档的所有段落（按 doc_id 或 filename）"""
        # 先尝试直接匹配 doc_id
        result = [p for p in self._paragraphs if p.source_file == doc_id]
        if result:
            return result
        # 兼容：尝试用 filename 匹配
        return [
            p
            for p in self._paragraphs
            if p.source_file.startswith(doc_id + "#") or p.source_file == doc_id
        ]

    def get_paragraph_context(
        self, doc_id: str, index: int = 0, radius: int = 3
    ) -> list:
        """
        获取指定文档中某段落及其前后上下文

        Args:
            filename: 文档名
            index: 段落在该文档中的序号
            radius: 前后取多少段

        Returns:
            [{text, location, index, is_target}, ...]
        """
        doc_paras = self.get_paragraphs_by_file(doc_id)
        if not doc_paras:
            return []
        target = min(index, len(doc_paras) - 1)
        start = max(0, target - radius)
        end = min(len(doc_paras), target + radius + 1)
        return [
            {
                "text": p.text,
                "location": p.location,
                "index": i,
                "is_target": i == target,
            }
            for i, p in enumerate(doc_paras[start:end], start=start)
        ]

    def find_paragraphs(self, doc_id: str, location: str = "", limit: int = 5) -> list:
        """
        搜索匹配的段落

        Args:
            filename: 文档名
            location: 位置匹配（空则返回所有）
            limit: 最多返回条数

        Returns:
            [{text, location, chapter}, ...]
        """
        matches = []
        for p in self._paragraphs:
            if p.source_file != doc_id and not p.source_file.startswith(doc_id + "#"):
                continue
            if not location or p.location == location:
                matches.append(
                    {"text": p.text, "location": p.location, "chapter": p.chapter}
                )
                if len(matches) >= limit:
                    break
        return matches
