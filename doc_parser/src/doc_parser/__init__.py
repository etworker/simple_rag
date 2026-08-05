"""
docparser — 通用文档解析库

用法:
    from docparser import parse, Document, Paragraph, Table

    doc = parse("path/to/file.pdf", config={...})
    for para in doc.paragraphs:
        print(para.text, para.location)
"""

from doc_parser.models import Document, Paragraph, Table
from doc_parser.parser import parse

__all__ = ["parse", "Document", "Paragraph", "Table"]
