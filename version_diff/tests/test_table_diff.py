"""表格比对模块的离线回归测试。"""

from doc_parser.models import Table

from version_diff.table_diff import compare_tables


def _table(rows):
    return Table(rows=rows, chapter_title="设备清单", page=1)


def test_reports_modified_row_in_correct_fields():
    changes = compare_tables(
        [_table([["设备", "型号"], ["防火墙", "PA-850"]])],
        [_table([["设备", "型号"], ["防火墙", "PA-3260"]])],
    )

    assert len(changes) == 1
    change = changes[0]
    assert change.change_type == "modified"
    assert change.location == "行: 防火墙"
    assert change.old_text == "防火墙 | PA-850"
    assert change.new_text == "防火墙 | PA-3260"


def test_reports_added_column_in_new_text():
    changes = compare_tables(
        [_table([["设备"], ["防火墙"]])],
        [_table([["设备", "状态"], ["防火墙", "在用"]])],
    )

    added = next(change for change in changes if change.change_type == "added" and change.location == "表格结构")
    assert added.old_text == ""
    assert added.new_text == "新增列: 状态"
