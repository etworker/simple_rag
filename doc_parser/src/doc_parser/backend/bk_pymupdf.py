"""PyMuPDF 后端 — 极速文本+规则表格解析（快路径）。

PyMuPDF (fitz) 是 C 实现的 PDF 渲染/提取库，文本层提取速度比纯 Python 的
pdfplumber 快 10-50 倍（约 0.01-0.1s/页）。本后端针对"数字文本 PDF"优化：
  第一遍：每页 get_text("dict") 提取带坐标的行 + find_tables() 提取框线表格
  第二遍：排除表格区域 / 页眉页脚 / 高频重复行，拼接正文流 → 语义分段

表格识别使用 PyMuPDF 内置 find_tables()（线段检测，支持有框线表格），
无框线表格请使用 docling（深度学习 TableFormer）或 mineru 后端。

依赖：pymupdf>=1.24（doc_parser 基础依赖）。
选择此后端:
    parse("file.pdf", config={"extract": {"backend": "pymupdf"}})

与 bk_pdfplumber 共享全部后处理：
  _merge_cross_page_tables / _filter_template_tables / _assign_table_chapters
  / detect_chapter / segment_and_locate / normalize_number_spacing
"""

import os
import re
from collections import Counter

import fitz  # PyMuPDF

from doc_parser.models import Document, Table


def _extract_lines(page, cfg):
    """从 PyMuPDF 页提取 (top, bottom, text) 行列表。

    用 get_text("dict") 的 lines（自带 bbox 与 spans），
    支持 margin_number_x 编号列分离（spans x0 < 阈值且匹配编号正则 → 拼到行首）。
    """
    margin_number_x = cfg.get("margin_number_x", 0)
    margin_number_re = None
    if margin_number_x > 0:
        margin_number_re = re.compile(cfg.get("margin_number_pattern", r"^(?:\d+(?:\.\d+)*|[A-Z])$"))

    # 用 words 按 y 坐标聚合为行（与 bk_pdfplumber 的 words→lines 逻辑一致），
    # 因为 PyMuPDF 的 get_text("dict") lines 不会把左侧 margin 编号列（x<130）
    # 与正文列（x≈150）的同一视觉行合并（y 差 <1pt 也判为两行），导致
    # "1.1.1 标题" 被拆成 "1.1.1" 与 "标题" 两行、章节识别丢失。
    # words 元组: (x0, y0, x1, y1, word, block_no, line_no, word_no)
    y_tol = cfg.get("y_tolerance", 3)
    words = page.get_text("words")
    if not words:
        return []

    out = []  # (top, bottom, text)
    sorted_words = sorted(words, key=lambda w: (w[1], w[0]))
    current = [sorted_words[0]]
    cur_top = sorted_words[0][1]

    def _assemble(word_list):
        """按 x 坐标排序拼接；margin_number_x > 0 时分离编号列拼到行首。"""
        from doc_parser._text import join_cjk_lines

        s_by_x = sorted(word_list, key=lambda w: w[0])
        if margin_number_re is None:
            text = join_cjk_lines([w[4] for w in s_by_x]).strip()
            return text
        number_parts, body_parts = [], []
        for w in s_by_x:
            if w[0] < margin_number_x and margin_number_re.match(w[4].strip()):
                number_parts.append(w[4].strip())
            else:
                body_parts.append(w[4])
        body_text = join_cjk_lines(body_parts).strip()
        if number_parts:
            number_text = ".".join(number_parts) if len(number_parts) > 1 else number_parts[0]
            return f"{number_text} {body_text}" if body_text else number_text
        return body_text

    for w in sorted_words[1:]:
        if abs(w[1] - cur_top) <= y_tol:
            current.append(w)
        else:
            text = _assemble(current)
            if text:
                out.append((cur_top, max(w_[3] for w_ in current), text))
            current = [w]
            cur_top = w[1]
    if current:
        text = _assemble(current)
        if text:
            out.append((cur_top, max(w_[3] for w_ in current), text))
    return out


def _is_in_table_region(top, table_regions):
    """判断某行是否在表格区域内（y 方向）"""
    return any(t_top <= top <= t_bottom for t_top, t_bottom in table_regions)


