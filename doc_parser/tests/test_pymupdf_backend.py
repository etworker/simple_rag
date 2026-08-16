"""
PyMuPDF 后端测试（离线纯单测）

覆盖：
  1. BACKENDS 注册表包含 pymupdf，load_backend 可加载
  2. extract_pdf 分发支持 backend="pymupdf"（含空 PDF 返回空 Document）
  3. 生成 2 页含章节标题/段落/框线表格的 PDF，验证段落/表格/章节提取
"""

import os
import tempfile

import pytest

from doc_parser.backend import BACKENDS, load_backend
from doc_parser.models import Document

fitz = pytest.importorskip("pymupdf")


def _make_test_pdf(path):
    """用 reportlab 生成 2 页 PDF：章节标题 + 段落 + 跨页框线表格。

    reportlab 的 Table 生成标准框线表格，pdfplumber / PyMuPDF find_tables 都能稳定识别。
    页面 1 放表格前半（表头 + 3 行），页面 2 放续表（重复表头 + 3 行），
    验证 _merge_cross_page_tables 的"页码连续 + 列数相同 + 跳过重复表头"合并。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    doc = SimpleDocTemplate(path, pagesize=A4)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("1  Introduction", styles["Heading1"]),
        Paragraph("This is the first paragraph of the document.", styles["BodyText"]),
    ]
    header = ["Col A", "Col B"]
    rows1 = [[str(i), "X" + str(i)] for i in range(1, 4)]
    rows2 = [[str(i), "X" + str(i)] for i in range(4, 7)]

    def _table(data):
        t = Table([header] + data, colWidths=[100, 100])
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, "black"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ]
            )
        )
        return t

    story.append(_table(rows1))
    story.append(PageBreak())
    story.append(_table(rows2))  # 续表（重复表头）
    doc.build(story)


class TestBackendRegistry:
    def test_pymupdf_registered(self):
        assert "pymupdf" in BACKENDS
        assert BACKENDS["pymupdf"] == "bk_pymupdf"

    def test_load_backend(self):
        mod = load_backend("pymupdf")
        assert hasattr(mod, "extract_pdf_with_pymupdf")


class TestPymupdfBackend:
    def test_blank_pdf_returns_empty_document(self):
        """单页无文本 PDF → 空 Document（fitz 不允许保存 0 页 PDF）。"""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "blank.pdf")
            doc = fitz.open()
            doc.new_page()  # 1 页空页
            doc.save(path)
            doc.close()
            from doc_parser._pdf import extract_pdf

            result = extract_pdf(path, {"extract": {"backend": "pymupdf"}})
            assert isinstance(result, Document)
            assert result.paragraphs == []
            assert result.tables == []

    def test_parse_generated_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "gen.pdf")
            _make_test_pdf(path)
            from doc_parser._pdf import extract_pdf

            result = extract_pdf(path, {"extract": {"backend": "pymupdf"}})
            assert isinstance(result, Document)
            assert len(result.paragraphs) >= 2
            # 章节标题识别（英文数字标题）
            chapters = {p.chapter for p in result.paragraphs if p.chapter}
            assert "1" in chapters
            # 表格：跨页表格应被合并为单个
            assert len(result.tables) >= 1
            # 跨页合并后 page_end > page
            assert any(t.page_end > t.page for t in result.tables)
