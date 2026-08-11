"""
正文段落差异比较模块

流程：
1. VectorStore 获取/缓存 embedding + FAISS index
2. FAISS 高效检索最相似段落 → 贪心配对
3. 对配对段落做 word/char-level diff
4. 输出差异列表
"""

import difflib
import re
from dataclasses import dataclass, field

from loguru import logger as log

from version_diff.vectorstore import VectorStore


@dataclass
class TextDiffItem:
    """一处正文差异"""

    para_a: object  # Paragraph
    para_b: object  # Paragraph
    similarity: float
    diff_fragments: list = field(default_factory=list)  # [(type, old, new), ...]
    description: str = ""
    llm_reason: str = ""  # LLM 给出的变更描述
    category: str = ""  # 差异类别（substantive/scope/inconsistency/...）
    category_label: str = ""  # 类别中文标签
    # 结构化字段（由 LLM 判断结果直接填充，避免后续字符串解析）
    llm_point: str = ""  # 矛盾事项（如"备份频率"）
    llm_doc_a_says: str = ""  # A 文档的说法
    llm_doc_b_says: str = ""  # B 文档的说法

    @property
    def has_changes(self):
        return bool(self.diff_fragments)


# 全局 VectorStore 实例（默认使用包内缓存目录）
_default_vector_store = VectorStore()


# ============================================================
# Embedding 段落配对（使用 FAISS）
# ============================================================


def pair_paragraphs(
    paras_a, paras_b, model, threshold=0.80, file_a="", file_b="", vector_store=None
):
    """
    用 FAISS 找到两篇文档中"说同一件事"的段落对

    流程：
    1. 获取/缓存文档 A 和 B 的 embedding + FAISS index
    2. 用 A 的 embedding 在 B 的 index 中检索 top-K
    3. 贪心配对（每段只配一个，优先最高分）
    """
    if not paras_a or not paras_b:
        return []

    vs = vector_store or _default_vector_store

    # 获取 embedding（命中缓存则跳过计算）
    log.info("  📄 文档A:")
    emb_a, _ = vs.get_or_compute(file_a, paras_a, model)

    log.info("  📄 文档B:")
    _emb_b, index_b = vs.get_or_compute(file_b, paras_b, model)

    # 用 FAISS 检索：对 A 中每段，在 B 中找 top-3 最相似
    top_k = min(3, len(paras_b))
    log.info(f"  🔍 FAISS 检索 (A的{len(paras_a)}段 → B的index, top-{top_k})...")
    similarities, indices = vs.search_similar(emb_a, index_b, top_k)

    # 贪心配对
    candidates = []
    for i in range(len(paras_a)):
        for k in range(top_k):
            j = int(indices[i][k])
            sim = float(similarities[i][k])
            if sim >= threshold:
                candidates.append((sim, i, j))

    candidates.sort(reverse=True)
    pairs = []
    used_a = set()
    used_b = set()

    for sim, i, j in candidates:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((i, j, sim))

    return pairs


# ============================================================
# 段落级差异计算
# ============================================================


def compute_diff(para_a, para_b, similarity):
    """对配对的段落计算细粒度差异"""
    text_a = para_a.text
    text_b = para_b.text

    is_cjk = sum(1 for c in text_a if "\u4e00" <= c <= "\u9fff") > len(text_a) * 0.3

    if is_cjk:
        tokens_a = list(text_a)
        tokens_b = list(text_b)
        sep = ""
    else:
        tokens_a = text_a.split()
        tokens_b = text_b.split()
        sep = " "

    matcher = difflib.SequenceMatcher(None, tokens_a, tokens_b)
    fragments = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        elif tag == "replace":
            fragments.append(
                ("replace", sep.join(tokens_a[i1:i2]), sep.join(tokens_b[j1:j2]))
            )
        elif tag == "delete":
            fragments.append(("delete", sep.join(tokens_a[i1:i2]), ""))
        elif tag == "insert":
            fragments.append(("insert", "", sep.join(tokens_b[j1:j2])))

    return TextDiffItem(
        para_a=para_a,
        para_b=para_b,
        similarity=similarity,
        diff_fragments=fragments,
        description=_describe_change(fragments, text_a, text_b),
    )


def _describe_change(fragments, text_a, text_b):
    """简要描述变更类型"""
    if not fragments:
        return "无实质差异"

    nums_a = set(re.findall(r"\d+\.?\d*", text_a))
    nums_b = set(re.findall(r"\d+\.?\d*", text_b))
    if nums_a != nums_b:
        new_nums = nums_b - nums_a
        if new_nums:
            return f"数值变更 ({', '.join(sorted(new_nums)[:3])})"

    total_changed = sum(len(f[1]) + len(f[2]) for f in fragments)
    total_text = max(len(text_a), len(text_b))
    ratio = total_changed / max(total_text, 1)

    if ratio < 0.1:
        return "细微措辞差异"
    elif ratio < 0.3:
        return "部分内容修改"
    else:
        return "大幅内容变更"


# ============================================================
# 主入口
# ============================================================


# ============================================================
# 差异文本比较（diff_texts 已废弃，统一使用 engine/DiffEngine 入口）
# ============================================================
