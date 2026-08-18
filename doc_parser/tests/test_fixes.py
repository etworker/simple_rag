"""
doc_parser 智能后端选择 / 章节识别 / 数字断字修复测试（离线纯单测）

覆盖本次修复的三个问题：
1. 公文（红头+印章大图但文本充足）不应被误判为扫描件走 MinerU
2. "3 万人次"/"6 月份" 等统计数字不应被误识别为章节标题
3. PDF 文本层数字断字（"28. 55 万小时"）后处理修复
"""

from doc_parser._text import _NUM_UNIT_PREFIXES, normalize_number_spacing
from doc_parser.parser import (
    DEFAULT_CONFIG,
    _detect_chapter,
)


def _cfg():
    return dict(DEFAULT_CONFIG)


class TestNormalizeNumberSpacing:
    def test_decimal_after_space_fixed(self):
        assert normalize_number_spacing("28. 55 万小时") == "28.55 万小时"

    def test_percent_decimal_fixed(self):
        assert normalize_number_spacing("上升 0. 18%、 0. 39%") == "上升 0.18%、 0.39%"

    def test_large_thousands_decimal_fixed(self):
        assert normalize_number_spacing("2632. 3 万人次") == "2632.3 万人次"

    def test_unit_joined_fixed(self):
        assert normalize_number_spacing("57 10. 万架次") == "57.10 万架次"
        assert normalize_number_spacing("26 63. 万吨") == "26.63 万吨"

    def test_normal_text_unchanged(self):
        assert normalize_number_spacing("共完成运输飞行 28.55 万小时") == "共完成运输飞行 28.55 万小时"
        assert normalize_number_spacing("第 5 章 安全管理") == "第 5 章 安全管理"


class TestDetectChapterStatNumbers:
    def test_unit_numbers_not_chapters(self):
        for text in ("3 万人次", "6 月份，管理局通过", "10 万架次", "28 万小时"):
            assert _detect_chapter(text, _cfg()) is None, f"{text!r} 不应被识别为章节"

    def test_real_chapters_kept(self):
        for text, expect in (
            ("2 职责分工", "职责分工"),
            ("3 网络管理", "网络管理"),
            ("5 附则", "附则"),
            ("1 修订记录", "修订记录"),
        ):
            ch = _detect_chapter(text, _cfg())
            assert ch is not None, f"{text!r} 应被识别为章节"
            assert ch[1] == expect

    def test_unit_prefix_list_has_key_units(self):
        assert any(p in _NUM_UNIT_PREFIXES for p in ("万人次", "月份", "万架次", "万小时"))


