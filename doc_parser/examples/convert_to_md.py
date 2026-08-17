#!/usr/bin/env python
"""
doc_parser example — PDF/Word → Markdown 转换

用法:
    python examples/convert_to_md.py <文件路径> [--out 输出.md] [--config 配置项]

示例:
    python examples/convert_to_md.py "data/pdf/(三级)(司批)信息技术部工作手册/R6-7/(三级)(司批)信息技术部工作手册.pdf"
    python examples/convert_to_md.py input.pdf --out output.md
    python examples/convert_to_md.py input.docx --min-paragraph-length 20
"""

import argparse
import os
import sys
import time

# 确保能 import doc_parser
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))

from doc_parser import parse  # noqa: E402
from doc_parser.log import configure_logger  # noqa: E402

configure_logger()  # 独立 example：开启 doc_parser 日志（console + ./logs/doc_parser.log）


def main():
    parser = argparse.ArgumentParser(
        description="把 PDF / Word 文档转为 Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python examples/convert_to_md.py input.pdf --out output.md",
    )
    parser.add_argument("filepath", help="输入文件路径 (.pdf / .docx)")
    parser.add_argument("--out", "-o", default="", help="输出 Markdown 文件路径（默认: 同名 .md）")
    parser.add_argument("--min-paragraph-length", type=int, default=10, help="最小段落长度（默认 10）")
    parser.add_argument("--max-paragraph-length", type=int, default=600, help="最大段落长度（默认 600）")
    parser.add_argument("--header-margin", type=int, default=8, help="页眉区域百分比（默认 8）")
    parser.add_argument("--footer-margin", type=int, default=8, help="页脚区域百分比（默认 8）")
    parser.add_argument("--no-template-filter", action="store_true", help="禁用空白模板表格过滤")
    args = parser.parse_args()

    if not os.path.exists(args.filepath):
        print(f"错误: 文件不存在: {args.filepath}")
        sys.exit(1)

    # 构造配置
    config = {
        "extract": {
            "min_paragraph_length": args.min_paragraph_length,
            "max_paragraph_length": args.max_paragraph_length,
            "header_margin_pct": args.header_margin,
            "footer_margin_pct": args.footer_margin,
        }
    }
    if args.no_template_filter:
        config["extract"]["table_empty_cell_threshold"] = 1.0

    # 解析 + 转换
    print(f"正在解析: {args.filepath}")
    t0 = time.time()

    doc = parse(args.filepath, config=config)
    print(f"解析完成: {len(doc.paragraphs)} 段, {len(doc.tables)} 表 ({time.time() - t0:.1f}s)")

    md = doc.to_markdown()
    print(f"Markdown 生成: {len(md)} 字符")

    # 输出
    out_path = args.out
    if not out_path:
        base = os.path.splitext(args.filepath)[0]
        out_path = base + ".md"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"已写入: {out_path}")


if __name__ == "__main__":
    main()
