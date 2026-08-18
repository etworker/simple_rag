"""
单测 - 跨页表格合并算法

覆盖场景:
1. 单页表格不合并
2. 同文件连续页 + 表头重复 → 合并
3. 不同文件的同名表格 → 不合并
4. 文件相同但不连续页码 → 不合并
5. 列数差异 > 1 → 不合并
"""

import os
import sys

# 把 src 加入 import path，使 from doc_parser import ... 可用
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "src"),
)

from doc_parser.models import Table
from doc_parser.parser import (
    _merge_cross_page_tables,
    _row_token_jaccard,
    _should_merge_tables,
)


def _mk_table(rows, page, page_end=0, source="f.pdf", chapter="", index=1):
    return Table(
        rows=rows,
        page=page,
        page_end=page_end,
        source_file=source,
        chapter=chapter,
        chapter_title="",
        index=index,
    )


def test_single_table_no_merge():
    """单张表格不合并"""
    tables = [_mk_table([["H1", "H2"], ["a", "b"]], page=1)]
    merged = _merge_cross_page_tables(tables)
    assert len(merged) == 1
    assert len(merged[0].rows) == 2


def test_adjacent_pages_same_header_merge():
    """同文件连续页 + 表头重复 → 合并, 续页表头跳过"""
    tables = [
        _mk_table(
            [["姓名", "年龄"], ["张三", "25"], ["李四", "30"]],
            page=3,
            index=1,
        ),
        _mk_table(
            [["姓名", "年龄"], ["王五", "28"]],
            page=4,
            index=2,
        ),
    ]
    merged = _merge_cross_page_tables(tables)
    assert len(merged) == 1
    assert merged[0].page == 3
    assert merged[0].page_end == 4
    assert len(merged[0].rows) == 4  # header + 3 data (张三/李四/王五); 续页表头跳过


def test_different_files_no_merge():
    """不同文件的表格不合并"""
    tables = [
        _mk_table([["H"], ["a"]], page=1, source="a.pdf"),
        _mk_table([["H"], ["b"]], page=2, source="b.pdf"),
    ]
    merged = _merge_cross_page_tables(tables)
    assert len(merged) == 2


def test_non_adjacent_pages_no_merge():
    """同文件但不连续页码 → 不合并"""
    tables = [
        _mk_table([["H"], ["a"]], page=1),
        _mk_table([["H"], ["b"]], page=3),  # 跳过第2页
    ]
    merged = _merge_cross_page_tables(tables)
    assert len(merged) == 2


def test_column_count_mismatch_no_merge():
    """列数差异 > 1 → 不合并"""
    tables = [
        _mk_table([["H1", "H2"], ["a", "b"]], page=1),
        _mk_table([["A", "B", "C", "D"], ["x", "y", "z", "w"]], page=2),
    ]
    merged = _merge_cross_page_tables(tables)
    assert len(merged) == 2


def test_triple_page_merge():
    """3 页连续表格合并"""
    tables = [
        _mk_table([["H"], ["a"], ["b"]], page=5, index=1),
        _mk_table([["H"], ["c"]], page=6, index=2),
        _mk_table([["H"], ["d"], ["e"]], page=7, index=3),
    ]
    merged = _merge_cross_page_tables(tables)
    assert len(merged) == 1
    assert merged[0].page == 5
    assert merged[0].page_end == 7
    assert len(merged[0].rows) == 6  # 1 header + 2(a,b) + 1(c) + 2(d,e)


def test_adjacent_tables_without_duplicate_header_merge():
    """相邻页且列数相同、续页不重复表头时，同章节表格仍可合并。"""
    tables = [
        _mk_table([["H"], ["a"]], page=1, chapter="1"),
        _mk_table([["b"], ["c"]], page=2, chapter="1"),  # 无表头行, 直接数据
    ]
    merged = _merge_cross_page_tables(tables)
    assert len(merged) == 1
    assert len(merged[0].rows) == 4  # H, a, b, c


def test_adjacent_unrelated_tables_do_not_merge():
    """连续页上的无关表格没有表头/章节连续性证据时不应误合并。"""
    tables = [
        _mk_table([["姓名", "年龄"], ["张三", "25"]], page=1, chapter="1"),
        _mk_table([["设备", "状态"], ["服务器", "正常"]], page=2, chapter="2"),
    ]
    merged = _merge_cross_page_tables(tables)
    assert len(merged) == 2


def test_row_token_jaccard():
    """Token Jaccard 相似度"""
    assert _row_token_jaccard(["H1", "H2"], ["H1", "H2"]) == 1.0
    assert _row_token_jaccard(["A"], ["B"]) == 0.0
    assert _row_token_jaccard([], ["B"]) == 0.0
    # partial overlap
    sim = _row_token_jaccard(["A", "B", "C"], ["A", "B", "D"])
    assert abs(sim - 2 / 4) < 1e-9


def test_should_merge_tables_different_columns():
    """列数差 > 1 不合并"""
    a = _mk_table([["A", "B"], ["1", "2"]], page=1)
    b = _mk_table([["A", "B", "C", "D"], ["x", "y", "z", "w"]], page=2)
    assert not _should_merge_tables(a, b)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
