"""表格比对模块的离线回归测试。"""

from doc_parser.models import Table

from version_diff.table_diff import compare_tables


def _table(rows, page=1):
    return Table(rows=rows, chapter_title="设备清单", page=page)


def test_reports_modified_row_in_correct_fields():
    changes = compare_tables(
        [_table([["设备", "型号"], ["防火墙", "PA-850"]])],
        [_table([["设备", "型号"], ["防火墙", "PA-3260"]])],
    )

    assert len(changes) == 1
    change = changes[0]
    assert change.change_type == "modified"
    assert change.location == "第1页 / 设备清单"
    assert change.old_location == "第1页 / 设备清单"
    assert change.old_text == "防火墙 | PA-850"
    assert change.new_text == "防火墙 | PA-3260"
    assert change.table_name == "第1页 / 设备清单"
    assert change.row_key == "防火墙"
    assert change.row_index == 1
    assert change.cell_changes == [{"column": "型号", "old_value": "PA-850", "new_value": "PA-3260"}]


def test_reports_added_row_with_table_row_and_column_details():
    changes = compare_tables(
        [_table([["序号", "日期"], ["21", "2025-01-01"]])],
        [_table([["序号", "日期"], ["21", "2025-01-01"], ["22", "2026-05-08"]])],
    )

    added = next(change for change in changes if change.change_type == "added")
    assert added.table_name == "第1页 / 设备清单"
    assert added.row_key == "22"
    assert added.row_index == 2
    assert added.cell_changes == [
        {"column": "序号", "old_value": "", "new_value": "22"},
        {"column": "日期", "old_value": "", "new_value": "2026-05-08"},
    ]


def test_supports_tables_with_separate_headers():
    old = Table(headers=["序号", "日期"], rows=[["21", "2025-01-01"]], chapter_title="修订记录")
    new = Table(headers=["序号", "日期"], rows=[["21", "2025-01-01"], ["22", "2026-05-08"]], chapter_title="修订记录")

    added = next(change for change in compare_tables([old], [new]) if change.change_type == "added")
    assert added.row_key == "22"
    assert added.cell_changes[1]["column"] == "日期"


def test_reports_added_column_in_new_text():
    changes = compare_tables(
        [_table([["设备"], ["防火墙"]])],
        [_table([["设备", "状态"], ["防火墙", "在用"]])],
    )

    added = next(change for change in changes if change.change_type == "added" and change.new_text == "新增列: 状态")
    assert added.old_text == ""
    assert added.new_text == "新增列: 状态"


def test_reports_new_and_old_table_locations_directionally():
    changes = compare_tables(
        [_table([["设备", "型号"], ["防火墙", "PA-850"]], page=53)],
        [_table([["设备", "型号"], ["防火墙", "PA-3260"]], page=50)],
    )

    change = changes[0]
    assert change.change_type == "modified"
    assert change.location == "第50页 / 设备清单"
    assert change.old_location == "第53页 / 设备清单"
