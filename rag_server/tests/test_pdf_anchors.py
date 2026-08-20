"""PDF 字符坐标锚点和页面矩形高亮测试。"""

import os
import sys

import fitz

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import page_renderer
from app.services.pdf_anchors import build_pdf_anchors
from app.services.page_renderer import PageRenderer
from doc_parser import Document, Paragraph


def _make_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=500, height=300)
    page.insert_text((50, 60), "repeated text", fontsize=14, fontname="helv")
    page.insert_text((260, 60), "repeated text", fontsize=14, fontname="helv")
    page.insert_text((50, 130), "cross line one", fontsize=14, fontname="helv")
    page.insert_text((50, 155), "cross line two", fontsize=14, fontname="helv")
    doc.save(path)
    doc.close()


def test_build_pdf_anchors_selects_sequential_duplicate_and_cross_line(tmp_path):
    pdf_path = tmp_path / "anchors.pdf"
    _make_pdf(str(pdf_path))
    paragraphs = [
        Paragraph(text="repeated text", page=1),
        Paragraph(text="repeated text", page=1),
        Paragraph(text="cross line onecross line two", page=1),
    ]

    assert build_pdf_anchors(str(pdf_path), paragraphs) == 3
    first, second, cross_line = paragraphs
    assert first.pdf_spans and second.pdf_spans
    first_rect = first.pdf_spans[0]["rects"][0]
    second_rect = second.pdf_spans[0]["rects"][0]
    assert first_rect[0] < second_rect[0]
    assert len(cross_line.pdf_spans) == 1
    assert len(cross_line.pdf_spans[0]["rects"]) == 2


def test_paragraph_pdf_spans_round_trip():
    legacy = Paragraph.from_dict({"text": "legacy"})
    assert legacy.pdf_spans is None

    paragraph = Paragraph(
        text="定位",
        page=2,
        pdf_spans=[{"page": 2, "rects": [[1.0, 2.0, 3.0, 4.0]]}],
    )
    restored = Paragraph.from_dict(Document(filename="x", paragraphs=[paragraph]).to_dict()["paragraphs"][0])
    assert restored.pdf_spans == paragraph.pdf_spans


def test_page_renderer_uses_anchor_rects_without_text_search(tmp_path, monkeypatch):
    pdf_path = tmp_path / "render.pdf"
    _make_pdf(str(pdf_path))
    output_path = tmp_path / "page.png"

    def fail_tolerant_search(*_args, **_kwargs):
        raise AssertionError("坐标锚点存在时不应执行文本回退搜索")

    monkeypatch.setattr(page_renderer, "_tolerant_search_rects", fail_tolerant_search)
    PageRenderer(cache_dir=str(tmp_path / "cache"))._render_page(
        str(pdf_path),
        1,
        str(output_path),
        highlight="不存在的文本",
        anchor_rects=[[45.0, 45.0, 125.0, 65.0], [45.0, 115.0, 180.0, 160.0]],
    )
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_page_renderer_text_fallback_remains_available(tmp_path):
    pdf_path = tmp_path / "fallback.pdf"
    _make_pdf(str(pdf_path))
    output_path = tmp_path / "fallback.png"

    PageRenderer(cache_dir=str(tmp_path / "cache"))._render_page(
        str(pdf_path), 1, str(output_path), highlight="repeated text", anchor_rects=None
    )
    assert output_path.exists()
    assert output_path.stat().st_size > 0
