"""
doc_parser 后端插件包。

每个 PDF 解析后端实现为一个独立模块（bk_*.py），暴露
`extract_pdf_with_<backend>(filepath, config) -> Document`，并在注册表中登记。

内置后端：
  - bk_pdfplumber : pdfplumber（默认，表格+跨页合并）
  - bk_pymupdf    : PyMuPDF（极速文本+规则表格，数字文本 PDF 快路径）
  - bk_mineru     : MinerU VLM/OCR（扫描件/复杂版面，CPU 慢）
  - bk_docling    : IBM Docling TableFormer（复杂版面/跨页表/深度学习表格）

后端模块按需懒加载：import 本包不会引入 docling/torch 等重依赖，
只有实际调用 extract() 时才加载对应驱动。
"""

import importlib

# 后端注册表：name -> 模块名（backend 包内）
BACKENDS = {
    "pdfplumber": "bk_pdfplumber",
    "pymupdf": "bk_pymupdf",
    "mineru": "bk_mineru",
    "docling": "bk_docling",
}

# 向后兼容的旧模块名 → 新模块名（mineru_backend.py 等）
_LEGACY_ALIASES = {
    "mineru_backend": "bk_mineru",
    "docling_backend": "bk_docling",
}


def load_backend(name: str):
    """加载并返回后端模块对象。"""
    mod_name = BACKENDS.get(name)
    if not mod_name:
        raise ValueError(f"未知后端: {name!r}，可用: {list(BACKENDS)}")
    return importlib.import_module(f"doc_parser.backend.{mod_name}")


def extract(filepath: str, config: dict | None = None):
    """按配置选择后端并解析（等价 doc_parser._pdf.extract_pdf 的分发逻辑）。"""
    from doc_parser._pdf import extract_pdf

    return extract_pdf(filepath, config)


__all__ = ["BACKENDS", "extract", "load_backend"]