class TestSelectBackendNotScanWhenTextRich:
    """大图覆盖但文本充足（公文红头/印章）不应误判扫描件。纯逻辑验证：

    select_backend 的判定依赖 _quick_scan_pdf 的真实扫描，这里直接构造 scan
    结构验证 select_backend 的分支（通过 monkeypatch _quick_scan_pdf）。
    """

    def test_rich_text_with_large_images_uses_pymupdf(self, monkeypatch):
        """大图覆盖但文本充足（公文红头/印章）→ 快路径 PyMuPDF，不误判扫描件。"""
        from doc_parser import _pdf

        fake_scan = {
            "num_pages": 9,
            "sampled": 5,
            "avg_text_per_page": 642.6,
            "avg_tables_per_page": 0.0,
            "large_image_ratio": 1.0,
            "has_drawings_no_tables": False,
            "text_samples": ["样本文本"] * 5,
        }
        monkeypatch.setattr(_pdf, "_quick_scan_pdf", lambda *a, **k: fake_scan)
        backend, _reason = _pdf.select_backend("fake.pdf", _cfg())
        assert backend == "pymupdf"

    def test_sparse_text_with_large_images_uses_mineru(self, monkeypatch):
        from doc_parser import _pdf

        fake_scan = {
            "num_pages": 9,
            "sampled": 5,
            "avg_text_per_page": 30,
            "avg_tables_per_page": 0.0,
            "large_image_ratio": 1.0,
            "has_drawings_no_tables": False,
            "text_samples": [],
        }
        monkeypatch.setattr(_pdf, "_quick_scan_pdf", lambda *a, **k: fake_scan)
        backend, reason = _pdf.select_backend("fake.pdf", _cfg())
        assert backend == "mineru"
        assert "扫描件" in reason

    def test_rich_text_low_images_uses_pymupdf(self, monkeypatch):
        """数字文本 + 表格提取正常 → 快路径 PyMuPDF。"""
        from doc_parser import _pdf

        fake_scan = {
            "num_pages": 9,
            "sampled": 5,
            "avg_text_per_page": 600,
            "avg_tables_per_page": 0.5,
            "large_image_ratio": 0.0,
            "has_drawings_no_tables": False,
            "text_samples": [],
        }
        monkeypatch.setattr(_pdf, "_quick_scan_pdf", lambda *a, **k: fake_scan)
        backend, _reason = _pdf.select_backend("fake.pdf", _cfg())
        assert backend == "pymupdf"

    def test_borderless_table_hint_uses_docling(self, monkeypatch):
        """文本表格关键词 + 列对齐（疑似无框线表格）→ 深度学习后端 Docling。"""
        from doc_parser import _pdf

        fake_scan = {
            "num_pages": 9,
            "sampled": 5,
            "avg_text_per_page": 500,
            "avg_tables_per_page": 0.0,
            "large_image_ratio": 0.0,
            "has_drawings_no_tables": False,
            "text_samples": [
                "序号   名称   描述   风险   措施",
                "1     名称   火灾   高     灭火",
                "2     名称   中断   中     备用",
            ]
            * 2,
        }
        monkeypatch.setattr(_pdf, "_quick_scan_pdf", lambda *a, **k: fake_scan)
        backend, reason = _pdf.select_backend("fake.pdf", _cfg())
        assert backend == "docling"
        assert "表格" in reason

    def test_drawings_without_tables_uses_docling(self, monkeypatch):
        """有绘图对象但规则后端提取不到表格 → Docling（替代原 MinerU 路由）。"""
        from doc_parser import _pdf

        fake_scan = {
            "num_pages": 9,
            "sampled": 5,
            "avg_text_per_page": 500,
            "avg_tables_per_page": 0.0,
            "large_image_ratio": 0.0,
            "has_drawings_no_tables": True,
            "text_samples": ["样本文本"] * 5,
        }
        monkeypatch.setattr(_pdf, "_quick_scan_pdf", lambda *a, **k: fake_scan)
        backend, _reason = _pdf.select_backend("fake.pdf", _cfg())
        assert backend == "docling"


class TestPdfAutoSampling:
    def test_representative_sampling_includes_document_tail(self):
        from doc_parser._pdf import _sample_page_indices

        assert _sample_page_indices(9, 5) == [0, 2, 4, 6, 8]
        assert _sample_page_indices(9, 0) == list(range(9))
        assert _sample_page_indices(3, 5) == [0, 1, 2]

    def test_borderless_header_split_across_lines_uses_docling(self, monkeypatch):
        """通报中的“序\n号 + 风险”表头即使只有单空格也应触发 Docling。"""
        from doc_parser import _pdf

        fake_scan = {
            "num_pages": 9,
            "sampled": 5,
            "avg_text_per_page": 500,
            "avg_tables_per_page": 0.0,
            "large_image_ratio": 0.0,
            "has_drawings_no_tables": False,
            "text_samples": [
                "正文内容",
                "附件\n序\n号 风险措述 涉及企事业单位 局方督导单位部门",
                "正文内容",
                "正文内容",
                "正文内容",
            ],
        }
        monkeypatch.setattr(_pdf, "_quick_scan_pdf", lambda *a, **k: fake_scan)
        backend, reason = _pdf.select_backend("fake.pdf", _cfg())
        assert backend == "docling"
        assert "表格" in reason
