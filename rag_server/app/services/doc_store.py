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

import numpy as np
from doc_parser import Paragraph
from loguru import logger as log
from version_diff.vectorstore import VectorStore

from app.services.parse_cache import cached_parse as parse
from app.services.utils import compute_sha256, get_pdf_page_count


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
    version: str = ""  # 保留字段（不再自动提取，一律用用户填写的 label）
    label: str = ""  # 用户上传时补充的描述（如版本号），列表醒目显示


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
        self._model: object | None = None
        self._documents: dict = {}  # {filename: DocMeta}
        self._paragraphs: list[Paragraph] = []
        from app.services.retriever import FaissRetriever

        self._retriever = FaissRetriever()

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
        # ★ config_hash 必须与 review_runner._build_engine_config 一致：
        #   两者都按「注入 parse_config 后的 embedding 段」计算哈希，否则同一文档
        #   在预审核阶段写入的向量缓存（key 含 hash）与入库时查询的 key 不同，
        #   导致入库时缓存未命中、重新计算全部 embedding。
        #   doc_store 的 config 来自 config_store.to_dict()，embedding 段不含
        #   parse_config，这里按 build_parse_config 同构逻辑注入后参与哈希。
        vector_cache_dir = config.get("vector_cache_dir", "")
        _emb_cfg = dict(config.get("embedding", {}))
        _pre_review = config.get("pre_review") or {}
        _parse_backend = _pre_review.get("parse_backend", "auto")
        _extract = {"backend": _parse_backend}
        if _parse_backend == "docling":
            _extract["docling_device"] = _pre_review.get("docling_device", "auto")
            _batch = _pre_review.get("docling_batch_size", 0)
            if _batch:
                _extract["docling_batch_size"] = int(_batch)
            _extract["docling_merge_split_paras"] = True
            _extract["docling_strip_header_prefix"] = True
        _emb_cfg["parse_config"] = {"extract": _extract}
        cfg_hash = VectorStore.compute_config_hash(_emb_cfg.get("parse_config", {}), _emb_cfg)
        self._vector_store = VectorStore(cache_dir=vector_cache_dir, config_hash=cfg_hash)

        # 解析配置（与预审核一致：pre_review.parse_backend，默认 docling）。
        # 入库（confirm）时复用同一解析配置，命中预审核阶段的 parse_cache，
        # 避免走 auto(PyMuPDF) 重新解析且缓存签名不匹配。
        pre_review = config.get("pre_review") or {}
        _parse_backend = pre_review.get("parse_backend", "auto")
        _extract = {"backend": _parse_backend}
        if _parse_backend == "docling":
            _extract["docling_device"] = pre_review.get("docling_device", "auto")
            _batch = pre_review.get("docling_batch_size", 0)
            if _batch:
                _extract["docling_batch_size"] = int(_batch)
            # 与 review_runner.build_parse_config 保持同构（含后处理开关），
            # 保证 confirm 入库命中预审核阶段的 parse_cache（签名一致）
            _extract["docling_merge_split_paras"] = True
            _extract["docling_strip_header_prefix"] = True
        self._parse_config = {"extract": _extract}

    @staticmethod
    def _compute_file_hash(filepath: str) -> str:
        """计算文件 SHA-256"""
        return compute_sha256(filepath)

    @staticmethod
    def _tables_to_paragraphs(tables: list) -> list:
        """把表格转成可检索的 Paragraph 列表（表格内容参与 RAG 向量检索）。

        每个表格转成一段结构化文本（markdown 行拼接），location 标
        "表格: 章节"，source_file 复用表格的（后续 add_document 统一覆盖为 doc_id）。
        """
        from doc_parser import Paragraph

        out = []
        for t in tables:
            md = t.to_markdown() if hasattr(t, "to_markdown") else ""
            if not md:
                continue
            out.append(
                Paragraph(
                    text=md,
                    page=t.page,
                    page_end=getattr(t, "page_end", 0),
                    chapter=getattr(t, "chapter", ""),
                    chapter_title=getattr(t, "chapter_title", ""),
                    source_file=getattr(t, "source_file", ""),
                    index=len(out) + 1,
                    order=getattr(t, "order", 0),
                )
            )
        return out

    def _get_model(self):
        """懒加载 embedding 模型（fastembed 适配器）"""
        if self._model is None:
            from version_diff.device_utils import load_embedding_model

            self._model = load_embedding_model(self._config.get("embedding", {}))
        return self._model

    def add_document(self, filepath: str, original_filename: str = "", label: str = "") -> DocMeta:
        """
        入库文档（解析 + embedding + 加入索引）

        Args:
            filepath: 文件路径（可能是安全文件名，如 SHA256.pdf）
            original_filename: 原始文件名（用于显示，如 用户上传时的文件名）

        Returns:
            DocMeta 文档元数据
        """
        # 解析（带缓存，使用配置的 parse_cache_dir 与解析后端，保证与预审核一致）
        doc = parse(filepath, config=self._parse_config, cache_dir=self._parse_cache_dir)
        # 用原始文件名覆盖（parse 返回的是磁盘文件名，可能是 SHA-256 安全名）
        if original_filename:
            doc.filename = original_filename
        log.info(f"解析完成: {doc.filename} ({len(doc.paragraphs)} 段, {len(doc.tables)} 表)")

        # 表格也转成可检索段落：表格本身不参与段落式 chunk，但其内容对 RAG 有
        # 价值（如"修订记录表里 5.1-1~5 是什么"），转成结构化文本加入向量索引。
        indexable = list(doc.paragraphs) + self._tables_to_paragraphs(doc.tables)

        # 计算 embedding（VectorStore 内部有缓存，自动复用）
        model = self._get_model()
        embeddings, _index = self._vector_store.get_or_compute(doc.filename, indexable, model)
        embeddings = np.array(embeddings).astype(np.float32)

        # 追加到全局（段落 + 表格统一进索引，检索时按 location 区分）
        start_idx = len(self._paragraphs)
        self._paragraphs.extend(indexable)
        self._retriever.add(embeddings, start_index=start_idx)

        # 计算页数
        page_count = get_pdf_page_count(filepath)

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
            label=label.strip()[:60],
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
        indices_to_remove = [i for i, p in enumerate(self._paragraphs) if p.source_file == doc_id]
        if not indices_to_remove:
            del self._documents[doc_id]
            return True

        # 移除段落和 embedding
        keep_mask = np.ones(len(self._paragraphs), dtype=bool)
        keep_mask[indices_to_remove] = False

        self._paragraphs = [p for i, p in enumerate(self._paragraphs) if keep_mask[i]]
        self._retriever.remove(keep_mask)
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
        self._retriever.clear()
        # 清空磁盘持久化文件（保留目录）；目录不存在时先创建，避免空库清空报错
        os.makedirs(self._persist_dir, exist_ok=True)
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
        if self._retriever.count == 0 or len(self._paragraphs) == 0:
            return []

        retrieval_config = self._config.get("retrieval", {})
        top_k = top_k or retrieval_config.get("top_k", 5)
        threshold = retrieval_config.get("similarity_threshold", 0.3)

        # 查询 embedding
        model = self._get_model()
        q_emb = model.encode([query], normalize_embeddings=True).astype(np.float32)

        # 向量检索（Retriever 抽象，未来可换 pgvector/OpenSearch）
        scores, indices = self._retriever.search(q_emb, top_k)

        # 封装结果（scores/indices 为一维，已按相似度降序）
        results = []
        for i in range(len(indices)):
            idx = int(indices[i])
            score = float(scores[i])
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

            # 3. 向量索引（交给 Retriever 持久化）
            self._retriever.save(self._persist_dir)

            log.info(f"持久化完成: {len(self._documents)} 文档, {len(self._paragraphs)} 段落")
        except Exception as e:
            log.error(f"持久化失败: {e}", exc_info=True)

    def _load_from_disk(self):
        """从磁盘恢复状态"""
        meta_path = os.path.join(self._persist_dir, "documents.json")
        paras_path = os.path.join(self._persist_dir, "paragraphs.json")
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

            # 3. 向量索引（Retriever 从磁盘恢复）
            self._retriever.load(self._persist_dir)

            # 回填缺失的 page_count / char_count（所有数据加载完毕后）
            need_save = False
            for fname, meta in self._documents.items():
                if meta.page_count == 0 and meta.filepath and meta.filepath.lower().endswith(".pdf"):
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

    def get_paragraphs_by_file(self, doc_id: str) -> list:
        """获取指定文档的所有段落（按 doc_id 或 filename）"""
        # 先尝试直接匹配 doc_id
        result = [p for p in self._paragraphs if p.source_file == doc_id]
        if result:
            return result
        # 兼容：尝试用 filename 匹配
        return [p for p in self._paragraphs if p.source_file.startswith(doc_id + "#") or p.source_file == doc_id]

    def get_paragraph_context(self, doc_id: str, index: int = 0, radius: int = 3) -> list:
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

    def get_neighbor_texts(self, global_index: int, radius: int = 3) -> list[dict]:
        """按全局索引取同文档前后 radius 个相邻段落（供检索上下文扩展）。

        Args:
            global_index: 全局 _paragraphs 索引（search 返回的 paragraph_index）
            radius: 前后取多少段

        Returns:
            [{"text", "location", "is_target"}, ...]，按文档内顺序
        """
        if not (0 <= global_index < len(self._paragraphs)):
            return []
        target = self._paragraphs[global_index]
        doc_id = target.source_file
        # 收集同文档所有段落（保持顺序）
        doc_paras = [
            (i, p) for i, p in enumerate(self._paragraphs) if p.source_file == doc_id
        ]
        if not doc_paras:
            return []
        # 定位目标在文档内的位置
        target_doc_pos = None
        for pos, (i, p) in enumerate(doc_paras):
            if i == global_index:
                target_doc_pos = pos
                break
        if target_doc_pos is None:
            return []
        start = max(0, target_doc_pos - radius)
        end = min(len(doc_paras), target_doc_pos + radius + 1)
        return [
            {
                "text": p.text,
                "location": p.location,
                "is_target": (i == global_index),
            }
            for i, p in doc_paras[start:end]
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
                matches.append({"text": p.text, "location": p.location, "chapter": p.chapter})
                if len(matches) >= limit:
                    break
        return matches
