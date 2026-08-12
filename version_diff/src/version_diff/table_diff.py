"""表格版本比对：表格配对、列对齐与行级差异计算。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from version_diff.models import VersionChange


def _normalize_cell(text: str) -> str:
    return re.sub(r"\s+", "", str(text).strip())


def _display_cell(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip())


def _header_text(table) -> str:
    return " ".join(_normalize_cell(cell) for cell in table.rows[0]) if table.rows else ""


def _row_key(row, column: int) -> str:
    return _normalize_cell(row[column]) if len(row) > column else ""


def _aligned_row_text(row, column_map: dict, is_old: bool) -> str:
    old_columns = sorted(column_map)
    if is_old:
        return " | ".join(_normalize_cell(row[index]) for index in old_columns if index < len(row))
    return " | ".join(_normalize_cell(row[column_map[index]]) for index in old_columns if column_map[index] < len(row))


def compare_table_pair(old_table, new_table) -> list[VersionChange]:
    """比较已配对表格，返回列、行和单元格变更。"""
    if not old_table.rows or not new_table.rows:
        return []
    changes: list[VersionChange] = []
    section = old_table.chapter_title or old_table.location
    old_header = [str(cell).strip() for cell in old_table.rows[0]]
    new_header = [str(cell).strip() for cell in new_table.rows[0]]
    column_map, used_new_columns = {}, set()
    new_header_valid = sum(bool(_normalize_cell(cell)) for cell in new_header) >= len(new_header) * 0.5
    if new_header_valid and old_header:
        for old_index, old_name in enumerate(old_header):
            best_index, best_score = -1, 0.0
            for new_index, new_name in enumerate(new_header):
                if new_index in used_new_columns:
                    continue
                score = SequenceMatcher(None, _normalize_cell(old_name), _normalize_cell(new_name)).ratio()
                if score >= 0.5 and score > best_score:
                    best_index, best_score = new_index, score
            if best_index >= 0:
                column_map[old_index] = best_index
                used_new_columns.add(best_index)
    else:
        for index in range(min(len(old_header), len(new_header))):
            column_map[index] = index
            used_new_columns.add(index)

    added_columns = [new_header[index] for index in range(len(new_header)) if index not in used_new_columns]
    removed_columns = [old_header[index] for index in range(len(old_header)) if index not in column_map]
    if added_columns:
        changes.append(
            VersionChange(
                change_type="added", section=f"表格: {section}", location="表格结构",
                new_text=f"新增列: {', '.join(added_columns)}", summary=f"表格新增 {len(added_columns)} 列",
            )
        )
    if removed_columns:
        changes.append(
            VersionChange(
                change_type="removed", section=f"表格: {section}", location="表格结构",
                old_text=f"删除列: {', '.join(removed_columns)}", summary=f"表格删除 {len(removed_columns)} 列",
            )
        )

    old_rows = {_row_key(row, 0): row for row in old_table.rows[1:]}
    new_rows = {_row_key(row, column_map.get(0, 0)): row for row in new_table.rows[1:]}
    for key in dict.fromkeys([*old_rows, *new_rows]):
        if not key:
            continue
        old_row, new_row = old_rows.get(key), new_rows.get(key)
        if old_row and new_row:
            old_text, new_text = _aligned_row_text(old_row, column_map, True), _aligned_row_text(new_row, column_map, False)
            if old_text != new_text:
                changes.append(
                    VersionChange(
                        change_type="modified", section=f"表格: {section}", location=f"行: {key}",
                        old_text=" | ".join(map(_display_cell, old_row)),
                        new_text=" | ".join(map(_display_cell, new_row)),
                        similarity=SequenceMatcher(None, old_text, new_text).ratio(),
                    )
                )
        elif old_row:
            changes.append(
                VersionChange(
                    change_type="removed", section=f"表格: {section}", location=f"行: {key}",
                    old_text=" | ".join(map(_display_cell, old_row)),
                )
            )
        elif new_row:
            changes.append(
                VersionChange(
                    change_type="added", section=f"表格: {section}", location=f"行: {key}",
                    new_text=" | ".join(map(_display_cell, new_row)),
                )
            )
    return changes


def compare_tables(old_tables: list, new_tables: list) -> list[VersionChange]:
    """按表头配对表格，必要时以规模相近的大表格作保守回退。"""
    paired_old, paired_new, pairs = set(), set(), []
    for old_index, old_table in enumerate(old_tables):
        best_index, best_score = -1, 0.0
        for new_index, new_table in enumerate(new_tables):
            if new_index in paired_new:
                continue
            score = SequenceMatcher(None, _header_text(old_table), _header_text(new_table)).ratio()
            if score >= 0.5 and score > best_score:
                best_index, best_score = new_index, score
        if best_index >= 0:
            pairs.append((old_index, best_index)); paired_old.add(old_index); paired_new.add(best_index)
    for old_index, old_table in enumerate(old_tables):
        if old_index in paired_old or len(old_table.rows or []) < 5:
            continue
        for new_index, new_table in enumerate(new_tables):
            if new_index in paired_new or len(new_table.rows or []) < 5:
                continue
            if len(old_table.rows[0]) == len(new_table.rows[0]) and min(len(old_table.rows), len(new_table.rows)) / max(len(old_table.rows), len(new_table.rows)) >= 0.7:
                pairs.append((old_index, new_index)); paired_old.add(old_index); paired_new.add(new_index); break
    return [change for old_index, new_index in pairs for change in compare_table_pair(old_tables[old_index], new_tables[new_index])]
