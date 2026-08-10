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
import os
import re
import time
from collections.abc import Callable
from difflib import SequenceMatcher

# ======================================================================
# 通用「版本管理噪声」模式：文档版本迭代中常见的非内容变化
# 适用于：修订日期戳、版本号标记、受控状态、页码跟踪表行等
# ======================================================================

# 修订日期戳："修订日期：2026-05-08" / "修订日期:2026年5月8日" 等
_REVISION_DATE_PATTERNS = [
    re.compile(r"修订日期[：:]\s*\d{4}[-年./]\s*\d{1,2}[-月./]\s*\d{1,2}[日]?"),
    re.compile(r"修订日期[：:]\s*\d{4}[-年./]\s*\d{1,2}[-月./]\s*\d{1,2}"),
]

# 版本号文件号："R5-22"、"BK-J-62"、"版次：xxx"
_VERSION_STAMP_RE = re.compile(r"(?:R\d+-\d{2,}|BK-J-\d+|版次[：:]\s*\S+)")

# 纯日期（无上下文）：行首或行尾的 YYYY-MM-DD
_STANDALONE_DATE_RE = re.compile(r"(?:^\s*|\b)\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}\s*(?:$|\b)")

# 页码跟踪表行特征："22 R 2026-05-08" 或 "R5-21 | 2026.04.14 | 2026.03.31 | 生效"
_TABLE_ROW_RE = re.compile(
    r"^(?:\d{1,4}\s+)?(?:R\d{2,3}|N|A|D)\s+\d{4}[-./]\d{1,2}[-./]\d{1,2}"  # "22 R 2026-05-08"
    r"|(?:R\d+-\d+\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2}(?:\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2})*\s*\|\s*(?:生效|无效|页))"
    r"|(?:\d{1,4}\s*\|\s*\d{1,3}\s*\|\s*[NRAD]\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2})",  # "0.4-1 | 21 | R | 2026-03-31"
    re.MULTILINE,
)

# 页码跟踪表名称（location 包含这些即归类为跟踪表）
_TRACKING_TABLE_HINTS = re.compile(r"有效页清单|修订记录表|发放清单|修改记录")


def _strip_revision_noise(text: str) -> str:
    """移除文档版本管理常见噪音，用于「实质内容相同」的判定

    通用模式：修订日期戳、版本号文件号、独立日期、跟踪表行标记
    """
    if not text:
        return ""
    result = text
    # 移除修订日期戳
    for pat in _REVISION_DATE_PATTERNS:
        result = pat.sub("", result)
    # 移除独立日期
    result = _STANDALONE_DATE_RE.sub("", result)
    # 移除版本号文件号
    result = _VERSION_STAMP_RE.sub("", result)
    # 压缩空白
    result = re.sub(r"\s+", " ", result).strip()
    return result


def _strip_configured_noise(text: str, patterns) -> str:
    """按配置的元数据噪声正则剥离文本（通用、可配置）

    用于判定 added/removed 段落是否「纯元数据」（如仅含修订日期戳/版本号/页码跟踪行）
    """
    if not text:
        return ""
    result = text
    for pat in patterns:
        try:
            if isinstance(pat, str):
                pat = re.compile(pat)
            result = pat.sub("", result)
        except Exception:
            continue
    return re.sub(r"\s+", " ", result).strip()


def _is_tracking_table_row(change) -> bool:
    """判断是否为页码跟踪表/修订记录表中的一行（版本号+日期行，非实质内容）

    兼容 VersionChange 实例和 plain dict。
    """
    if isinstance(change, dict):
        loc = change.get("location", "") or ""
        new_t = change.get("new_text", "") or ""
        old_t = change.get("old_text", "") or ""
    else:
        loc = getattr(change, "location", "") or ""
        new_t = getattr(change, "new_text", "") or ""
        old_t = getattr(change, "old_text", "") or ""
    # location 直接命中常见表名
    if loc and _TRACKING_TABLE_HINTS.search(loc):
        return True
    # 两侧文本本身符合跟踪表行格式
    text_to_check = new_t or old_t
    if text_to_check and _TABLE_ROW_RE.search(text_to_check):
        return True
    return False


def _classify_change(category: str, change):
    """给 VersionChange 打 category 标签（兼容 dataclass 实例和 plain dict）"""
    if isinstance(change, dict):
        change["category"] = category
    else:
        change.category = category
    return change

import numpy as np
from sentence_transformers import SentenceTransformer

