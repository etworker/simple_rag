"""
示例：验证 simple_rag 的「跨文档冲突检测 + LLM 矛盾归纳」端到端能力。

与 verify_version_diff.py 的区别：
  - 版本对比（verify_version_diff.py）：同一文档的新旧版本，看"变了什么"。
  - 跨文档审核（本脚本）：新文档 vs 已有文档库，看"是否矛盾"。

流程：
  1. 将若干已有文档加入引擎知识库（engine.add）
  2. 对新文档执行预审核（engine.pre_review）
     → 跨文档语义检索 → 字级 Diff → 规则预过滤 → LLM 矛盾判断
  3. 输出不一致列表，并用 LLM 归纳为结构化中文摘要

用法（在 version_diff 目录下执行）：
    # 默认：用二级管理手册做知识库，审核三级工作手册
    uv run python examples/verify_cross_doc_conflict.py

    # 自定义已有文档（可多个）和待审核文档
    uv run python examples/verify_cross_doc_conflict.py \
        --existing a.pdf --existing b.pdf --new c.pdf

    # 仅做冲突检测、跳过 LLM 归纳（更快，离线可跑）
    uv run python examples/verify_cross_doc_conflict.py --no-summary

    # 指定归纳用的 LLM 模型
    uv run python examples/verify_cross_doc_conflict.py --model zai.glm-4.7-flash
"""

import argparse
import os
import pathlib
import sys
import time

from llm_chat.defaults import DEFAULT_LLM_MODEL
from loguru import logger as log

from version_diff.log import configure_logger
from version_diff.paths import DEFAULT_EMBEDDING_MODEL

configure_logger()  # 独立 example：开启 version_diff 日志（console + ./logs/version_diff.log）

# 脚本位于 version_diff/examples/ → parents[2] 即项目根（simple_rag）
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PDF_DIR = REPO_ROOT / "data" / "pdf"

# 默认示例：用二级管理手册做知识库，审核三级工作手册
DEFAULT_EXISTING = [
    PDF_DIR / "(二级)(司批)信息技术管理手册" / "R3-3" / "(二级)(司批)信息技术管理手册.pdf",
    PDF_DIR / "(二级)(司批)网络与信息安全管理手册" / "R5-22" / "(二级)(司批)网络与信息安全管理手册.pdf",
]
DEFAULT_NEW = PDF_DIR / "(三级)(司批)信息技术部工作手册" / "R6-7" / "(三级)(司批)信息技术部工作手册.pdf"


def build_summary_prompt(new_name, existing_names, result):
    """把 pre_review 的矛盾列表整理成中文归纳 prompt。"""
    inconsistencies = result.inconsistencies

    lines = []
    lines.append("你是跨文档一致性审核专家。")
    lines.append(f"新文档《{new_name}》已与已有文档库进行交叉比对。")
    lines.append(f"已有文档: {', '.join(existing_names)}\n")
    lines.append(f"共检测到 {len(inconsistencies)} 处潜在不一致。\n")
    lines.append("请使用中文，按以下结构归纳：")
    lines.append("1. 【不一致概览】用 2-3 句话概括矛盾的性质和严重程度。")
    lines.append("2. 【关键矛盾清单】列出最重要的 5-10 处不一致，说明各文档分别如何表述、为何矛盾。")
    lines.append("3. 【处置建议】给出修订建议（如：以某文档为准 / 需人工裁决 / 可忽略的表述差异等）。\n")
    lines.append("逐条不一致如下（已截断长文本）：\n")

    def trunc(s, n=150):
        s = (s or "").replace("\n", " ")
        return s if len(s) <= n else s[:n] + "…"

    for idx, inc in enumerate(inconsistencies[:30], start=1):
        lines.append(f"{idx}. 矛盾事项: {inc.point}")
        lines.append(f"   - 《{inc.doc_a_file}》{inc.doc_a_location}: {trunc(inc.doc_a_says)}")
        lines.append(f"   - 《{inc.doc_b_file}》{inc.doc_b_location}: {trunc(inc.doc_b_says)}")
        lines.append("")

    return "\n".join(lines)


