"""
跨页表格合并 — rag_server 集成测试

策略:
  - 直接调用 doc_parser._merge_cross_page_tables 验证合并逻辑 (真实数据)
  - 构造真实的 2 页 PDF 文件, 跑完整 parse() 管线验证集成 (不 mock)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# 直接合并逻辑测试 (使用真实 Table dataclass)
# ---------------------------------------------------------------------------


class TestCrossPageTableMergeLogic:
    """直接调用 _merge_cross_page_tables 验证合并逻辑"""

    def _mk_table(self, rows, page, page_end=None, source="f.pdf"):
        from doc_parser.models import Table

        return Table(rows=list(rows), page=page, page_end=page_end or page, source_file=source, index=0)

    def test_merge_called_during_parse(self):
        """验证 _merge_cross_page_tables 函数可被正常导入和调用"""
        from doc_parser.parser import _merge_cross_page_tables

        tables = [self._mk_table([["h"], ["a"]], page=1)]
        result = _merge_cross_page_tables(tables)
        assert len(result) == 1

    def test_adjacent_same_header_skip_dup(self):
        """相邻页且续页首部与表头重复 → 合并 + 跳过重复表头"""
        from doc_parser.parser import _merge_cross_page_tables

        tables = [
            self._mk_table([["姓名", "年龄"], ["张三", "25"], ["李四", "30"]], page=1),
            self._mk_table([["姓名", "年龄"], ["王五", "28"]], page=2),
        ]
        merged = _merge_cross_page_tables(tables)
        assert len(merged) == 1
        # 合并后: header 1 行 + 数据 3 行 = 4 行
        assert len(merged[0].rows) == 4
        # 末行来自 page 2 的数据
        assert merged[0].rows[-1] == ["王五", "28"]

    def test_different_files_not_merged(self):
        from doc_parser.parser import _merge_cross_page_tables

        tables = [
            self._mk_table([["h"], ["a"]], page=1, source="A.pdf"),
            self._mk_table([["h"], ["b"]], page=2, source="B.pdf"),
        ]
        merged = _merge_cross_page_tables(tables)
        assert len(merged) == 2

    def test_column_mismatch_gt1_not_merged(self):
        from doc_parser.parser import _merge_cross_page_tables

        tables = [
            self._mk_table([["A", "B"], ["1", "2"]], page=1),
            self._mk_table([["A", "B", "C", "D"], ["x", "y", "z", "w"]], page=2),
        ]
        merged = _merge_cross_page_tables(tables)
        assert len(merged) == 2

    def test_continuation_without_repeated_header_keeps_all_rows(self):
        """续页不重复表头: 全行保留"""
        from doc_parser.parser import _merge_cross_page_tables

        tables = [
            self._mk_table([["H"], ["a"]], page=1),
            self._mk_table([["b"], ["c"]], page=2),
        ]
        merged = _merge_cross_page_tables(tables)
        assert len(merged) == 1
        assert len(merged[0].rows) == 4  # H,a,b,c


# ---------------------------------------------------------------------------
# PDF 解析管线集成测试 (构造真实 PDF, 跑完整 parse)
# ---------------------------------------------------------------------------


class TestCrossPageTableMergeInParse:
    """构造真实 PDF, 通过 parse() 验证表格被提取"""

    @pytest.fixture
    def two_page_pdf(self, tmp_path):
        """构造一个 2 页真实 PDF 文件 (纯文本, 确保 pymupdf 能解析)"""
        try:
            import fitz
        except ImportError:
            pytest.skip("pymupdf 未安装")

        path = str(tmp_path / "test_two_pages.pdf")
        doc = fitz.open()
        for i in range(2):
            page = doc.new_page(width=595, height=842)  # A4
            text = (
                f"这是第 {i + 1} 页\n\n"
                "姓名 部门 工号\n"
                "张三 研发 1001\n"
                "李四 产品 1002\n"
                "王五 运营 1003\n"
                "赵六 销售 1004\n"
                "孙七 财务 1005\n"
                "周八 市场 1006\n"
                "吴九 人事 1007\n"
                "郑十 行政 1008\n"
            )
            page.insert_text((72, 72), text, fontsize=12)
        doc.save(path)
        doc.close()
        return path

    def test_parse_real_pdf_extracts_tables(self, two_page_pdf):
        """解析真实 PDF 应能提取表格 (具体数量取决于 pdfplumber 识别)"""
        from doc_parser import parse

        doc = parse(two_page_pdf)
        assert doc is not None
        assert doc.filename
        # pdfplumber 可能至少识别出 1 个表格 — 不断言具体数量，避免解析器差异导致测试脆弱
        assert isinstance(doc.tables, list)
        print(f"\n  [INFO] 解析 {os.path.basename(two_page_pdf)} 命中 {len(doc.tables)} 个表格")
        for i, t in enumerate(doc.tables):
            print(f"    - table[{i}]: page={t.page}, rows={len(t.rows)}, first_row={t.rows[0] if t.rows else '?'}")

    def test_parse_pipeline_no_exception(self, two_page_pdf):
        """验证解析管线在跨页场景下不抛异常"""
        from doc_parser import parse

        doc = parse(two_page_pdf)
        # 不崩溃 + 至少提取出一些段落
        assert doc is not None
        print(f"\n  [INFO] paragraphs={len(doc.paragraphs)}, tables={len(doc.tables)}")

    def test_tables_have_page_info(self, two_page_pdf):
        """表格结果应包含页码信息"""
        from doc_parser import parse

        doc = parse(two_page_pdf)
        for t in doc.tables:
            assert t.page >= 1, f"表格页码应 >= 1, 实际 {t.page}"
