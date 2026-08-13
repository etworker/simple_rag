"""表格处理（PDF / DOCX 共享）：清洗 / 过滤 / 跨页合并 / 章节分配"""


def clean_table(table_data):
    """清理表格数据"""
    cleaned = [row for row in table_data if any(cell and cell.strip() for cell in row)]
    if not cleaned:
        return []
    cleaned = [[cell.strip() if cell else "" for cell in row] for row in cleaned]
    num_cols = len(cleaned[0])
    non_empty_cols = [c for c in range(num_cols) if any(row[c] if c < len(row) else "" for row in cleaned)]
    if non_empty_cols:
        cleaned = [[row[c] if c < len(row) else "" for c in non_empty_cols] for row in cleaned]
    return cleaned


def filter_template_tables(tables, cfg):
    """
    过滤空白模板表格（签到表/申请表等无信息价值的空表格）。

    判定条件：空单元格率超过 table_empty_cell_threshold。
    空单元格包括：None、纯空白、以及 table_empty_placeholders 中定义的占位符。
    """
    threshold = cfg.get("table_empty_cell_threshold", 1.0)
    if threshold >= 1.0:
        return tables  # 功能禁用

    placeholders = set(cfg.get("table_empty_placeholders", []))

    result = []
    for table in tables:
        total_cells = sum(len(row) for row in table.rows)
        if total_cells == 0:
            continue
        empty_count = 0
        for row in table.rows:
            for cell in row:
                if not cell or not cell.strip() or cell.strip() in placeholders:
                    empty_count += 1
        if empty_count / total_cells < threshold:
            result.append(table)
    # 重新标号
    for idx, t in enumerate(result, 1):
        t.index = idx
    return result


def assign_table_chapters(tables, paragraphs):
    """为表格分配最近的章节信息"""
    if not paragraphs:
        return
    for table in tables:
        if table.chapter:
            continue
        best = None
        for p in paragraphs:
            if p.page <= table.page and p.chapter:
                best = p
        if best:
            table.chapter = best.chapter
            table.chapter_title = best.chapter_title


def _row_token_jaccard(row_a, row_b) -> float:
    """两行之间的 Token Jaccard 相似度。"""
    tokens_a = {str(c).strip() for c in row_a if c and str(c).strip()}
    tokens_b = {str(c).strip() for c in row_b if c and str(c).strip()}
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _should_merge_tables(a, b, header_threshold: float = 0.85) -> bool:
    """判断两张表格是否应为同一逻辑表格的跨页片段。

    合并判定: 同文件 + 页码连续 + 列数兼容 (差异 ≤ 1)。

    表头相似度不作为合并前置条件——续页可能不重复表头。
    表头相似度仅在 ``_append_rows_skip_dup_header`` 阶段用于决定
    是否跳过续页首行（若与表头高度相似则视为重复表头跳过）。
    """
    # 1. 同文件
    if a.source_file != b.source_file:
        return False

    # 2. 页码连续
    a_end = a.page_end or a.page
    if b.page != a_end + 1:
        return False

    # 3. 列数接近
    a_rows = a.rows or []
    b_rows = b.rows or []
    if not a_rows or not b_rows:
        return False
    a_cols = len(a_rows[0]) if a_rows[0] else 0
    b_cols = len(b_rows[0]) if b_rows[0] else 0
    if abs(a_cols - b_cols) > 1:
        return False
    # 表头相似度不作为合并条件；仅用于 _append 阶段跳过重复表头
    _ = header_threshold  # 保留参数签名兼容外部调用
    return True


def _append_rows_skip_dup_header(target, source, header_threshold: float = 0.85) -> list:
    """追加 source 的行到 target 末尾，跳过重复的表头行（如果有）。"""
    result = list(target.rows or [])
    source_rows = list(source.rows or [])
    if not source_rows:
        return result

    target_header = result[0] if result else None
    target_cols = len(target_header) if target_header else len(source_rows[0])

    for idx, row in enumerate(source_rows):
        # 续页第 0 行若与表头相似 → 跳过（重复表头）
        if idx == 0 and target_header and _row_token_jaccard(target_header, row) >= header_threshold:
            continue
        # 列数对齐: 短的补空, 长的截断
        if len(row) < target_cols:
            row = list(row) + [""] * (target_cols - len(row))
        elif len(row) > target_cols:
            row = row[:target_cols]
        result.append(row)
    return result


def merge_cross_page_tables(tables: list) -> list:
    """
    启发式合并跨页连续表格。

    合并条件（同时满足）:
    1. 同一 source_file
    2. 页码连续 (后续表格的 page == 前一表格的 page/page_end + 1)
    3. 列数接近 (差异 ≤ 1)
    续页的表头相似度仅用于 _append 阶段决定是否跳过重复行，不影响是否合并。

    合并后:
    - 保留首张表格的 headers / page / chapter
    - 续页的数据行追加到首张的 rows 末尾（跳过重复表头行）
    - 更新 page_end = 末页页码
    """
    if len(tables) <= 1:
        return tables

    merged: list = []
    i = 0
    while i < len(tables):
        cur = tables[i]
        j = i + 1
        while j < len(tables):
            nxt = tables[j]
            if not _should_merge_tables(cur, nxt):
                break
            # 执行合并: cur.rows += nxt 的非表头行
            new_rows = _append_rows_skip_dup_header(cur, nxt)
            cur.rows = new_rows
            # 更新页码范围
            cur.page_end = getattr(nxt, "page_end", None) or nxt.page
            # 继承章节（取第一个有的）
            if not cur.chapter and getattr(nxt, "chapter", None):
                cur.chapter = nxt.chapter
                cur.chapter_title = nxt.chapter_title
            j += 1
        merged.append(cur)
        i = max(i + 1, j)

    # 重新标号
    for idx, t in enumerate(merged, 1):
        t.index = idx
    return merged