def main(existing_paths, new_path, model, do_summary):
    from llm_chat import ask_once

    from version_diff import DiffEngine

    # 校验文件存在
    for p in existing_paths:
        if not os.path.exists(p):
            log.error(f"已有文档不存在: {p}")
            sys.exit(1)
    if not os.path.exists(new_path):
        log.error(f"待审核文档不存在: {new_path}")
        sys.exit(1)

    new_name = os.path.basename(new_path)
    existing_names = [os.path.basename(p) for p in existing_paths]

    print("\n=== 跨文档冲突检测 ===")
    print(f"已有文档库 ({len(existing_paths)} 份):")
    for p in existing_paths:
        print(f"  - {p}")
    print(f"待审核文档: {new_path}")

    cfg = {
        "embedding": {"model": DEFAULT_EMBEDDING_MODEL, "device": "cpu"},
        "llm": {
            "provider": "bedrock_converse",
            "model": model,
            "max_tokens": 2048,
            "timeout": 120,
            "max_retries": 3,
            "retry_backoff": 2.0,
        },
        "diff": {"similarity_threshold": 0.80, "top_k": 3, "batch_size": 5},
    }

    engine = DiffEngine(config=cfg)

    # Step 1: 构建已有文档库
    print("\n--- Step 1: 加载已有文档 ---")
    for p in existing_paths:
        t0 = time.time()
        engine.add(p)
        print(f"  已加载: {os.path.basename(p)} ({time.time() - t0:.1f}s)")

    # Step 2: 预审核新文档
    print("\n--- Step 2: 预审核新文档 ---")
    t0 = time.time()
    result = engine.pre_review(new_path, doc_filename=new_name)
    elapsed = time.time() - t0

    print(f"\n--- pre_review 结果（耗时 {elapsed:.1f}s）---")
    print(f"候选对数: {result.total_candidates}")
    print(f"规则过滤: {result.rule_filtered}")
    print(f"LLM 判断: {result.llm_judged}")
    print(f"不一致总数: {len(result.inconsistencies)}", end="")
    if result.dedup_count:
        print(f"（已合并 {result.dedup_count} 处重复）", end="")
    print()
    if result.suspects:
        print(f"疑似不一致: {len(result.suspects)} 处（需人工复核）")

    if result.is_safe:
        if result.suspects:
            print("\n✅ 未发现确定性矛盾，但有疑似不一致需人工复核。")
        else:
            print("\n✅ 未发现与已有文档的矛盾，可安全入库。")
    else:
        print(f"\n⚠️ 发现 {len(result.inconsistencies)} 处文档间不一致：\n")
        for i, inc in enumerate(result.inconsistencies[:10], 1):
            print(f"  [{i}] {inc.point}")
            print(f"      《{inc.doc_a_file}》{inc.doc_a_location}: {inc.doc_a_says[:80]}")
            print(f"      《{inc.doc_b_file}》{inc.doc_b_location}: {inc.doc_b_says[:80]}")
            print()
        if len(result.inconsistencies) > 10:
            print(f"  ...（仅展示前 10 处，共 {len(result.inconsistencies)} 处）")
        if result.suspects:
            print(f"\n  ⚠️ 疑似不一致（{len(result.suspects)} 处）：")
            for i, inc in enumerate(result.suspects[:5], 1):
                print(f"    [{i}] {inc.point}: {inc.doc_a_says[:50]} ↔ {inc.doc_b_says[:50]}")
            if len(result.suspects) > 5:
                print(f"    ...（仅展示前 5 处，共 {len(result.suspects)} 处）")

    # Step 3: LLM 归纳
    summary = ""
    if do_summary and not result.is_safe:
        print("\n=== LLM 矛盾归纳 ===")
        t1 = time.time()
        prompt = build_summary_prompt(new_name, existing_names, result)
        summary = ask_once(
            prompt,
            system_prompt="你是一名严谨的跨文档一致性审核专家，输出结构化、客观的中文分析。",
            model=model,
            max_tokens=1500,
            timeout=180,
        )
        s_elapsed = time.time() - t1
        print(f"LLM 归纳完成（耗时 {s_elapsed:.1f}s）\n")
        print("=" * 60)
        print(summary)
        print("=" * 60)
    elif do_summary and result.is_safe:
        print("\n（无不一致项，跳过 LLM 归纳）")
    else:
        print("\n（已跳过 LLM 归纳：--no-summary）")

    # 落盘
    out_dir = REPO_ROOT / "temp"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "verify_cross_doc_conflict_output.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"跨文档审核: {new_name}\n")
        f.write(f"已有文档库: {', '.join(existing_names)}\n")
        f.write(
            f"候选对数: {result.total_candidates}  规则过滤: {result.rule_filtered}  LLM判断: {result.llm_judged}\n"
        )
        f.write(f"不一致总数: {len(result.inconsistencies)}")
        if result.dedup_count:
            f.write(f"（已合并 {result.dedup_count} 处重复）")
        f.write("\n")
        if result.suspects:
            f.write(f"疑似不一致: {len(result.suspects)} 处\n")
        f.write(f"pre_review 耗时: {elapsed:.1f}s\n\n")
        if result.inconsistencies:
            f.write("【不一致详情】\n")
            for i, inc in enumerate(result.inconsistencies, 1):
                f.write(f"\n#{i} {inc.point}\n")
                f.write(f"  A: 《{inc.doc_a_file}》{inc.doc_a_location}\n")
                f.write(f"     {inc.doc_a_says}\n")
                f.write(f"  B: 《{inc.doc_b_file}》{inc.doc_b_location}\n")
                f.write(f"     {inc.doc_b_says}\n")
        if summary:
            f.write("\n【LLM 归纳摘要】\n")
            f.write(summary)
    print(f"\n输出已保存: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证跨文档冲突检测 + LLM 矛盾归纳（示例脚本）")
    parser.add_argument(
        "--existing",
        action="append",
        default=None,
        help="已有文档路径（可重复指定多个）",
    )
    parser.add_argument("--new", default=str(DEFAULT_NEW), help="待审核文档路径")
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL, help="归纳用 LLM 模型")
    parser.add_argument("--no-summary", action="store_true", help="仅做冲突检测，跳过 LLM 归纳")
    args = parser.parse_args()

    existing = args.existing or [str(p) for p in DEFAULT_EXISTING]
    main(existing, args.new, args.model, not args.no_summary)
