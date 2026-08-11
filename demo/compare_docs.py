#!/usr/bin/env python
"""
文档内容差异对比（统一入口，自动识别类型）

对比两份文档，**自动识别**是「同一文档不同版本」还是「不同文档 / 不同级别」，并
自适应选择对比方法：
    - 同一文档不同版本（结构相似）→ version_compare（版本配对），输出新增/删除/修改
    - 不同文档 / 不同级别（结构差异大）→ 相似度聚类，输出「同一主题但表述不一致」

判断依据：**内容重叠度**（较小文档中，能在另一文档找到相似段的比例）。
同文档不同版本重叠度通常 >90%；跨文档/跨级别通常 <10%，界限清晰。

特点：
- 文档路径作参数，自动识别类型，无需用户手动选择方法
- 噪声过滤（目录/记录清单/页码占位等）由系统内置 CrossNoiseFilter 提供
- 默认纯规则模式（无需 LLM），--llm 可选生成修改类摘要
- 输出报告 + 控制台摘要

用法:
    uv run --project version_diff python demo/compare_docs.py <文档A> <文档B> [--out 报告] [--llm] [阈值参数...]

可配置阈值（均有缺省值）:
    --same-threshold <float>   内容重叠度>=此值判定「同一文档不同版本」（缺省 0.5）
    --overlap-sample <int>     重叠度估算采样段数（缺省 100）
    --min-sim <float>          跨文档相似度下限（缺省 0.4）
    --max-sim <float>          跨文档相似度上限（缺省 0.95）
    --top <int>                跨文档模式列出前 N 条（缺省 20）

示例:
    # 同一文档不同版本（自动用版本对比）
    uv run --project version_diff python demo/compare_docs.py \
        "data/pdf/(二级)(司批)网络与信息安全管理手册/R5-21/(二级)(司批)网络与信息安全管理手册.pdf" \
        "data/pdf/(二级)(司批)网络与信息安全管理手册/R5-22/(二级)(司批)网络与信息安全管理手册.pdf" \
        --out demo/reports/compare_r5_21_22.md
    # 不同级别（自动用相似度聚类；自定义阈值）
    uv run --project version_diff python demo/compare_docs.py \
        "data/pdf/(二级)(司批)信息技术管理手册/R3-3/(二级)(司批)信息技术管理手册.pdf" \
        "data/pdf/(三级)(司批)信息技术部工作手册/R6-7/(三级)(司批)信息技术部工作手册.pdf" \
        --same-threshold 0.5 --min-sim 0.4 --max-sim 0.95 --out demo/reports/compare_2_3.md
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


def load_paragraphs(path: str) -> list[str]:
    from doc_parser import parse

    doc = parse(path)
    return [(p.text or "").strip() for p in doc.paragraphs if (p.text or "").strip() and len((p.text or "").strip()) >= 6]


def compute_overlap(A: list[str], B: list[str], sample: int = 100, thresh: float = 0.5) -> float:
    """估算 A 能在 B 找到相似段的比例（采样，够区分类型即可）。

    Args:
        sample: 采样段数（重叠度 90% vs <10% 界限清晰，采样即可可靠判断）
        thresh: 单段相似度阈值，>= 此值视为在另一文档找到相似内容
    """
    Bg = [grams(t) for t in B]
    hit, total = 0, 0
    for ta in A[:sample]:
        total += 1
        ga = grams(ta)
        best = max((jaccard(ga, gb) for gb in Bg), default=0.0)
        if best >= thresh:
            hit += 1
    return hit / total if total else 0.0


def run_version_compare(a, b, args, engine):
    """同一文档不同版本：version_compare。"""

    res = engine.version_compare(str(a), str(b))
    real, noise = engine.filter_cross_noise(res.changes)
    return build_version_report(a, b, real, noise, res.minor_changes)


def build_version_report(a, b, real, noise, minor):
    def label(t):
        return {"added": "新增", "removed": "删除", "modified": "修改"}.get(t, t)

    lines = [
        "# 文档内容差异（识别为：同一文档不同版本）",
        "",
        f"- 文档A: `{a}`", f"- 文档B: `{b}`",
        f"- 实质差异 **{len(real)}** 处（新增 {sum(1 for c in real if c.change_type == 'added')} / 删除 {sum(1 for c in real if c.change_type == 'removed')} / 修改 {sum(1 for c in real if c.change_type == 'modified')}）",
        f"- 噪声(版式) {len(noise)} | 细微 {len(minor)}", "",
    ]
    for key, title in [("added", "新增（B 有而 A 无）"), ("removed", "删除（A 有而 B 无）"), ("modified", "修改（表述不同）")]:
        group = [c for c in real if c.change_type == key]
        if not group:
            continue
        lines.append(f"## {title}（{len(group)} 处）")
        for c in group:
            lines.append("")
            lines.append(f"- 位置 {c.location or '未知'}")
            if c.old_text:
                lines.append(f"  旧: {c.old_text}")
            if c.new_text:
                lines.append(f"  新: {c.new_text}")
            if c.summary:
                lines.append(f"  摘要: {c.summary}")
        lines.append("")
    return "\n".join(lines)


def run_similarity_cluster(a, b, args, engine):
    """跨文档/跨级别：相似度聚类找同主题不同表述。"""
    A, B = load_paragraphs(str(a)), load_paragraphs(str(b))
    Ag, Bg = [grams(t) for t in A], [grams(t) for t in B]
    found = []
    for i, ga in enumerate(Ag):
        best_j, best_s = -1, 0.0
        for j, gb in enumerate(Bg):
            s = jaccard(ga, gb)
            if s > best_s:
                best_s, best_j = s, j
        if args.min_sim <= best_s <= args.max_sim:
            found.append((best_s, A[i], B[best_j]))
    found.sort(key=lambda x: -x[0])
    lines = [
        "# 文档内容差异（识别为：不同文档 / 不同级别）",
        "",
        f"- 文档A: `{a}`", f"- 文档B: `{b}`",
        f"- 找到「同一主题但表述不一致」**{len(found)}** 处（相似度区间 [{args.min_sim}, {args.max_sim}]）", "",
    ]
    for i, (s, ta, tb) in enumerate(found[: args.top], 1):
        lines.append(f"### [{i}] 相似度 {s:.2f}")
        lines.append(f"- A: {ta}")
        lines.append(f"- B: {tb}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="文档内容差异对比（自动识别类型）")
    parser.add_argument("doc_a")
    parser.add_argument("doc_b")
    parser.add_argument("--out", default="")
    parser.add_argument("--llm", action="store_true", help="启用 LLM 摘要（需 .env 有效 token）")
    parser.add_argument("--top", type=int, default=20, help="跨文档模式列出前 N 条")
    parser.add_argument("--embedding", default="BAAI/bge-small-zh-v1.5")
    # 可配置阈值（均有缺省值）
    parser.add_argument("--same-threshold", type=float, default=0.5,
                        help="内容重叠度 >= 此值判定为「同一文档不同版本」（缺省 0.5）")
    parser.add_argument("--overlap-sample", type=int, default=100,
                        help="重叠度估算采样段数（缺省 100）")
    parser.add_argument("--min-sim", type=float, default=0.4,
                        help="跨文档相似度下限（缺省 0.4）")
    parser.add_argument("--max-sim", type=float, default=0.95,
                        help="跨文档相似度上限（缺省 0.95）")
    args = parser.parse_args()

    for p in (args.doc_a, args.doc_b):
        if not os.path.exists(p):
            print(f"[错误] 文件不存在: {p}")
            sys.exit(1)

    load_dotenv()

    # 自动识别类型（阈值可配置，缺省 0.5）
    print("解析文档并评估内容重叠度 ...")
    A, B = load_paragraphs(args.doc_a), load_paragraphs(args.doc_b)
    overlap = compute_overlap(A, B, sample=args.overlap_sample, thresh=args.same_threshold)
    same_doc = overlap >= args.same_threshold
    print(f"A={len(A)} 段, B={len(B)} 段, 内容重叠度={overlap:.1%} → "
          f"{'同一文档不同版本' if same_doc else '不同文档/不同级别'}")

    t0 = time.time()
    if same_doc:
        config = {
            "embedding": {"model": args.embedding, "device": "cpu"},
            "llm": {"provider": "noop", "model": "", "api_key": ""},
            "diff": {"similarity_threshold": 0.80, "top_k": 3, "batch_size": 5,
                     "noise_filter": {"enabled": True}},
        }
        from version_diff import DiffEngine
        engine = DiffEngine(config=config)
        report = run_version_compare(args.doc_a, args.doc_b, args, engine)
    else:
        report = run_similarity_cluster(args.doc_a, args.doc_b, args, None)
    elapsed = time.time() - t0

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"报告已保存: {args.out}")
    else:
        print(report)
    print(f"耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
