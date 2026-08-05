"""
文档管理服务 — 入库/查询/删除/检索

职责:
  - 管理已入库文档的元数据
  - 维护全局向量索引（FAISS）
  - 提供语义检索接口（给问答引擎调用）
  - 集成 version_diff 进行预审核
"""
import os
import time
import json
import logging
from typing import List, Optional, Callable
from dataclasses import dataclass, field, asdict

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

from doc_parser import Document, Paragraph
from app.services.parse_cache import cached_parse as parse

log = logging.getLogger("rag_demo.doc_store")


@dataclass
class DocMeta:
    """文档元数据"""
    filename: str
    filepath: str
    paragraph_count: int
    table_count: int
    added_at: str           # ISO 时间
    status: str = "active"  # active / rejected / deleted
    page_count: int = 0
    char_count: int = 0


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
        self._model: Optional[SentenceTransformer] = None
        self._documents: dict = {}          # {filename: DocMeta}
        self._paragraphs: List[Paragraph] = []
        self._embeddings: Optional[np.ndarray] = None
        self._index: Optional[faiss.Index] = None

        # 持久化目录
        self._persist_dir = config.get("persist_dir", "./data/doc_store")
        os.makedirs(self._persist_dir, exist_ok=True)
        self._load_from_disk()

        # 解析缓存目录
        self._parse_cache_dir = config.get("parse_cache_dir", "./data/parse_cache")
        os.makedirs(self._parse_cache_dir, exist_ok=True)

    def _get_model(self) -> SentenceTransformer:
        """懒加载 embedding 模型"""
        if self._model is None:
            emb_config = self._config.get("embedding", {})
            model_name = emb_config.get("model", "BAAI/bge-base-zh-v1.5")
            cache_dir = emb_config.get("cache_dir") or None
            log.info(f"加载 embedding 模型: {model_name}")
            self._model = SentenceTransformer(model_name, cache_folder=cache_dir)
        return self._model

    def add_document(self, filepath: str) -> DocMeta:
        """
        入库文档（解析 + embedding + 加入索引）

        Returns:
            DocMeta 文档元数据
        """
        # 解析（带缓存）
        doc = parse(filepath)
        log.info(f"解析完成: {doc.filename} ({len(doc.paragraphs)} 段, {len(doc.tables)} 表)")

        # 计算 embedding
        model = self._get_model()
        texts = [p.text for p in doc.paragraphs]
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
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
        if filepath.lower().endswith('.pdf'):
            try:
                import fitz
                with fitz.open(filepath) as pdf_doc:
                    page_count = pdf_doc.page_count
            except Exception:
                pass

        # 计算字数
        char_count = sum(len(p.text) for p in doc.paragraphs)

        # 记录元数据
        meta = DocMeta(
            filename=doc.filename,
            filepath=filepath,
            paragraph_count=len(doc.paragraphs),
            table_count=len(doc.tables),
            added_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            page_count=page_count,
            char_count=char_count,
        )
        self._documents[doc.filename] = meta
        log.info(f"已入库: {doc.filename}")
        self._save_to_disk()
        return meta

    def remove_document(self, filename: str) -> bool:
        """
        从库中移除文档

        注意：需要重建索引（删除对应段落的 embedding）
        """
        if filename not in self._documents:
            return False

        # 找到该文档段落的范围
        indices_to_remove = [
            i for i, p in enumerate(self._paragraphs)
            if p.source_file == filename
        ]
        if not indices_to_remove:
            del self._documents[filename]
            return True

        # 移除段落和 embedding
        keep_mask = np.ones(len(self._paragraphs), dtype=bool)
        keep_mask[indices_to_remove] = False

        self._paragraphs = [p for i, p in enumerate(self._paragraphs) if keep_mask[i]]
        if self._embeddings is not None and len(self._embeddings) > 0:
            self._embeddings = self._embeddings[keep_mask]

        self._rebuild_index()
        self._documents[filename].status = "deleted"
        log.info(f"已移除: {filename}")
        self._save_to_disk()
        return True

    def clear_all(self):
        """
        清空知识库（删除所有文档、段落、索引、缓存）

        用于重置整个知识库。
        """
        import shutil
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

    def search(self, query: str, top_k: int = None) -> List[RetrievedChunk]:
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
            results.append(RetrievedChunk(
                text=para.text,
                source_file=para.source_file,
                location=para.location,
                score=score,
                paragraph_index=idx,
            ))

        return results

    def list_documents(self) -> List[DocMeta]:
        """列出所有已入库文档"""
        return [m for m in self._documents.values() if m.status == "active"]

    def get_document(self, filename: str) -> Optional[DocMeta]:
        """获取文档元数据"""
        return self._documents.get(filename)

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
            paras_data = [
                {
                    "text": p.text,
                    "source_file": p.source_file,
                    "page": p.page,
                    "page_end": p.page_end,
                    "chapter": p.chapter,
                    "chapter_title": p.chapter_title,
                    "index": p.index,
                }
                for p in self._paragraphs
            ]
            with open(paras_path, "w", encoding="utf-8") as f:
                json.dump(paras_data, f, ensure_ascii=False)

            # 3. Embeddings
            if self._embeddings is not None and len(self._embeddings) > 0:
                emb_path = os.path.join(self._persist_dir, "embeddings.npy")
                np.save(emb_path, self._embeddings)

            log.info(f"持久化完成: {len(self._documents)} 文档, {len(self._paragraphs)} 段落")
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
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_dict = json.load(f)
            self._documents = {k: DocMeta(**v) for k, v in meta_dict.items()}

            # 2. 段落
            if os.path.exists(paras_path):
                with open(paras_path, "r", encoding="utf-8") as f:
                    paras_data = json.load(f)
                self._paragraphs = [
                    Paragraph(text=p["text"], source_file=p["source_file"],
                              page=p.get("page", 0), page_end=p.get("page_end", 0),
                              chapter=p.get("chapter", ""),
                              chapter_title=p.get("chapter_title", ""),
                              index=p.get("index", 0))
                    for p in paras_data
                ]

            # 3. Embeddings + FAISS
            if os.path.exists(emb_path):
                self._embeddings = np.load(emb_path)
                self._rebuild_index()

            # 回填缺失的 page_count / char_count（所有数据加载完毕后）
            need_save = False
            for fname, meta in self._documents.items():
                if meta.page_count == 0 and meta.filepath and meta.filepath.lower().endswith('.pdf'):
                    try:
                        import fitz
                        with fitz.open(meta.filepath) as pdf_doc:
                            meta.page_count = pdf_doc.page_count
                            need_save = True
                    except Exception:
                        pass
                if meta.char_count == 0:
                    chars = sum(len(p.text) for p in self._paragraphs if p.source_file == fname)
                    if chars > 0:
                        meta.char_count = chars
                        need_save = True
            if need_save:
                self._save_to_disk()

            log.info(f"从磁盘恢复: {len(self._documents)} 文档, {len(self._paragraphs)} 段落")
        except Exception as e:
            log.error(f"从磁盘恢复失败: {e}", exc_info=True)

    # ============================================================
    # 段落查询公共接口
    # ============================================================

    def get_paragraphs_by_file(self, filename: str) -> list:
        """获取指定文档的所有段落"""
        return [p for p in self._paragraphs if p.source_file == filename]

    def get_paragraph_context(self, filename: str, index: int = 0, radius: int = 3) -> list:
        """
        获取指定文档中某段落及其前后上下文

        Args:
            filename: 文档名
            index: 段落在该文档中的序号
            radius: 前后取多少段

        Returns:
            [{text, location, index, is_target}, ...]
        """
        doc_paras = self.get_paragraphs_by_file(filename)
        if not doc_paras:
            return []
        target = min(index, len(doc_paras) - 1)
        start = max(0, target - radius)
        end = min(len(doc_paras), target + radius + 1)
        return [
            {"text": p.text, "location": p.location, "index": i, "is_target": i == target}
            for i, p in enumerate(doc_paras[start:end], start=start)
        ]

    def find_paragraphs(self, filename: str, location: str = "", limit: int = 5) -> list:
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
            if p.source_file != filename:
                continue
            if not location or p.location == location:
                matches.append({"text": p.text, "location": p.location, "chapter": p.chapter})
                if len(matches) >= limit:
                    break
        return matches