from version_diff.config import Config
from version_diff.judge import filter_diffs
from version_diff.llm_util import call_llm_json
from version_diff.matcher import compute_diff
from version_diff.models import DiffResult, Inconsistency, VersionChange, VersionDiffResult
from version_diff.vectorstore import VectorStore

log = logging.getLogger("version_diff.engine")


# 版本过滤 prompt（随包发布，外部文件优先）
_VERSION_FILTER_PROMPT_FILE = os.path.join(
    os.path.dirname(__file__), "prompts", "version_filter.txt"
)


def _load_version_filter_prompt() -> str:
    """从随包 .txt 文件加载版本过滤 prompt 模板（缺失则用内置兜底）"""
    fallback = (
        "你是文档版本对比专家。请判断以下 {count} 处新旧版本文本差异是否为实质性变更。"
        "回复 JSON 数组：{{\"index\": N, \"keep\": true/false, \"summary\": \"...\"}}"
    )
    try:
        with open(_VERSION_FILTER_PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        log.warning(f"加载版本过滤 prompt 失败，使用兜底: {e}")
        return fallback


# ============================================================
# 表格对比辅助函数（模块级，供 DiffEngine._compare_tables 调用）
# ============================================================


def _normalize_cell(text: str) -> str:
    """标准化单元格文本：去换行、压缩空格"""
    return re.sub(r"\s+", "", str(text).strip())


def _normalize_display_cell(text: str) -> str:
    """显示用标准化：去换行、压缩连续空格为单个"""
    return re.sub(r"\s+", " ", str(text).strip())


def _table_header_text(table) -> str:
    """提取表格表头文本用于配对（标准化后）"""
    if table.rows:
        return " ".join(_normalize_cell(cell) for cell in table.rows[0])
    return ""


def _row_key(row, col_idx: int) -> str:
    """行对齐关键字（按指定对齐列）"""
    return _normalize_cell(row[col_idx]) if len(row) > col_idx else ""


def _aligned_row_text(row, col_map: dict, is_old: bool) -> str:
    """只提取对齐列的内容（忽略新增/删除列的噪音）"""
    ordered = sorted(col_map.keys())
    if is_old:
        return " | ".join(
            _normalize_cell(row[oi]) for oi in ordered if oi < len(row)
        )
    return " | ".join(
        _normalize_cell(row[col_map[oi]]) for oi in ordered if col_map[oi] < len(row)
    )


def _compare_table_pair(old_t, new_t) -> list[VersionChange]:
    """
    对一对已配对表格做列对齐 + 行对齐 + 单元格 diff。

    返回 VersionChange 列表（可能为空）。行为与旧实现完全一致。
    """
    changes: list[VersionChange] = []
    section = old_t.chapter_title or old_t.location

    if not old_t.rows or not new_t.rows:
        return changes

    old_header = [str(c).strip() for c in old_t.rows[0]]
    new_header = [str(c).strip() for c in new_t.rows[0]]

    # 列对齐：按列名模糊匹配
    col_map: dict = {}
    used_new_cols = set()

    # 检测新表头是否被截断（PDF 跨页导致表头丢失）
    new_header_valid = (
        sum(1 for h in new_header if _normalize_cell(h)) >= len(new_header) * 0.5
    )

    if new_header_valid and len(old_header) > 0:
        for oi, oh in enumerate(old_header):
            best_ci = -1
            best_cs = 0.0
            for ni, nh in enumerate(new_header):
                if ni in used_new_cols:
                    continue
                cs = SequenceMatcher(
                    None, _normalize_cell(oh), _normalize_cell(nh)
                ).ratio()
                if cs > best_cs and cs >= 0.5:
                    best_cs = cs
                    best_ci = ni
            if best_ci >= 0:
                col_map[oi] = best_ci
                used_new_cols.add(best_ci)
    else:
        # 新表头被截断：假设列结构相同，1:1 映射
        for ci in range(min(len(old_header), len(new_header))):
            col_map[ci] = ci
            used_new_cols.add(ci)

    # 报告新增/删除的列
    added_cols = [
        new_header[ni] for ni in range(len(new_header)) if ni not in used_new_cols
    ]
    removed_cols = [
        old_header[oi] for oi in range(len(old_header)) if oi not in col_map
    ]
    if added_cols:
        changes.append(
            VersionChange(
                change_type="added",
                section=f"表格: {section}",
                location="表格结构",
                old_text="",
                new_text=f"新增列: {', '.join(added_cols)}",
                summary=f"表格新增 {len(added_cols)} 列",
            )
        )
    if removed_cols:
        changes.append(
            VersionChange(
                change_type="removed",
                section=f"表格: {section}",
                location="表格结构",
                old_text=f"删除列: {', '.join(removed_cols)}",
                new_text="",
                summary=f"表格删除 {len(removed_cols)} 列",
            )
        )

    # 行对齐：按首列（对齐后）关键字配对
    first_old_col = 0
    first_new_col = col_map.get(0, 0)

    old_rows = {_row_key(r, first_old_col): r for r in old_t.rows[1:]}
    new_rows = {_row_key(r, first_new_col): r for r in new_t.rows[1:]}
    all_keys = list(dict.fromkeys(list(old_rows.keys()) + list(new_rows.keys())))

    for key in all_keys:
        if not key:
            continue
        old_r = old_rows.get(key)
        new_r = new_rows.get(key)

        if old_r and new_r:
            old_txt = _aligned_row_text(old_r, col_map, is_old=True)
            new_txt = _aligned_row_text(new_r, col_map, is_old=False)
            if old_txt != new_txt:
                changes.append(
                    VersionChange(
                        change_type="modified",
                        section=f"表格: {section}",
                        location=f"行: {key}",
                        old_text=" | ".join(_normalize_display_cell(c) for c in old_r),
                        new_text=" | ".join(_normalize_display_cell(c) for c in new_r),
                        summary="",
                        similarity=SequenceMatcher(None, old_txt, new_txt).ratio(),
                    )
                )
        elif old_r and not new_r:
            changes.append(
                VersionChange(
                    change_type="removed",
                    section=f"表格: {section}",
                    location=f"行: {key}",
                    old_text=" | ".join(_normalize_display_cell(c) for c in old_r),
                    new_text="",
                    summary="",
                )
            )
        elif new_r and not old_r:
            changes.append(
                VersionChange(
                    change_type="added",
                    section=f"表格: {section}",
                    location=f"行: {key}",
                    old_text="",
                    new_text=" | ".join(_normalize_display_cell(c) for c in new_r),
                    summary="",
                )
            )

    return changes


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
        self, filepath: str, on_progress: Callable | None = None, doc_filename: str = "",
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
        import faiss

        from doc_parser import parse

        threshold = self.config.diff.get("similarity_threshold", 0.80)
        top_k = self.config.diff.get("top_k", 3)

        # Step 1: 解析新文档（始终执行）
        self._notify(on_progress, "parsing", 0.0, "解析文档...")
        t0 = time.time()
        new_doc = parse(filepath, config=self.config.embedding.get("parse_config"))
        # ★ 用可读文件名覆盖 SHA256 哈希名，确保矛盾列表前端显示友好
        if doc_filename:
            new_doc.filename = doc_filename
            for p in new_doc.paragraphs:
                p.source_file = doc_filename
            for t in new_doc.tables:
                t.source_file = doc_filename
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
        candidates = self._retrieve_candidates(new_doc, new_emb, threshold, top_k)
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
            diff_items, llm_config=self.config.llm, judge_config=self.config.judge,
            on_batch=_on_batch_callback,
        )
        log.info(
            f"判断完成: {len(judge_result.inconsistent_items)} 处矛盾（跨文档） ({time.time() - t4:.1f}s)"
        )

        # Step 6: 封装结果
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

    def _retrieve_candidates(
        self, new_doc, new_emb, threshold: float, top_k: int
    ) -> list:
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
                old_section=old_para.chapter_title or old_para.chapter or "",
                old_location=old_para.location,
                old_text=old_para.text.strip(),
                new_text=new_para.text.strip(),
                summary="",  # 后续可用 LLM 生成摘要
                similarity=sim,
            ))

        # 旧版有但新版没有的（removed）
        for i, para in enumerate(old_doc.paragraphs):
            if i not in paired_old:
                new_ref = new_doc.paragraphs[0] if new_doc.paragraphs else None
                changes.append(VersionChange(
                    change_type="removed",
                    section=(new_ref.chapter_title or new_ref.chapter or "") if new_ref else "",
                    location=para.location,
                    old_section=para.chapter_title or para.chapter or "",
                    old_location=para.location,
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

    def _compare_tables(self, old_tables: list, new_tables: list) -> list[VersionChange]:
        """
        表格版本对比：配对新旧表格，逐行比较差异

        策略：
        1. 按表头内容相似度配对表格（不靠位置序号）
        2. 列对齐：按列名模糊匹配配对新旧列
        3. 行对齐：按首列关键字配对新旧行
        4. 只比较对齐列的内容，新增/删除列单独报告

        支持：列增减、行增减、单元格内容修改
        列对齐 / 行对齐 / 单元格 diff 的实现见模块级 _compare_table_pair。
        """
        changes = []

        # 1. 配对表格（按表头相似度）
        paired_old = set()
        paired_new = set()
        table_pairs = []

        for i, old_t in enumerate(old_tables):
            old_header = _table_header_text(old_t)
            best_match = -1
            best_score = 0.0
            for j, new_t in enumerate(new_tables):
                if j in paired_new:
                    continue
                new_header = _table_header_text(new_t)
                score = SequenceMatcher(None, old_header, new_header).ratio()
                if score > best_score and score >= 0.5:
                    best_score = score
                    best_match = j
            if best_match >= 0:
                table_pairs.append((i, best_match))
                paired_old.add(i)
                paired_new.add(best_match)

        # Fallback：未配对的大表格按列数+行数规模匹配（处理 PDF 跨页表头丢失的情况）
        for i, old_t in enumerate(old_tables):
            if i in paired_old:
                continue
            if not old_t.rows or len(old_t.rows) < 5:
                continue  # 只对大表格做 fallback
            old_cols = len(old_t.rows[0])
            for j, new_t in enumerate(new_tables):
                if j in paired_new:
                    continue
                if not new_t.rows or len(new_t.rows) < 5:
                    continue
                new_cols = len(new_t.rows[0])
                # 列数相同 + 行数差异在 30% 以内
                if old_cols == new_cols:
                    size_ratio = min(len(old_t.rows), len(new_t.rows)) / max(
                        len(old_t.rows), len(new_t.rows)
                    )
                    if size_ratio >= 0.7:
                        table_pairs.append((i, j))
                        paired_old.add(i)
                        paired_new.add(j)
                        break

        # 2. 对配对的表格做行级 diff
        for old_idx, new_idx in table_pairs:
            changes.extend(_compare_table_pair(old_tables[old_idx], new_tables[new_idx]))

        return changes

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
        import math
        import re

        keep = []
        minor = []
        modified = [c for c in changes if c.change_type == "modified"]

        # ---- 元数据噪声过滤配置（通用、可覆盖）----
        # added/removed 剥离配置的噪声模式后为空 → 判为纯元数据（修订日期戳/版本号/页码跟踪行等），
        # 归入 minor_changes 而非 keep。
        noise_cfg = self.config.diff.get("noise_filter", {}) if self.config.diff else {}
        noise_enabled = bool(noise_cfg.get("enabled", True))
        noise_patterns = noise_cfg.get("patterns", [])

        # added/removed：剥离配置噪声后为空 → 纯元数据噪声；否则保留为 content
        for c in changes:
            if c.change_type == "modified":
                continue
            if noise_enabled:
                target = c.new_text if c.new_text else c.old_text
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
            if _is_tracking_table_row(c):
                c = _classify_change("tracking_table", c)
                summary = c.summary or ""
                c.summary = f"[页码跟踪] {summary}" if summary else "[页码跟踪表自动更新]"
                minor.append(c)
                continue

            # 2) 修订日期 / 版本戳噪声剥离后比较
            old_stripped = _strip_revision_noise(c.old_text)
            new_stripped = _strip_revision_noise(c.new_text)

            if old_stripped and new_stripped and old_stripped == new_stripped:
                # 剥离噪声后实质相同 → 纯元数据变更
                c = _classify_change("metadata", c)
                c.summary = "[元数据] 修订日期或版本标记更新"
                minor.append(c)
                continue

            # 3) normalize 后完全一致（空白差异）
            old_norm = re.sub(r'\s+', '', c.old_text)
            new_norm = re.sub(r'\s+', '', c.new_text)
            if old_norm == new_norm:
                c = _classify_change("metadata", c)
                c.summary = "[排版] 纯空白/换行差异"
                minor.append(c)
                continue

            # 4) 仅编号差异（如条款编号微调）
            old_no_num = re.sub(r'[（(]\d+[)）]', '', old_norm)
            new_no_num = re.sub(r'[（(]\d+[)）]', '', new_norm)
            if old_no_num == new_no_num:
                c = _classify_change("metadata", c)
                c.summary = "[编号] 仅条款编号微调"
                minor.append(c)
                continue

            need_llm.append(c)

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
                # LLM 失败全部保留
                for c in batch:
                    c = _classify_change("content", c)
                keep.extend(batch)

        log.info(
            f"  版本diff过滤完成: {len(changes)} → "
            f"{len(keep)} 实质性 + {len(minor)} 细微变更"
        )
        return keep, minor
