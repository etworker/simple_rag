"""
doc_parser 单元测试

测试范围:
  - 数据模型 (Paragraph, Table, Document)
  - _clean_table 清理逻辑
  - _words_to_lines 行聚合
  - _detect_chapter 章节检测
  - _find_page 页码反查
  - _is_in_table_region 表格区域判断
  - extract_document 格式分发（含 .doc 报错）
"""
import pytest
from pathlib import Path

from doc_parser.models import Document, Paragraph, Table
from doc_parser.parser import (
    _clean_table,
    _words_to_lines,
    _detect_chapter,
    _find_page,
    _is_in_table_region,
    extract_document,
    get_extract_config,
    DEFAULT_CONFIG,
)


class TestParagraph:
    def test_location_with_page_and_chapter(self):
        p = Paragraph(text="test", page=5, chapter="2.1", chapter_title="适用范围")
        assert p.location == "第5页 / §2.1 / 适用范围"

    def test_location_page_range(self):
        p = Paragraph(text="test", page=5, page_end=8, chapter="2.1", chapter_title="适用范围")
        assert "第5-8页" in p.location

    def test_location_fallback(self):
        p = Paragraph(text="test", index=3)
        assert p.location == "段落#3"


class TestTable:
    def test_location(self):
        t = Table(rows=[], page=10, chapter="3", chapter_title="标题")
        assert "第10页" in t.location
        assert "§3" in t.location

    def test_location_fallback(self):
        t = Table(rows=[], index=2)
        assert t.location == "表格#2"


class TestCleanTable:
    def test_removes_empty_rows(self):
        data = [["a", "b"], ["", ""], ["c", "d"]]
        result = _clean_table(data)
        assert len(result) == 2
        assert result[0] == ["a", "b"]

    def test_strips_cells(self):
        data = [["  a  ", " b "]]
        result = _clean_table(data)
        assert result[0] == ["a", "b"]

    def test_empty_input(self):
        assert _clean_table([]) == []
        assert _clean_table([["", ""], ["", ""]]) == []

    def test_removes_empty_columns(self):
        data = [["a", "", "b"], ["c", "", "d"]]
        result = _clean_table(data)
        assert len(result[0]) == 2
        assert result[0] == ["a", "b"]


class TestWordsToLines:
    def test_empty(self):
        assert _words_to_lines([]) == []

    def test_single_word(self):
        words = [{"top": 10, "x0": 0, "text": "hello"}]
        lines = _words_to_lines(words)
        assert len(lines) == 1
        assert lines[0][1] == "hello"

    def test_multiple_words_same_line(self):
        words = [
            {"top": 10, "x0": 0, "text": "hello"},
            {"top": 10, "x0": 5, "text": "world"},
        ]
        lines = _words_to_lines(words)
        assert len(lines) == 1
        assert lines[0][1] == "hello world"

    def test_multiple_lines(self):
        words = [
            {"top": 10, "x0": 0, "text": "line1"},
            {"top": 20, "x0": 0, "text": "line2"},
        ]
        lines = _words_to_lines(words)
        assert len(lines) == 2
        assert lines[0][1] == "line1"
        assert lines[1][1] == "line2"

    def test_y_tolerance(self):
        words = [
            {"top": 10, "x0": 0, "text": "a"},
            {"top": 12, "x0": 5, "text": "b"},  # within 3pt
            {"top": 20, "x0": 0, "text": "c"},  # new line
        ]
        lines = _words_to_lines(words, y_tolerance=3)
        assert len(lines) == 2


class TestDetectChapter:
    def setup_method(self):
        self.cfg = DEFAULT_CONFIG.copy()

    def test_three_level(self):
        result = _detect_chapter("2.1.3 适用范围", self.cfg)
        assert result == ("2.1.3", "适用范围")

    def test_two_level(self):
        result = _detect_chapter("2.1 适用范围", self.cfg)
        assert result == ("2.1", "适用范围")

    def test_chinese_chapter(self):
        result = _detect_chapter("第3章 标题", self.cfg)
        assert result == ("3", "标题")

    def test_not_chapter(self):
        assert _detect_chapter("这是一段普通文字", self.cfg) is None

    def test_too_long(self):
        assert _detect_chapter("2.1 " + "x" * 100, self.cfg) is None

    def test_empty(self):
        assert _detect_chapter("", self.cfg) is None


class TestFindPage:
    def test_in_range(self):
        boundaries = [(0, 100, 1), (100, 200, 2), (200, 300, 3)]
        assert _find_page(50, boundaries) == 1
        assert _find_page(150, boundaries) == 2
        assert _find_page(250, boundaries) == 3

    def test_out_of_range(self):
        boundaries = [(0, 100, 1), (100, 200, 2)]
        assert _find_page(500, boundaries) == 2  # last page

    def test_empty(self):
        assert _find_page(0, []) == 0


class TestIsInTableRegion:
    def test_inside(self):
        regions = [(100, 200)]
        assert _is_in_table_region(150, regions) is True

    def test_outside(self):
        regions = [(100, 200)]
        assert _is_in_table_region(50, regions) is False
        assert _is_in_table_region(250, regions) is False

    def test_empty_regions(self):
        assert _is_in_table_region(100, []) is False


class TestExtractDocument:
    def test_doc_format_raises(self):
        with pytest.raises(ValueError, match="不支持的格式.*doc"):
            extract_document("test.doc")

    def test_unsupported_format(self):
        with pytest.raises(ValueError, match="不支持的格式"):
            extract_document("test.txt")

    def test_docx_accepted(self):
        """确保 .docx 格式不会报 ValueError('不支持的格式')"""
        # 用一个不存在的 docx 路径，应报其他错误而非 ValueError("不支持的格式")
        with pytest.raises(Exception) as exc_info:
            extract_document("nonexistent.docx")
        # 确认不是 ValueError("不支持的格式")
        assert not (isinstance(exc_info.value, ValueError) and "不支持的格式" in str(exc_info.value)), \
            ".docx should not raise unsupported format error"


class TestGetExtractConfig:
    def test_default(self):
        cfg = get_extract_config(None)
        assert cfg["min_paragraph_length"] == 10

    def test_override(self):
        cfg = get_extract_config({"extract": {"min_paragraph_length": 20}})
        assert cfg["min_paragraph_length"] == 20
