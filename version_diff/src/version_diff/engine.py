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

import math
import os
import re
import time
from collections.abc import Callable
from contextlib import suppress

import numpy as np
from loguru import logger as log

from version_diff.config import Config
from version_diff.judge import filter_diffs
from version_diff.llm_util import call_llm_json
from version_diff.matcher import compute_diff
from version_diff.models import DiffResult, Inconsistency, VersionChange, VersionDiffResult
from version_diff.normalization import (
    is_tracking_table_row as _is_tracking_table_row,
)
from version_diff.normalization import (
    normalize_text,
)
from version_diff.normalization import (
    strip_configured_noise as _strip_configured_noise,
)
from version_diff.normalization import (
    strip_revision_noise as _strip_revision_noise,
)
from version_diff.table_diff import compare_tables
from version_diff.vectorstore import VectorStore

# 兼容已有的私有调用入口。
_normalize_text = normalize_text


def _classify_change(category: str, change):
    """为 dataclass 或 dict 形式的变更写入分类标签。"""
    if isinstance(change, dict):
        change["category"] = category
    else:
        change.category = category
    return change


# 版本过滤 prompt（随包发布，外部文件优先）
_VERSION_FILTER_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompts", "version_filter.txt")


def _load_version_filter_prompt() -> str:
    """从随包 .txt 文件加载版本过滤 prompt 模板（缺失则用内置兜底）"""
    fallback = (
        "你是文档版本对比专家。请判断以下 {count} 处新旧版本文本差异是否为实质性变更。"
        '回复 JSON 数组：{{"index": N, "keep": true/false, "summary": "..."}}'
    )
    try:
        with open(_VERSION_FILTER_PROMPT_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        log.warning(f"加载版本过滤 prompt 失败，使用兜底: {e}")
        return fallback


class DiffEngine:
    """
    文档差异检测引擎

    Example:
        from version_diff import DiffEngine

        engine = DiffEngine(config={
            "embedding": {"model": "BAAI/bge-small-zh-v1.5"},
            "llm": {"provider": "bedrock_converse", "model": "zai.glm-4.7-flash"},
            "diff": {"similarity_threshold": 0.80},
        })
        engine.add("a.pdf")
        engine.add("b.pdf")
        result = engine.pre_review("new.pdf", on_progress=my_callback)
        if not result.is_safe:
            print(result.report())
    """

    def __init__(self, config: dict | None = None):
        self.config = Config.from_dict(config or {})
        self._documents = {}  # {filename: Document}
        self._all_paras = []  # 所有段落（带 source_file）
        self._all_embeddings = None  # 全局 embedding 矩阵
        self._model = None  # SentenceTransformer (lazy load)
        cache_dir = self.config.cache.get("vector_cache_dir", "")
        # 解析缓存目录（version_compare 用，避免重复解析慢后端）
        self._parse_cache_dir = self.config.cache.get("parse_cache_dir", "")
        # 根据配置计算缓存哈希，配置变化时缓存自动失效
        cfg_hash = VectorStore.compute_config_hash(
            self.config.embedding.get("parse_config", {}),
            self.config.embedding,
        )
        self._vector_store = VectorStore(cache_dir=cache_dir, config_hash=cfg_hash)

    def _get_model(self):
        """懒加载 embedding 模型"""
        if self._model is None:
            from version_diff.device_utils import load_embedding_model

            self._model = load_embedding_model(self.config.embedding)
        return self._model

    def _notify(self, callback, step, percent, message):
        """发送进度通知"""
        if callback:
            with suppress(Exception):
                callback(step, percent, message)

    def add(self, filepath: str) -> None:
        """
        添加文档到已有库

        解析文档 → 计算 embedding → 追加到全局向量库
        """
        from version_diff.parse_cache import cached_parse

        doc = cached_parse(filepath, config=self.config.embedding.get("parse_config"), cache_dir=self._parse_cache_dir)
        self._documents[doc.filename] = doc

        # 计算 embedding
        model = self._get_model()
        emb, _ = self._vector_store.get_or_compute(doc.filename, doc.paragraphs, model)

        # 追加到全局列表
        self._all_paras.extend(doc.paragraphs)

        if self._all_embeddings is None:
            self._all_embeddings = emb
        else:
            self._all_embeddings = np.vstack([self._all_embeddings, emb])

        log.info(f"已加载到对比引擎: {doc.filename} ({len(doc.paragraphs)} 段落)")

    def pre_review(
        self,
        filepath: str,
        on_progress: Callable | None = None,
        doc_filename: str = "",
        on_candidates: Callable | None = None,
        on_judge_batch: Callable | None = None,
    ) -> DiffResult:
        """
        预审核：检测新文档与已有文档的矛盾

        Args:
            filepath: 新文档路径
            on_progress: 进度回调 fn(step, percent, message)
            doc_filename: 可读文件名（覆盖 source_file 中的 SHA256 名）
            on_candidates: 候选对检索完成后回调 fn(candidate_count, diff_count)
            on_judge_batch: 每批 LLM 判定后回调 fn(batch_idx, total_batches, new_inconsistency_dicts)
                           其中 inconsistency_dicts 为可直接序列化的 dict 列表

        Returns:
            DiffResult 包含不一致列表和统计信息
        """
        from doc_parser import parse

        threshold = self.config.diff.get("similarity_threshold", 0.80)
        top_k = self.config.diff.get("top_k", 3)

        # Step 1: 解析新文档（始终执行，带缓存避免重复解析慢后端）
        self._notify(on_progress, "parsing", 0.0, "解析文档...")
        t0 = time.time()
        from version_diff.parse_cache import cached_parse

        new_doc = cached_parse(filepath, config=self.config.embedding.get("parse_config"), cache_dir=self._parse_cache_dir)
        # ★ 用可读文件名覆盖 SHA256 哈希名，确保矛盾列表前端显示友好
        if doc_filename:
            new_doc.filename = doc_filename
            for p in new_doc.paragraphs:
                p.source_file = doc_filename
            for t in new_doc.tables:
                t.source_file = doc_filename
        log.info(f"解析完成: {new_doc.filename} ({len(new_doc.paragraphs)} 段落, {time.time() - t0:.1f}s)")

        # Step 2: 计算新文档 embedding（始终执行）
        self._notify(on_progress, "embedding", 0.2, "计算语义向量...")
        t1 = time.time()
        model = self._get_model()
        new_emb, _ = self._vector_store.get_or_compute(new_doc.filename, new_doc.paragraphs, model)
        log.info(f"Embedding 完成 ({time.time() - t1:.1f}s)")

        # 如果库中没有已有文档，跳过对比步骤，直接返回安全结果
        if not self._documents:
            self._notify(on_progress, "done", 1.0, "完成（库中无已有文档，无需对比）")
            log.info("库中无已有文档，跳过对比步骤")
            return DiffResult()

        # Step 3: 在已有全局库中检索相似段落
        self._notify(on_progress, "searching", 0.4, "跨文档语义检索...")
        t2 = time.time()
        candidates = self._retrieve_candidates(new_doc, new_emb, threshold, top_k)
        log.info(f"检索完成: {len(candidates)} 个候选对 ({time.time() - t2:.1f}s)")

        # Step 4: 字级 Diff
        self._notify(on_progress, "diffing", 0.6, "计算文本差异...")
        t3 = time.time()
        diff_items = []
        for para_a, para_b, sim in candidates:
            # 路径 1：strip 后完全相同 → 直接跳过
            if para_a.text.strip() == para_b.text.strip():
                continue
            # 路径 2：归一化后相同（消除 PDF 解析差异）→ 跳过，避免浪费 LLM
            if normalize_text(para_a.text) == normalize_text(para_b.text):
                continue
            item = compute_diff(para_a, para_b, sim)
            if item.has_changes:
                diff_items.append(item)
        log.info(f"Diff 完成: {len(diff_items)} 处有差异 ({time.time() - t3:.1f}s)")

        # 通知调用方"候选已就绪"（前端可立即显示 N 个候选）
        if on_candidates:
            on_candidates(len(candidates), len(diff_items))

        # Step 5: LLM 矛盾判断（支持增量回调）
        self._notify(on_progress, "judging", 0.7, "LLM 矛盾判断...")
        t4 = time.time()

        # 将 TextDiffItem 转换为可序列化 dict（供前端增量展示）
        def _item_to_dict(item) -> dict:
            return {
                "point": item.llm_point or item.description or "内容差异",
                "doc_a_file": item.para_a.source_file,
                "doc_a_location": item.para_a.location,
                "doc_a_says": item.llm_doc_a_says or item.description or "",
                "doc_b_file": item.para_b.source_file,
                "doc_b_location": item.para_b.location,
                "doc_b_says": item.llm_doc_b_says or item.description or "",
                "similarity": item.similarity,
            }

        # on_judge_batch 回调包装：把每批新发现的 TextDiffItem 转为 dict
        _on_batch_callback = None
        if on_judge_batch is not None:

            def _on_batch_callback(batch_idx, total_batches, new_items):
                dicts = [_item_to_dict(i) for i in new_items]
                on_judge_batch(batch_idx, total_batches, dicts)

        judge_result = filter_diffs(
            diff_items,
            llm_config=self.config.llm,
            judge_config=self.config.judge,
            on_batch=_on_batch_callback,
        )
        log.info(
            f"判断完成: {len(judge_result.inconsistent_items)} 处矛盾"
            f" (+{len(judge_result.suspect_items)} 疑似)（跨文档） ({time.time() - t4:.1f}s)"
        )

        # Step 6: 封装结果（含去重）
        self._notify(on_progress, "done", 1.0, "完成")

        inconsistencies = [
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
        ]
        suspects = [
            Inconsistency(
                point=item.llm_point or item.description or "疑似差异",
                doc_a_file=item.para_a.source_file,
                doc_a_location=item.para_a.location,
                doc_a_says=item.llm_doc_a_says or item.description or "",
                doc_b_file=item.para_b.source_file,
                doc_b_location=item.para_b.location,
                doc_b_says=item.llm_doc_b_says or item.description or "",
                similarity=item.similarity,
            )
            for item in judge_result.suspect_items
        ]

        # 去重：合并语义高度相似的矛盾项
        inconsistencies, dedup_count = self._dedup_inconsistencies(inconsistencies)
        if dedup_count:
            log.info(f"  去重: 合并 {dedup_count} 处重复，保留 {len(inconsistencies)} 处")

        result = DiffResult(
            inconsistencies=inconsistencies,
            suspects=suspects,
            total_candidates=len(candidates),
            rule_filtered=judge_result.rule_filtered,
            llm_judged=judge_result.llm_judged,
            dedup_count=dedup_count,
        )

        return result

    def _dedup_inconsistencies(
        self, inconsistencies: list[Inconsistency], threshold: float = 0.85
    ) -> tuple[list[Inconsistency], int]:
        """
        对不一致列表进行语义去重。

        当新文档的一个段落与已有库中多个相似段落配对时，会产生相同矛盾的重复副本。
        本方法将 point + doc_a_says + doc_b_says 的 embedding 余弦相似度超过阈值者合并。

        Args:
            inconsistencies: 原始不一致列表
            threshold: 合并阈值（余弦相似度），默认 0.85

        Returns:
            (去重后的列表, 合并掉的数量)
        """
        if len(inconsistencies) < 2:
            return inconsistencies, 0

        import numpy as np

        # 构造每条不一致的文本表示
        texts = []
        for inc in inconsistencies:
            text = f"{inc.point} | {inc.doc_a_says} | {inc.doc_b_says}"
            texts.append(text)

        # 用已有 embedding 模型计算向量
        model = self._get_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        embeddings = np.array(embeddings, dtype=np.float32)

        # 贪心聚类：按相似度合并
        kept_indices = []
        merged = set()
        for i in range(len(inconsistencies)):
            if i in merged:
                continue
            kept_indices.append(i)
            for j in range(i + 1, len(inconsistencies)):
                if j in merged:
                    continue
                sim = float(np.dot(embeddings[i], embeddings[j]))
                if sim >= threshold:
                    merged.add(j)

        deduped = [inconsistencies[i] for i in kept_indices]
        return deduped, len(merged)

    def _retrieve_candidates(self, new_doc, new_emb, threshold: float, top_k: int) -> list:
        """
        在已有全局向量库中检索 new_doc 的相似段落。

        Returns:
            list[tuple] — 候选对 [(para_a, para_b, similarity), ...]
        """
        import faiss

        from version_diff.device_utils import maybe_index_to_gpu

        dim = self._all_embeddings.shape[1]
        cpu_index = faiss.IndexFlatIP(dim)
        norms_existing = np.linalg.norm(self._all_embeddings, axis=1, keepdims=True)
        norms_existing[norms_existing == 0] = 1
        normalized_existing = (self._all_embeddings / norms_existing).astype(np.float32)
        cpu_index.add(normalized_existing)
        index, used_gpu = maybe_index_to_gpu(cpu_index, gpu_id=self.config.embedding.get("gpu_id", 0))
        if used_gpu:
            log.info("  FAISS 检索在 GPU 上执行")

        # 归一化新文档 embedding
        norms_new = np.linalg.norm(new_emb, axis=1, keepdims=True)
        norms_new[norms_new == 0] = 1
        normalized_new = (new_emb / norms_new).astype(np.float32)

        # 检索
        actual_k = min(top_k, len(self._all_paras))
        similarities, indices = index.search(normalized_new, actual_k)

        # 收集候选对（去重 + 阈值过滤）
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
        return candidates

    def check_conflicts(self, retrieved_passages: list[dict]) -> list[Inconsistency]:
        """
        问答冲突检测：检查 RAG 检索结果中是否存在矛盾

        对来自不同文档的检索结果两两配对，使用 LLM 判断是否存在矛盾描述。
        统一复用 ``version_diff.conflict.detect_conflicts`` 实现，结果适配为
        ``Inconsistency`` 列表（每个 doc_a 与其每个 doc_other 生成一条）。

        Args:
            retrieved_passages: RAG 检索到的段落列表
                每项: {"text": str, "source_file": str, "location": str, "score": float(可选)}

        Returns:
            矛盾列表（空列表表示无冲突）
        """
        if len(retrieved_passages) < 2:
            return []

        from version_diff.conflict import detect_conflicts

        passages = [
            {
                "text": p.get("text", ""),
                "source_file": p.get("source_file", ""),
                "location": p.get("location", ""),
                "score": p.get("score"),
            }
            for p in retrieved_passages
        ]

        conflicts = detect_conflicts(
            passages,
            llm_config=self.config.llm,
            judge_config=self.config.judge,
        )

        return [
            Inconsistency(
                point=c["point"],
                doc_a_file=c["doc_a_file"],
                doc_a_location=c["doc_a_location"],
                doc_a_says=c["doc_a_says"],
                doc_b_file=o["file"],
                doc_b_location=o["location"],
                doc_b_says=o["says"],
                similarity=0.0,
            )
            for c in conflicts
            for o in c["doc_others"]
        ]

    def filter_cross_noise(self, changes: list) -> tuple[list, list]:
        """
        跨文档（不同级别/体例）内容差异的版式噪声过滤。

        对比两份体例不同的文档（如《二级…管理手册》与《三级…工作手册》）时，
        目录 / 记录清单 / 页码占位等版式内容整体不同，会被误判为内容差异。
        本方法用内置、可配置的 `CrossNoiseFilter` 过滤这些噪声。

        超参数经 ``config.diff.cross_noise_filter`` 配置（enabled / patterns / min_length /
        dir_entry_max_length），调用方可按需覆盖。

        Args:
            changes: version_compare 返回的差异列表（``VersionDiffResult.changes``）

        Returns:
            (实质差异列表, 噪声差异列表)
        """
        from version_diff.noise import CrossNoiseFilter

        cfg = (self.config.diff or {}).get("cross_noise_filter", {})
        nf = CrossNoiseFilter(cfg)
        return nf.filter_changes(changes)

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
        from version_diff.parse_cache import cached_parse

        old_doc = cached_parse(old_filepath, config=parse_config, cache_dir=self._parse_cache_dir)
        new_doc = cached_parse(new_filepath, config=parse_config, cache_dir=self._parse_cache_dir)

        self._notify(on_progress, "embedding", 0.3, "计算语义向量...")

        model = self._get_model()
        threshold = self.config.diff.get("similarity_threshold", 0.80)

        self._notify(on_progress, "diffing", 0.5, "配对与差异计算...")

        # 语义配对
        pairs = pair_paragraphs(
            old_doc.paragraphs,
            new_doc.paragraphs,
            model,
            threshold=threshold,
            file_a=old_filepath,
            file_b=new_filepath,
            vector_store=self._vector_store,
            top_k=self.config.diff.get("top_k", 3),
        )

        log.info(
            f"  版本对比: 旧版 {len(old_doc.paragraphs)} 段, 新版 {len(new_doc.paragraphs)} 段, "
            f"配对 {len(pairs)} 对 (threshold={threshold})"
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

            changes.append(
                VersionChange(
                    change_type="modified",
                    section=new_para.chapter_title or new_para.chapter or "",
                    location=new_para.location,
                    old_section=old_para.chapter_title or old_para.chapter or "",
                    old_location=old_para.location,
                    old_text=old_para.text.strip(),
                    new_text=new_para.text.strip(),
                    summary="",  # 后续可用 LLM 生成摘要
                    similarity=sim,
                )
            )

        # 旧版有但新版没有的（removed）
        for i, para in enumerate(old_doc.paragraphs):
            if i not in paired_old:
                old_section = para.chapter_title or para.chapter or ""
                old_text = para.text.strip()
                changes.append(
                    VersionChange(
                        change_type="removed",
                        section=old_section,
                        location=para.location,
                        old_section=para.chapter_title or para.chapter or "",
                        old_location=para.location,
                        old_text=old_text,
                        new_text="",
                        summary=f"[删除] {old_text[:160]}{'...' if len(old_text) > 160 else ''}",
                    )
                )

        # 新版有但旧版没有的（added）
        for i, para in enumerate(new_doc.paragraphs):
            if i not in paired_new:
                new_text = para.text.strip()
                changes.append(
                    VersionChange(
                        change_type="added",
                        section=para.chapter_title or para.chapter or "",
                        location=para.location,
                        old_text="",
                        new_text=new_text,
                        summary=f"[新增] {new_text[:160]}{'...' if len(new_text) > 160 else ''}",
                    )
                )

        # ====== 表格对比 ======
        table_changes = self._compare_tables(old_doc.tables, new_doc.tables)
        changes.extend(table_changes)

        # ====== removed/added 二次配对：改写/移动段落合并为 modified ======
        # 语义配对阈值（0.80）对"改写/移动"段落太严：同一段落被大改（如"OKAIR 无线连接"
        # →"VPN 连接"）或移动到其他章节时相似度 < 阈值 → 未配对 → 拆成 removed+added，
        # 用户看到的是"删除 X + 新增 Y"而非"X 改写成 Y"。这里对未配对的 removed/added
        # 做文本相似度二次配对，显著相似者合并为 modified。
        changes = self._merge_rewritten_pairs(changes)

        # ====== 过滤：保留实质性变更，噪声归入 minor_changes ======
        self._notify(on_progress, "filtering", 0.9, "过滤非实质性差异...")
        changes, minor_changes = self._filter_substantive_changes(changes)

        self._notify(on_progress, "done", 1.0, "版本对比完成")

        return VersionDiffResult(
            changes=changes,
            minor_changes=minor_changes,
            old_paragraph_count=len(old_doc.paragraphs),
            new_paragraph_count=len(new_doc.paragraphs),
        )

    def _merge_rewritten_pairs(self, changes: list) -> list:
        """removed/added 二次配对：文本显著相似的删除/新增合并为修改（改写/移动）。

        用 difflib.SequenceMatcher 计算 removed.old_text 与 added.new_text 的相似度，
        超过阈值（默认 0.45）视为同一段落的改写/移动 → 合并为一条 modified。
        每段只合并一次（贪心，最高分优先）。

        Returns:
            合并后的 changes 列表
        """
        import difflib

        threshold = self.config.diff.get("rewrite_pair_threshold", 0.45)
        removed = [c for c in changes if c.change_type == "removed"]
        added = [c for c in changes if c.change_type == "added"]
        if not removed or not added:
            return changes

        # 表格行（location 形如 "行: xxx"）的删除/新增是真实变更（如某设备行被整行
        # 替换成另一设备行），不应参与"改写/移动"二次配对——否则会被错误合并成一条
        # modified，丢失"旧行删除 + 新行新增"的信息。仅对正文段落做二次配对。
        def _is_table_row(c) -> bool:
            loc = c.location or ""
            return loc.startswith("行:") or loc.startswith("表格")

        # 计算所有 removed×added 相似度，贪心合并
        candidates = []
        for ri, r in enumerate(removed):
            if _is_table_row(r):
                continue
            for ai, a in enumerate(added):
                if _is_table_row(a):
                    continue
                old_t = (r.old_text or "").strip()
                new_t = (a.new_text or "").strip()
                if not old_t or not new_t:
                    continue
                sim = difflib.SequenceMatcher(None, old_t, new_t).ratio()
                if sim >= threshold:
                    candidates.append((sim, ri, ai))
        candidates.sort(reverse=True)
        used_r, used_a = set(), set()
        merged = []
        for sim, ri, ai in candidates:
            if ri in used_r or ai in used_a:
                continue
            used_r.add(ri)
            used_a.add(ai)
            r, a = removed[ri], added[ai]
            merged.append(
                VersionChange(
                    change_type="modified",
                    section=a.section or r.section,
                    location=a.location or r.location,
                    old_section=r.section or r.old_section,
                    old_location=r.location,
                    old_text=r.old_text,
                    new_text=a.new_text,
                    summary="",
                    similarity=round(sim, 3),
                )
            )

        # 未被合并的 removed/added 保留；被合并的剔除
        keep_removed = [c for i, c in enumerate(removed) if i not in used_r]
        keep_added = [c for i, c in enumerate(added) if i not in used_a]
        others = [c for c in changes if c.change_type not in ("removed", "added")]

        # ── removed×modified 二次配对：removed 内容已被某 modified 包含 → 剔除 removed ──
        # 场景：旧版某段落（半句/整句）移动到新版另一章节并被并入改写后的段落，
        # 语义配对未成功 → 旧版内容标 removed、新位置标 modified（两条，用户看到
        # "删除 X + 新增 X" 矛盾）。若 removed.old_text 是某 modified 的 new_text 或
        # old_text 的子串（长度 >= min_len），说明该内容已体现在 modified 中 → 剔除 removed。
        if keep_removed:
            # 注意：merged 是本次 removed×added 二次配对新合并出的 modified，
            # 其 new_text 也可能包含被二次配对遗漏的 removed 内容（如旧版半句被新版
            # 并入改写段落的开头），必须一并纳入吸收检查。
            _mods = [c for c in others if c.change_type == "modified"] + merged
            _min_len = int(self.config.diff.get("removed_in_modified_min_len", 15))
            _absorbed = set()
            for i, r in enumerate(keep_removed):
                if _is_table_row(r):
                    continue
                old_t = (r.old_text or "").strip()
                if len(old_t) < _min_len:
                    continue
                for m in _mods:
                    if old_t in (m.new_text or "") or old_t in (m.old_text or ""):
                        _absorbed.add(i)
                        break
            if _absorbed:
                keep_removed = [c for i, c in enumerate(keep_removed) if i not in _absorbed]

        return others + keep_removed + keep_added + merged

    def _compare_tables(self, old_tables: list, new_tables: list) -> list[VersionChange]:
        """
        表格版本对比：配对新旧表格，逐行比较差异

        策略：
        1. 按表头内容相似度配对表格（不靠位置序号）
        2. 列对齐：按列名模糊匹配配对新旧列
        3. 行对齐：按首列关键字配对新旧行
        4. 只比较对齐列的内容，新增/删除列单独报告

        支持：列增减、行增减、单元格内容修改
        具体配对和行级比较由 ``version_diff.table_diff`` 负责；引擎仅编排流程。
        """
        return compare_tables(old_tables, new_tables)

    # ============================================================
    # 版本对比：实质性变更过滤
    # ============================================================

    _VERSION_FILTER_PROMPT = _load_version_filter_prompt()

    def _filter_substantive_changes(self, changes: list) -> tuple:
        """
        过滤版本对比结果，只保留实质性变更；噪声归入 minor_changes

        策略：
        1. added/removed 直接保留（新增/删除本身就是实质变更）
        2. modified 先做规则预过滤（去噪 + 分类）
           - 修订日期/版本戳/跟踪表行 → minor_changes (category="metadata"/"tracking_table")
           - strip 噪声后相同 → minor_changes (category="metadata")
           - 仅编号差异 → minor_changes (category="metadata")
        3. 剩余 modified 批量送 LLM 判断 → 保留或丢弃
        """
        keep = []
        minor = []
        modified = [c for c in changes if c.change_type == "modified"]

        # ---- 元数据噪声过滤配置（通用、可覆盖）----
        # added/removed 剥离配置的噪声模式后为空 → 判为纯元数据（修订日期戳/版本号/页码跟踪行等），
        # 归入 minor_changes 而非 keep。
        noise_cfg = self.config.diff.get("noise_filter", {}) if self.config.diff else {}
        noise_enabled = bool(noise_cfg.get("enabled", True))
        noise_patterns = noise_cfg.get("patterns", [])

        # 跟踪表过滤配置（added/removed 和 modified 共用）
        tracking_cfg = self.config.diff.get("tracking_table", {}) if self.config.diff else {}
        tracking_hints = tracking_cfg.get("hints")
        tracking_row_patterns = tracking_cfg.get("row_patterns")
        version_stamp_patterns = tracking_cfg.get("version_stamp_patterns")

        # added/removed：剥离配置噪声后为空 → 纯元数据噪声；否则保留为 content
        # added/removed 跟踪表行 → minor_changes（页码/修订记录等自动更新）
        for c in changes:
            if c.change_type == "modified":
                continue
            # 跟踪表行（修订记录表、有效页清单等）→ minor_changes
            if _is_tracking_table_row(c, tracking_hints=tracking_hints, tracking_row_patterns=tracking_row_patterns):
                c = _classify_change("tracking_table", c)
                c.summary = tracking_cfg.get("summary_template", "[页码跟踪] 跟踪表行自动更新（修订记录/有效页清单）")
                minor.append(c)
                continue
            if noise_enabled:
                target = c.new_text or c.old_text
                stripped = _strip_configured_noise(target, noise_patterns)
                # 配置剥离后为空 → 纯元数据噪声；内置剥离仅作补充（配置未命中时兜底）
                if target and stripped == "":
                    c = _classify_change("metadata", c)
                    c.summary = "[元数据] 修订日期/版本标记/页码跟踪行等纯元数据变更"
                    minor.append(c)
                    continue
            c = _classify_change("content", c)
            keep.append(c)

        if not modified:
            return keep, minor

        # ---- 规则预过滤：分类噪声 ----
        need_llm = []
        for c in modified:
            # 1) 跟踪表行（修订记录表、有效页清单等）
            if _is_tracking_table_row(c, tracking_hints=tracking_hints, tracking_row_patterns=tracking_row_patterns):
                c = _classify_change("tracking_table", c)
                summary = c.summary or ""
                if summary:
                    c.summary = f"[页码跟踪] {summary}"
                else:
                    default_tpl = tracking_cfg.get("summary_template", "[页码跟踪表自动更新]")
                    c.summary = default_tpl
                minor.append(c)
                continue

            # 2) 修订日期 / 版本戳噪声剥离后比较
            old_stripped = _strip_revision_noise(c.old_text, version_stamp_patterns=version_stamp_patterns)
            new_stripped = _strip_revision_noise(c.new_text, version_stamp_patterns=version_stamp_patterns)

            if old_stripped and new_stripped and old_stripped == new_stripped:
                # 剥离噪声后实质相同 → 纯元数据变更
                c = _classify_change("metadata", c)
                c.summary = "[元数据] 修订日期或版本标记更新"
                minor.append(c)
                continue

            # 3) normalize 后完全一致（空白差异）
            old_norm = re.sub(r"\s+", "", c.old_text)
            new_norm = re.sub(r"\s+", "", c.new_text)
            if old_norm == new_norm:
                c = _classify_change("metadata", c)
                c.summary = "[排版] 纯空白/换行差异"
                minor.append(c)
                continue

            # 3b) 新版是旧版的严格前缀（长度差 < 50）→ 新版疑似解析截断/跨页断句
            # 例：新版段落被截断成旧版前缀（旧 162 字 vs 新 142 字且以"更"结尾半句话）。
            # 这类是解析噪声（PDF 跨页/断句），不是真实内容删除，归入 minor。
            # ★ 仅此方向归 minor：新版短、旧版长且新版是旧版前缀 → 新版疑似截断。
            # 反向（旧版短、新版长且旧版是新版前缀）→ 新版补全/新增了内容，
            # 是真实版本变更（如 §5.1.9.7 新版补全"法规、政策和公司相关规定…"、
            # §5.1.6.5 新版新增"A 网络安全设备开启漏洞…"），必须保留为实质性。
            if (
                len(new_norm) < len(old_norm)
                and old_norm.startswith(new_norm)
                and len(old_norm) - len(new_norm) < 50
            ):
                c = _classify_change("metadata", c)
                c.summary = "[解析] 新版疑似跨页断句/文本截断（新版是旧版前缀），非实质性变更"
                minor.append(c)
                continue

            # 4) 仅编号差异（如条款编号微调）
            old_no_num = re.sub(r"[（(]\d+[)）]", "", old_norm)
            new_no_num = re.sub(r"[（(]\d+[)）]", "", new_norm)
            if old_no_num == new_no_num:
                c = _classify_change("metadata", c)
                c.summary = "[编号] 仅条款编号微调"
                minor.append(c)
                continue

            need_llm.append(c)

        if not need_llm:
            return keep, minor

        # ---- 确定性摘要：纯增/纯删方向明确，不依赖 LLM 猜方向 ----
        # LLM 对"旧版删除了 X"的 modified（旧长新短）偶发把方向说反
        # （如 §5.1.4.2 删除了 OKAIR 无线密码获取方式，LLM 摘要成"新增紧急
        # 联系方式"）。若 diff 只有 delete 或只有 insert（一方是另一方的子串
        # 或同序子序列），用确定性规则直接生成摘要并保留，跳过 LLM。
        import difflib as _difflib

        _deterministic = []
        for c in need_llm:
            _old_n = re.sub(r"\s+", "", c.old_text or "")
            _new_n = re.sub(r"\s+", "", c.new_text or "")
            if not _old_n or not _new_n:
                continue
            _sm = _difflib.SequenceMatcher(None, _old_n, _new_n, autojunk=False)
            _ops = _sm.get_opcodes()
            # 只有 equal+delete（内容被删）或 equal+insert（内容被加）
            _dirs = {o[0] for o in _ops} - {"equal"}
            if _dirs in ({"delete"}, {"insert"}):
                _diff_txt = "".join(
                    (_old_n[i1:i2] if tag == "delete" else _new_n[j1:j2])
                    for tag, i1, i2, j1, j2 in _ops
                    if tag in ("delete", "insert")
                ).strip()
                # 残词/段界碎片（过短、不含句末标点）→ 不抢判，交 LLM
                # （如"需求。"、"略，阻止…需求。"是解析段界差异而非真实增删）
                # 注意：完整删除可能以编号/括号结尾（如 OKAIR 删除以"5.1.4.3"结尾），
                # 只要文本长度足够且含句末标点即可确定性判定。
                _SENT_END = set("。；！？.;!？")
                if len(_diff_txt) < 8 or not any(ch in _diff_txt for ch in _SENT_END):
                    _deterministic.append(c)
                    continue
                if _dirs == {"delete"}:
                    c.summary = f"删除内容: {_diff_txt[:80]}"
                else:
                    c.summary = f"新增内容: {_diff_txt[:80]}"
                c = _classify_change("content", c)
                keep.append(c)
                continue
            _deterministic.append(c)
        need_llm = _deterministic
        if not need_llm:
            return keep, minor

        # ---- 批量 LLM 判断 ----
        batch_size = self.config.diff.get("batch_size", 5) or 10
        num_batches = math.ceil(len(need_llm) / batch_size)
        modified_total = len(modified)
        noise_count = modified_total - len(need_llm)
        log.info(
            f"  版本diff过滤: {modified_total} modified → "
            f"规则过滤 {noise_count} (含跟踪表/日期/编号) → "
            f"LLM判断 {len(need_llm)} ({num_batches} batches)"
        )

        llm_config = self.config.llm

        for batch_idx in range(num_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, len(need_llm))
            batch = need_llm[start:end]

            items_text = ""
            for i, c in enumerate(batch, 1):
                items_text += f"--- 第 {i} 处 ---\n旧: {c.old_text[:200]}\n新: {c.new_text[:200]}\n\n"

            prompt = self._VERSION_FILTER_PROMPT.format(count=len(batch), items=items_text)

            results = call_llm_json(prompt, llm_config)
            if results:
                for r in results:
                    if not isinstance(r, dict):
                        continue
                    idx = int(r.get("index", 0)) - 1
                    if 0 <= idx < len(batch):
                        c = batch[idx]
                        if r.get("keep", False):
                            c.summary = r.get("summary", "")
                            c = _classify_change("content", c)
                            keep.append(c)
                        else:
                            c.summary = r.get("summary", "") or "非实质变更"
                            c = _classify_change("metadata", c)
                            minor.append(c)
            else:
                # LLM 失败全部保留（保守策略）
                for c in batch:
                    keep.append(_classify_change("content", c))

        log.info(f"  版本diff过滤完成: {len(changes)} → {len(keep)} 实质性 + {len(minor)} 细微变更")
        for i, c in enumerate(keep, 1):
            log.info(f"    [{i}] {c.change_type} @ {c.location} | {c.summary[:80]}")
        return keep, minor
