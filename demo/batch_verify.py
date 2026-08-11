#!/usr/bin/env python
"""
多组合文档差异批量验证（demo 辅助）

对若干「同文档不同版本 / 不同级别 / 不同文档」组合执行内容差异对比，
输出每个组合的差异统计 + 前 N 条代表性真实差异，作为验证与示例。

用法:
    uv run --project version_diff python demo/batch_verify.py [--out 输出] [--top N] [--max-changes N]
"""
import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "version_diff"))

PDF = PROJECT_ROOT / "data" / "pdf"


def load_dotenv():
    dotenv = PROJECT_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# 组合：(文档A 相对路径, 文档B 相对路径, 类型, 说明)
COMBOS = [
    ("(二级)(司批)网络与信息安全管理手册/R5-21/(二级)(司批)网络与信息安全管理手册.pdf",
     "(二级)(司批)网络与信息安全管理手册/R5-22/(二级)(司批)网络与信息安全管理手册.pdf",
     "同文档不同版本", "网络与信息安全管理手册 R5-21 → R5-22（相邻修订）"),
    ("(二级)(司批)网络与信息安全管理手册/R5-18/(二级)(司批)网络与信息安全管理手册.pdf",
     "(二级)(司批)网络与信息安全管理手册/R5-22/(二级)(司批)网络与信息安全管理手册.pdf",
     "同文档不同版本", "网络与信息安全管理手册 R5-18 → R5-22（跨版修订）"),
    ("(二级)(司批)信息技术管理手册/R3-3/(二级)(司批)信息技术管理手册.pdf",
     "(三级)(司批)信息技术部工作手册/R6-7/(三级)(司批)信息技术部工作手册.pdf",
     "不同级别", "二级《信息技术管理手册》 vs 三级《信息技术部工作手册》"),
    ("(二级)(司批)网络与信息安全管理手册/R5-22/(二级)(司批)网络与信息安全管理手册.pdf",
     "IT运维管理规范/v2/IT运维管理规范.pdf",
     "不同文档", "《网络与信息安全管理手册》 vs 《IT运维管理规范》"),
]


def fmt_label(t):
    return {"added": "新增", "removed": "删除", "modified": "修改"}.get(t, t)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="报告输出路径（默认打印控制台）")
    parser.add_argument("--top", type=int, default=5, help="每个组合列出前 N 条差异")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个组合（0=全部）")
    args = parser.parse_args()

    load_dotenv()
    from version_diff import DiffEngine

    config = {
        "embedding": {"model": "BAAI/bge-small-zh-v1.5", "device": "cpu"},
        "llm": {"provider": "noop", "model": "", "api_key": ""},
        "diff": {"similarity_threshold": 0.80, "top_k": 3, "batch_size": 5, "noise_filter": {"enabled": True}},
    }
    engine = DiffEngine(config=config)

    combos = COMBOS[: args.limit] if args.limit else COMBOS
    report = []
    for a_rel, b_rel, ctype, desc in combos:
        a, b = PDF / a_rel, PDF / b_rel
        report.append(f"## [{ctype}] {desc}")
        report.append(f"- A: `{a_rel}`")
        report.append(f"- B: `{b_rel}`")
        if not (a.exists() and b.exists()):
            report.append("- 文件缺失，跳过")
            continue
        t0 = time.time()
        print(f"对比中: {desc} ...", flush=True)
        try:
            res = engine.version_compare(str(a), str(b))
            real, noise = engine.filter_cross_noise(res.changes)
        except Exception as e:  # noqa: BLE001  - 单个组合失败不中断整体
            report.append(f"- 失败: {e}")
            continue
        el = time.time() - t0
        stats = {
            "total": len(real),
            "added": sum(1 for c in real if c.change_type == "added"),
            "removed": sum(1 for c in real if c.change_type == "removed"),
            "modified": sum(1 for c in real if c.change_type == "modified"),
            "noise": len(noise),
            "minor": len(res.minor_changes),
        }
        report.append(f"- 耗时 {el:.1f}s | 实质差异 {stats['total']}（新增 {stats['added']} / 删除 {stats['removed']} / "
                      f"修改 {stats['modified']}）| 噪声 {stats['noise']} | 细微 {stats['minor']}")
        report.append("")
        # 代表性真实差异
        for i, c in enumerate(real[: args.top], 1):
            t = fmt_label(c.change_type)
            report.append(f"### {t}@{c.location or '未知'}")
            if c.old_text:
                report.append(f"- 旧: {c.old_text}")
            if c.new_text:
                report.append(f"- 新: {c.new_text}")
            if c.summary:
                report.append(f"- 摘要: {c.summary}")
            report.append("")
        report.append("---")
        report.append("")
        print(f"  完成: 实质差异 {stats['total']}（{el:.1f}s）", flush=True)

    text = "\n".join(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"报告已保存: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
