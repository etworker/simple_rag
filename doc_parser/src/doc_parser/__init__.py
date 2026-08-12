"""
doc_parser — 通用文档解析库

用法:
    from doc_parser import parse, Document, Paragraph, Table

    doc = parse("path/to/file.pdf", config={...})
    for para in doc.paragraphs:
        print(para.text, para.location)

    # 直接转 Markdown
    from doc_parser import parse_to_markdown
    md = parse_to_markdown("path/to/file.pdf")
"""

from doc_parser.models import Document, Paragraph, Table
from doc_parser.parser import parse, parse_to_markdown

__all__ = ["parse", "parse_to_markdown", "Document", "Paragraph", "Table"]
