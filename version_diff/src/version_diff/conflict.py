"""RAG 问答冲突检测（统一实现）

收敛原先分布在两处的冲突检测逻辑：

- ``engine.check_conflicts``（version_diff 公共 API，生产未被调用）
- ``qa_engine._detect_conflicts``（rag_demo 生产实际调用，含启发式回退）

提供单一公共入口 ``detect_conflicts``，被两处复用，避免算法各写一份、

以及 qa_engine 自行实现 Jaccard 2-gram 相似度"绕开" ``matcher`` 的坏味道。

流程：
1. 跨文档 + 非相同文本预过滤；带 ``score`` 的检索段落叠加
   score 预过滤 + Jaccard 2-gram 相似度门控；
2. LLM 精准确认（复用 ``judge_pairs`` 公共接口）；
3. LLM 不可用时回退到启发式聚合（按 doc_a 分组）。
"""

import hashlib

from version_diff.judge import judge_pairs


def _text_similarity(text_a: str, text_b: str) -> float:
    """简单文本相似度（Jaccard on char 2-grams）"""
    if not text_a or not text_b:
        return 0.0
    grams_a = {text_a[i : i + 2] for i in range(len(text_a) - 1)}
    grams_b = {text_b[i : i + 2] for i in range(len(text_b) - 1)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / len(grams_a | grams_b)


def detect_conflicts(
    passages: list[dict],
    llm_config: dict | None = None,
    judge_config: dict | None = None,
    cd_config: dict | None = None,
) -> list[dict]:
    """统一的 RAG 问答冲突检测。

    Args:
        passages: 检索段落列表，每项至少含
            ``{"text": str, "source_file": str, "location": str, "score": float(可选)}``
        llm_config: 透传给 ``judge_pairs`` 的 LLM 配置（dict）。
        judge_config: 透传给 ``judge_pairs`` 的 prompt 覆盖配置（dict）。
        cd_config: 冲突检测参数，默认
            ``{"min_score": 0.7, "min_similarity": 0.5, "max_similarity": 0.95}``

    Returns:
        list[dict]，每项::

            {
              "point": str,
              "doc_a_file": str,
              "doc_a_location": str,
              "doc_a_says": str,
              "doc_others": [{"file": str, "location": str, "says": str}, ...],
            }

        空列表表示无冲突。
    """
    if len(passages) < 2:
        return []

    cd = cd_config or {}
    min_score = cd.get("min_score", 0.7)
    min_sim = cd.get("min_similarity", 0.5)
    max_sim = cd.get("max_similarity", 0.95)

    # 1. 跨文档 + 非相同文本预过滤；带 score 时叠加 score/相似度门控
    raw_pairs: list[dict] = []
    for i in range(len(passages)):
        for j in range(len(passages)):
            if i >= j:
                continue
            a, b = passages[i], passages[j]
            if a.get("source_file") == b.get("source_file"):
                continue
            text_a, text_b = a.get("text", ""), b.get("text", "")
            if text_a.strip() == text_b.strip():
                continue
            score_a, score_b = a.get("score"), b.get("score")
            if score_a is not None and score_b is not None:
                if not (score_a > min_score and score_b > min_score):
                    continue
                sim = _text_similarity(text_a, text_b)
                if not (min_sim < sim < max_sim):
                    continue
            raw_pairs.append({"a": a, "b": b})

    if not raw_pairs:
        return []

    # 2. LLM 精准确认
    confirmed = _llm_confirm(raw_pairs, llm_config, judge_config)
    if confirmed is not None:
        return confirmed

    # 3. LLM 不可用时回退启发式聚合
    return _heuristic_group(raw_pairs)


def _llm_confirm(raw_pairs, llm_config, judge_config):
    """用 LLM 确认候选对是否真正矛盾；返回 list[dict] 或 None（应回退）"""
    if not llm_config:
        return None
    if not llm_config.get("model") and not llm_config.get("provider"):
        return None

    pairs = [
        {
            "a": {
                "text": p["a"].get("text", ""),
                "source_file": p["a"].get("source_file", ""),
                "location": p["a"].get("location", ""),
            },
            "b": {
                "text": p["b"].get("text", ""),
                "source_file": p["b"].get("source_file", ""),
                "location": p["b"].get("location", ""),
            },
        }
        for p in raw_pairs
    ]

    results = judge_pairs(pairs, llm_config, judge_config)
    if results is None:
        return None

    confirmed: list[dict] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        try:
            idx = int(r.get("index", 0)) - 1
        except (ValueError, TypeError):
            continue
        if 0 <= idx < len(raw_pairs) and r.get("inconsistent", False):
            pair = raw_pairs[idx]
            confirmed.append(
                {
                    "point": r.get("point", "可能存在描述差异"),
                    "doc_a_file": pair["a"].get("source_file", ""),
                    "doc_a_location": pair["a"].get("location", ""),
                    "doc_a_says": r.get("doc_a_says", pair["a"].get("text", "")),
                    "doc_others": [
                        {
                            "file": pair["b"].get("source_file", ""),
                            "location": pair["b"].get("location", ""),
                            "says": r.get("doc_b_says", pair["b"].get("text", "")),
                        }
                    ],
                }
            )
    return confirmed


def _heuristic_group(raw_pairs):
    """启发式冲突聚合（LLM 不可用时的回退逻辑），按 doc_a 分组"""
    groups: dict[str, dict] = {}
    for pair in raw_pairs:
        a = pair["a"]
        sig = (
            hashlib.sha256((a.get("source_file", "") + "|" + a.get("text", "")[:50].strip()).encode())
            .hexdigest()[-12:]
            .upper()
        )
        if sig not in groups:
            groups[sig] = {"point": "可能存在描述差异", "a": a, "others": []}
        groups[sig]["others"].append(pair["b"])

    conflicts: list[dict] = []
    for g in groups.values():
        conflicts.append(
            {
                "point": g["point"],
                "doc_a_file": g["a"].get("source_file", ""),
                "doc_a_location": g["a"].get("location", ""),
                "doc_a_says": g["a"].get("text", ""),
                "doc_others": [
                    {
                        "file": b.get("source_file", ""),
                        "location": b.get("location", ""),
                        "says": b.get("text", ""),
                    }
                    for b in g["others"]
                ],
            }
        )
    return conflicts
