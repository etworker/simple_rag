"""
DiffEngine — 文档差异检测引擎主入口

编排流程:
    1. 文档解析 (doc_parser)
    2. 全局向量库构建/增量更新 (vectorstore)
    3. 跨文档语义检索 (FAISS)
    4. 字级 Diff (matcher)
    5. 规则预过滤 (prefilter)
    6. LLM 矛盾判断 (judge)
    7. 结果封装 (DiffResult)

公共 API:
    engine.add(filepath)            # 添加文档到已有库
    engine.pre_review(filepath)     # 预审核新文档
    engine.check_conflicts(chunks)  # 问答时冲突检测
"""

import logging
import time
from collections.abc import Callable

import numpy as np
from sentence_transformers import SentenceTransformer

from version_diff.config import Config
from version_diff.judge import filter_diffs
from version_diff.matcher import compute_diff
from version_diff.models import DiffResult, Inconsistency, VersionChange, VersionDiffResult
from version_diff.vectorstore import VectorStore

log = logging.getLogger("version_diff.engine")


class DiffEngine:
    """
    文档差异检测引擎

    Example:
        from version_diff import DiffEngine

        engine = DiffEngine(config={
            "embedding": {"model": "BAAI/bge-base-zh-v1.5"},
            "llm": {"provider": "bedrock_converse", "model": "zai.glm-4.7-flash"},
            "diff": {"similarity_threshold": 0.80},
        })
        engine.add("a.pdf")
        engine.add("b.pdf")
        result = engine.pre_review("new.pdf", on_progress=my_callback)
        if not result.is_safe:
            print(result.report())
    """

    def __init__(self, config: dict = None):
        self.config = Config.from_dict(config or {})
        self._documents = {}  # {filename: Document}
        self._all_paras = []  # 所有段落（带 source_file）
        self._all_embeddings = None  # 全局 embedding 矩阵
        self._model = None  # SentenceTransformer (lazy load)
        cache_dir = self.config.cache.get("vector_cache_dir", "")
        # 根据配置计算缓存哈希，配置变化时缓存自动失效
        cfg_hash = VectorStore.compute_config_hash(
            self.config.embedding.get("parse_config", {}),
            self.config.embedding,
        )
        self._vector_store = VectorStore(cache_dir=cache_dir, config_hash=cfg_hash)

    def _get_model(self):
        """懒加载 embedding 模型"""
        if self._model is None:
            from version_diff.device_utils import (
                embedding_model_kwargs,
                log_device_status,
                resolve_embedding_device,
            )

            model_name = self.config.embedding.get("model", "")
            cache_dir = self.config.embedding.get("cache_dir") or None
            device = resolve_embedding_device(self.config.embedding)
            kwargs = embedding_model_kwargs(self.config.embedding)
            log.info(f"加载 embedding 模型: {model_name} (device={device})")
            m_kwargs = {"cache_folder": cache_dir}
            m_kwargs.update(kwargs)
            self._model = SentenceTransformer(model_name, device=device, **m_kwargs)
            log_device_status(device)
        return self._model

    def _notify(self, callback, step, percent, message):
        """发送进度通知"""
        if callback:
            try:
                callback(step, percent, message)
            except Exception:
                pass

    def add(self, filepath: str) -> None:
        """
        添加文档到已有库

        解析文档 → 计算 embedding → 追加到全局向量库
        """
        from doc_parser import parse

        doc = parse(filepath, config=self.config.embedding.get("parse_config"))
        self._documents[doc.filename] = doc

        # 计算 embedding
        model = self._get_model()
        emb, _ = self._vector_store.get_or_compute(doc.filename, doc.paragraphs, model)

        # 追加到全局列表
        start_idx = len(self._all_paras)
        self._all_paras.extend(doc.paragraphs)

        if self._all_embeddings is None:
            self._all_embeddings = emb
        else:
            self._all_embeddings = np.vstack([self._all_embeddings, emb])

        log.info(f"已加载到对比引擎: {doc.filename} ({len(doc.paragraphs)} 段落)")

    def pre_review(
        self, filepath: str, on_progress: Callable | None = None
    ) -> DiffResult:
        """
        预审核：检测新文档与已有文档的矛盾

        Args:
            filepath: 新文档路径
            on_progress: 进度回调 fn(step, percent, message)
                step: "parsing" | "embedding" | "searching" | "diffing" | "judging" | "done"
                percent: 0.0 ~ 1.0
                message: 人类可读描述

        Returns:
            DiffResult 包含不一致列表和统计信息
        """
        import faiss

        from doc_parser import parse

        threshold = self.config.diff.get("similarity_threshold", 0.80)
        top_k = self.config.diff.get("top_k", 3)

        # Step 1: 解析新文档（始终执行）
        self._notify(on_progress, "parsing", 0.0, "解析文档...")
        t0 = time.time()
        new_doc = parse(filepath, config=self.config.embedding.get("parse_config"))
        log.info(
            f"解析完成: {new_doc.filename} ({len(new_doc.paragraphs)} 段落, {time.time() - t0:.1f}s)"
        )

        # Step 2: 计算新文档 embedding（始终执行）
        self._notify(on_progress, "embedding", 0.2, "计算语义向量...")
        t1 = time.time()
        model = self._get_model()
        new_emb, _ = self._vector_store.get_or_compute(
            new_doc.filename, new_doc.paragraphs, model
        )
        log.info(f"Embedding 完成 ({time.time() - t1:.1f}s)")

        # 如果库中没有已有文档，跳过对比步骤，直接返回安全结果
        if not self._documents:
            self._notify(on_progress, "done", 1.0, "完成（库中无已有文档，无需对比）")
            log.info("库中无已有文档，跳过对比步骤")
            return DiffResult()

        # Step 3: 在已有全局库中检索相似段落
        self._notify(on_progress, "searching", 0.4, "跨文档语义检索...")
        t2 = time.time()

        # 构建已有文档的 FAISS index
        from version_diff.device_utils import maybe_index_to_gpu

        dim = self._all_embeddings.shape[1]
        cpu_index = faiss.IndexFlatIP(dim)
        norms_existing = np.linalg.norm(self._all_embeddings, axis=1, keepdims=True)
        norms_existing[norms_existing == 0] = 1
        normalized_existing = (self._all_embeddings / norms_existing).astype(np.float32)
        cpu_index.add(normalized_existing)
        index, used_gpu = maybe_index_to_gpu(
            cpu_index, gpu_id=self.config.embedding.get("gpu_id", 0)
        )
        if used_gpu:
            log.info("  FAISS 检索在 GPU 上执行")

        # 归一化新文档 embedding
        norms_new = np.linalg.norm(new_emb, axis=1, keepdims=True)
        norms_new[norms_new == 0] = 1
        normalized_new = (new_emb / norms_new).astype(np.float32)

        # 检索
        actual_k = min(top_k, len(self._all_paras))
        similarities, indices = index.search(normalized_new, actual_k)

        # 收集候选对
        candidates = []
        seen_pairs = set()
        for i in range(len(new_doc.paragraphs)):
            for k in range(actual_k):
                j = int(indices[i][k])
                sim = float(similarities[i][k])
                if sim < threshold:
                    continue
                pair_key = (i, j)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                candidates.append((new_doc.paragraphs[i], self._all_paras[j], sim))

        log.info(f"检索完成: {len(candidates)} 个候选对 ({time.time() - t2:.1f}s)")

        # Step 4: 字级 Diff
        self._notify(on_progress, "diffing", 0.6, "计算文本差异...")
        t3 = time.time()
        diff_items = []
        for para_a, para_b, sim in candidates:
            if para_a.text.strip() == para_b.text.strip():
                continue
            item = compute_diff(para_a, para_b, sim)
            if item.has_changes:
                diff_items.append(item)
        log.info(f"Diff 完成: {len(diff_items)} 处有差异 ({time.time() - t3:.1f}s)")

        # Step 5: LLM 矛盾判断
        self._notify(on_progress, "judging", 0.7, "LLM 矛盾判断...")
        t4 = time.time()
        judge_result = filter_diffs(
            diff_items, llm_config=self.config.llm, judge_config=self.config.judge
        )
        log.info(
            f"判断完成: {len(judge_result.inconsistent_items)} 处矛盾 ({time.time() - t4:.1f}s)"
        )

        # Step 6: 封装结果（使用结构化字段，不再解析字符串）
        self._notify(on_progress, "done", 1.0, "完成")

        result = DiffResult(
            inconsistencies=[
                Inconsistency(
                    point=item.llm_point or item.description or "内容差异",
                    doc_a_file=item.para_a.source_file,
                    doc_a_location=item.para_a.location,
                    doc_a_says=item.llm_doc_a_says or item.description or "",
                    doc_b_file=item.para_b.source_file,
                    doc_b_location=item.para_b.location,
                    doc_b_says=item.llm_doc_b_says or item.description or "",
                    similarity=item.similarity,
                )
                for item in judge_result.inconsistent_items
            ],
            total_candidates=len(candidates),
            rule_filtered=judge_result.rule_filtered,
            llm_judged=judge_result.llm_judged,
        )

        return result

    def check_conflicts(self, retrieved_passages: list[dict]) -> list[Inconsistency]:
        """
        问答冲突检测：检查 RAG 检索结果中是否存在矛盾

        对来自不同文档的检索结果两两配对，
        使用 LLM 判断是否存在矛盾描述。

        Args:
            retrieved_passages: RAG 检索到的段落列表
                每项: {"text": str, "source_file": str, "location": str}

        Returns:
            矛盾列表（空列表表示无冲突）
        """
        if len(retrieved_passages) < 2:
            return []

        # 构造跨文档段落对
        from doc_parser.models import Paragraph

        diff_items = []
        for i, chunk_a in enumerate(retrieved_passages):
            for j, chunk_b in enumerate(retrieved_passages):
                if i >= j:
                    continue
                # 只比较来自不同文档的
                if chunk_a.get("source_file") == chunk_b.get("source_file"):
                    continue
                # 文本完全相同则跳过
                text_a = chunk_a.get("text", "")
                text_b = chunk_b.get("text", "")
                if text_a.strip() == text_b.strip():
                    continue
                # 构造 Paragraph 和 TextDiffItem
                para_a = Paragraph(
                    text=text_a,
                    source_file=chunk_a.get("source_file", ""),
                )
                para_b = Paragraph(
                    text=text_b,
                    source_file=chunk_b.get("source_file", ""),
                )
                item = compute_diff(para_a, para_b, similarity=0.8)
                if item.has_changes:
                    diff_items.append(item)

        if not diff_items:
            return []

        # 复用 judge.py 的 LLM 判断逻辑
        judge_result = filter_diffs(
            diff_items,
            llm_config=self.config.llm,
            judge_config=self.config.judge,
        )

        return [
            Inconsistency(
                point=item.llm_point or item.description or "内容差异",
                doc_a_file=item.para_a.source_file,
                doc_a_location=item.para_a.location,
                doc_a_says=item.llm_doc_a_says or "",
                doc_b_file=item.para_b.source_file,
                doc_b_location=item.para_b.location,
                doc_b_says=item.llm_doc_b_says or "",
                similarity=item.similarity,
            )
            for item in judge_result.inconsistent_items
        ]

    def version_compare(
        self, old_filepath: str, new_filepath: str, on_progress: Callable | None = None
    ) -> VersionDiffResult:
        """
        版本对比：两个文件直接对比，列出全部差异

        不经过 LLM 矛盾判断，直接用语义配对 + 文本 diff 输出所有变更。
        适用于同一文档的新旧版本对比。

        Args:
            old_filepath: 旧版本文件路径
            new_filepath: 新版本文件路径
            on_progress: 进度回调

        Returns:
            VersionDiffResult 包含所有变更
        """
        from doc_parser import parse
        from version_diff.matcher import pair_paragraphs

        self._notify(on_progress, "parsing", 0.1, "解析新旧版本...")

        parse_config = self.config.embedding.get("parse_config")
        old_doc = parse(old_filepath, config=parse_config)
        new_doc = parse(new_filepath, config=parse_config)

        self._notify(on_progress, "embedding", 0.3, "计算语义向量...")

        model = self._get_model()
        threshold = self.config.diff.get("similarity_threshold", 0.80)

        self._notify(on_progress, "diffing", 0.5, "配对与差异计算...")

        # 语义配对
        pairs = pair_paragraphs(
            old_doc.paragraphs, new_doc.paragraphs, model,
            threshold=threshold,
            file_a=old_filepath, file_b=new_filepath,
            vector_store=self._vector_store,
        )

        changes = []
        paired_old = set()
        paired_new = set()

        # 处理配对成功的段落（modified）
        for old_idx, new_idx, sim in pairs:
            paired_old.add(old_idx)
            paired_new.add(new_idx)
            old_para = old_doc.paragraphs[old_idx]
            new_para = new_doc.paragraphs[new_idx]

            # 完全相同则跳过
            if old_para.text.strip() == new_para.text.strip():
                continue

            changes.append(VersionChange(
                change_type="modified",
                section=new_para.chapter_title or new_para.chapter or "",
                location=new_para.location,
                old_text=old_para.text.strip(),
                new_text=new_para.text.strip(),
                summary="",  # 后续可用 LLM 生成摘要
                similarity=sim,
            ))

        # 旧版有但新版没有的（removed）
        for i, para in enumerate(old_doc.paragraphs):
            if i not in paired_old:
                changes.append(VersionChange(
                    change_type="removed",
                    section=para.chapter_title or para.chapter or "",
                    location=para.location,
                    old_text=para.text.strip(),
                    new_text="",
                    summary="",
                ))

        # 新版有但旧版没有的（added）
        for i, para in enumerate(new_doc.paragraphs):
            if i not in paired_new:
                changes.append(VersionChange(
                    change_type="added",
                    section=para.chapter_title or para.chapter or "",
                    location=para.location,
                    old_text="",
                    new_text=para.text.strip(),
                    summary="",
                ))

        # ====== 表格对比 ======
        table_changes = self._compare_tables(old_doc.tables, new_doc.tables)
        changes.extend(table_changes)

        self._notify(on_progress, "done", 1.0, "版本对比完成")

        return VersionDiffResult(
            changes=changes,
            old_paragraph_count=len(old_doc.paragraphs),
            new_paragraph_count=len(new_doc.paragraphs),
        )

    def _compare_tables(self, old_tables: list, new_tables: list) -> list[VersionChange]:
        """
        表格版本对比：配对新旧表格，逐行比较差异

        策略：
        1. 按表头内容相似度配对表格（不靠位置序号）
        2. 配对成功的表格，按首列关键字对齐行
        3. 输出行级差异（modified/added/removed）
        """
        from difflib import SequenceMatcher

        changes = []

        def table_header_text(table) -> str:
            """提取表格表头文本用于配对"""
            if table.rows:
                return " ".join(str(cell) for cell in table.rows[0])
            return ""

        def row_key(row) -> str:
            """行的配对键：首列文本"""
            return str(row[0]).strip() if row else ""

        def row_text(row) -> str:
            """行的完整文本"""
            return " | ".join(str(cell).strip() for cell in row)

        # 1. 配对表格（按表头相似度）
        paired_old = set()
        paired_new = set()
        table_pairs = []

        for i, old_t in enumerate(old_tables):
            old_header = table_header_text(old_t)
            best_match = -1
            best_score = 0.0
            for j, new_t in enumerate(new_tables):
                if j in paired_new:
                    continue
                new_header = table_header_text(new_t)
                score = SequenceMatcher(None, old_header, new_header).ratio()
                if score > best_score and score >= 0.5:
                    best_score = score
                    best_match = j
            if best_match >= 0:
                table_pairs.append((i, best_match))
                paired_old.add(i)
                paired_new.add(best_match)

        # 2. 对配对的表格做行级 diff
        for old_idx, new_idx in table_pairs:
            old_t = old_tables[old_idx]
            new_t = new_tables[new_idx]
            section = old_t.chapter_title or old_t.location
            header_text = row_text(old_t.rows[0]) if old_t.rows else "表格"

            # 跳过表头行（index 0），对数据行按首列对齐
            old_rows = {row_key(r): r for r in old_t.rows[1:]} if len(old_t.rows) > 1 else {}
            new_rows = {row_key(r): r for r in new_t.rows[1:]} if len(new_t.rows) > 1 else {}

            all_keys = list(dict.fromkeys(list(old_rows.keys()) + list(new_rows.keys())))

            for key in all_keys:
                if not key:
                    continue
                old_r = old_rows.get(key)
                new_r = new_rows.get(key)

                if old_r and new_r:
                    old_txt = row_text(old_r)
                    new_txt = row_text(new_r)
                    if old_txt != new_txt:
                        changes.append(VersionChange(
                            change_type="modified",
                            section=f"表格: {section}",
                            location=f"行: {key}",
                            old_text=old_txt,
                            new_text=new_txt,
                            summary="",
                            similarity=SequenceMatcher(None, old_txt, new_txt).ratio(),
                        ))
                elif old_r and not new_r:
                    changes.append(VersionChange(
                        change_type="removed",
                        section=f"表格: {section}",
                        location=f"行: {key}",
                        old_text=row_text(old_r),
                        new_text="",
                        summary="",
                    ))
                elif new_r and not old_r:
                    changes.append(VersionChange(
                        change_type="added",
                        section=f"表格: {section}",
                        location=f"行: {key}",
                        old_text="",
                        new_text=row_text(new_r),
                        summary="",
                    ))

        return changes