def extract_pdf_with_pymupdf(filepath, config=None, get_config=None):
    """使用 PyMuPDF 解析 PDF，返回 Document 对象。"""
    from doc_parser._tables import (
        assign_table_chapters,
        clean_table,
        filter_template_tables,
        merge_cross_page_tables,
    )
    from doc_parser._text import detect_chapter, segment_and_locate

    if get_config is None:
        from doc_parser.parser import get_extract_config

        get_config = get_extract_config
    cfg = get_config(config)

    doc = fitz.open(filepath)
    try:
        num_pages = doc.page_count
        if num_pages == 0:
            return Document(filename=os.path.basename(filepath), paragraphs=[], tables=[])

        # ========== 第一遍：收集行 + 提取表格 + 获取表格区域 ==========
        all_lines_flat = []  # 所有行的平面列表（用于统计重复）
        page_data = []  # [(page_num, page_lines, table_regions, header_y, footer_y)]
        tables = []
        current_chapter = ""
        current_chapter_title = ""

        y_tol = cfg.get("y_tolerance", 3)
        for page_num in range(num_pages):
            page = doc.load_page(page_num)
            page_height = page.rect.height

            header_y = page_height * (cfg["header_margin_pct"] / 100)
            footer_y = page_height * (1 - cfg["footer_margin_pct"] / 100)

            page_lines = _extract_lines(page, cfg)

            for top, _bottom, line_text in page_lines:
                # +y_tol 容差覆盖恰好落在 header_y 边界下方的页眉（top=68 vs 67.36）
                if top < header_y + y_tol or top > footer_y - y_tol:
                    all_lines_flat.append(line_text)

            # find_tables()：PyMuPDF 内置线段检测表格（框线表格）
            page_tables = []
            table_regions = []
            try:
                for tf in page.find_tables():
                    bbox = tf.bbox  # (x0, y0, x1, y1)
                    if bbox:
                        table_regions.append((bbox[1], bbox[3]))
                    extracted = tf.extract()
                    if extracted:
                        page_tables.append(extracted)
            except Exception:
                page_tables, table_regions = [], []

            # 章节追踪（与 pdfplumber 后端一致：逐行检测）
            for _top, _bottom, line_text in page_lines:
                ch = detect_chapter(line_text, cfg)
                if ch:
                    current_chapter, current_chapter_title = ch

            # 提取表格数据
            for table_data in page_tables:
                if table_data and len(table_data) >= 2:
                    cleaned = clean_table(table_data)
                    if cleaned and len(cleaned) >= 2:
                        tables.append(
                            Table(
                                rows=cleaned,
                                page=page_num + 1,
                                chapter=current_chapter,
                                chapter_title=current_chapter_title,
                                source_file=os.path.basename(filepath),
                                index=len(tables) + 1,
                            )
                        )

            page_data.append((page_num + 1, page_lines, table_regions, header_y, footer_y))

        # ========== 统计高频重复行（自动识别页眉页脚残留）==========
        repeat_threshold = max(3, int(num_pages * cfg["repeat_line_threshold_pct"] / 100))
        line_counts = Counter(all_lines_flat)
        repeated_lines = {line for line, count in line_counts.items() if count >= repeat_threshold and len(line) < 100}

        # ========== 第二遍：构建正文流（排除表格区域 + 重复行）==========
        full_text = ""
        page_boundaries = []  # [(start_offset, end_offset, page_num)]

        for page_num, page_lines, table_regions, header_y, footer_y in page_data:
            start_offset = len(full_text)
            for top, _bottom, line_text in page_lines:
                if top < header_y + y_tol or top > footer_y - y_tol:
                    continue
                if line_text in repeated_lines:
                    continue
                if _is_in_table_region(top, table_regions):
                    continue
                full_text += line_text + "\n"
            end_offset = len(full_text)
            if end_offset > start_offset:
                page_boundaries.append((start_offset, end_offset, page_num))

        # ========== 跨页表格合并 (启发式) ==========
        tables = merge_cross_page_tables(tables)

        # ========== 空白模板表格过滤 ==========
        tables = filter_template_tables(tables, cfg)

        # ========== 流式分段 + 章节标记 + 反查页码 ==========
        paragraphs = segment_and_locate(full_text, page_boundaries, filepath, cfg)

        # ========== 数字断字后处理 ==========
        if cfg.get("normalize_number_spacing", True):
            from doc_parser._text import normalize_number_spacing as _norm_num

            for para in paragraphs:
                para.text = _norm_num(para.text)

        # 为表格分配章节信息
        assign_table_chapters(tables, paragraphs)

        return Document(filename=os.path.basename(filepath), paragraphs=paragraphs, tables=tables)
    finally:
        doc.close()
