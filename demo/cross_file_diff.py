#!/usr/bin/env python
"""
跨文件（不同文档 / 不同级别）内容差异示例（demo）

与 version_compare（版本对比，适用于同文档不同版本）不同，本示例针对**结构不同**的
两份文档，用「相似度聚类」找**描述同一主题但表述不一致**的段落对——这才是跨文件
差异的正确语义（例如同一制度在二级《管理手册》与三级《工作手册》中的不同表述、
生效日期/编号/适用范围/签发主体等不一致）。

特点：
- 文档路径作为命令行参数传入
- 基于 Jaccard 2-gram 文本相似度（无需 embedding 模型，离线可用，轻量）
- 只找「相似但不完全相同」的段落对（相似度落在中间区间），排除完全重复与无关
- 输出按相似度排序的真实差异（含两侧原文），保存报告 + 控制台摘要

用法:
    uv run --project doc_parser python demo/cross_file_diff.py <文档A路径> <文档B路径> [--out 报告] [--min-sim 0.4] [--max-sim 0.95] [--top 20]

示例:
    uv run --project doc_parser python demo/cross_file_diff.py \
        "data/pdf/(二级)(司批)信息技术管理手册/R3-3/(二级)(司批)信息技术管理手册.pdf" \
        "data/pdf/(三级)(司批)信息技术部工作手册/R6-7/(三级)(司批)信息技术部工作手册.pdf" \
        --out demo/reports/cross_file_diff.md

说明:
    - 相似度阈值可调：min_sim 过低会混入无关，过高会漏掉真正不一致；默认 [0.4, 0.95]。
    - 与 version_compare 互补：同文档不同版本用 version_compare；不同文档用本方法。
"""
import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "doc_parser"))
sys.path.insert(0, str(PROJECT_ROOT / "version_diff"))


def load_dotenv():
    dotenv = PROJECT_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def grams(text: str) -> set:
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) > 1 else set(text)


def jaccard(ga: set, gb: set) -> float:
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def load_paragraphs(path: str) -> tuple[list[str], list[str]]:
    """解析 PDF，返回 (段落文本列表, 位置列表)。"""
    from doc_parser import parse

    doc = parse(path)
    texts, locs = [], []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if len(t) >= 6:  # 过滤过短无意义
            texts.append(t)
            locs.append(f"第{p.page}页")
    return texts, locs


def main():
    parser = argparse.ArgumentParser(description="跨文件内容差异（相似度聚类）")
    parser.add_argument("doc_a", help="文档A 路径")
    parser.add_argument("doc_b", help="文档B 路径")
    parser.add_argument("--out", default="", help="报告输出路径")
    parser.add_argument("--min-sim", type=float, default=0.4, help="相似度下限（默认0.4）")
    parser.add_argument("--max-sim", type=float, default=0.95, help="相似度上限（默认0.95）")
    parser.add_argument("--top", type=int, default=20, help="输出前 N 条")
    args = parser.parse_args()

    for p in (args.doc_a, args.doc_b):
        if not os.path.exists(p):
            print(f"[错误] 文件不存在: {p}")
            sys.exit(1)

    print("解析文档 ...")
    t0 = time.time()
    A_texts, A_locs = load_paragraphs(args.doc_a)
    B_texts, B_locs = load_paragraphs(args.doc_b)
    print(f"A({os.path.basename(args.doc_a)})={len(A_texts)} 段, B({os.path.basename(args.doc_b)})={len(B_texts)} 段")

    # 预计算 2-gram 集合（避免重复计算）
    A_g = [grams(t) for t in A_texts]
    B_g = [grams(t) for t in B_texts]

    # 对文档A每个段落，在文档B中找相似度落在 [min_sim, max_sim] 的最佳匹配
    # （内容相关但不完全相同 → 同一主题的不同表述）
    found = []
    for i, ga in enumerate(A_g):
        best_j, best_s = -1, 0.0
        for j, gb in enumerate(B_g):
            s = jaccard(ga, gb)
            if s > best_s:
                best_s, best_j = s, j
        if args.min_sim <= best_s <= args.max_sim:
            found.append((best_s, A_texts[i], A_locs[i], B_texts[best_j], B_locs[best_j]))

    found.sort(key=lambda x: -x[0])
    elapsed = time.time() - t0

    lines = []
    lines.append("# 跨文件内容差异（相似度聚类）")
    lines.append("")
    lines.append(f"- 文档A: `{args.doc_a}`")
    lines.append(f"- 文档B: `{args.doc_b}`")
    lines.append(f"- 相似度区间: [{args.min_sim}, {args.max_sim}]，耗时 {elapsed:.1f}s")
    lines.append(f"- **找到「同一主题但表述不一致」的差异: {len(found)} 处**")
    lines.append("")
    for i, (s, ta, la, tb, lb) in enumerate(found[: args.top], 1):
        lines.append(f"### [{i}] 相似度 {s:.2f}（{la} vs {lb}）")
        lines.append(f"- A: {ta}")
        lines.append(f"- B: {tb}")
        lines.append("")
    text = "\n".join(lines)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"报告已保存: {args.out}")
    else:
        print(text)
    print(f"找到差异 {len(found)} 处，耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
