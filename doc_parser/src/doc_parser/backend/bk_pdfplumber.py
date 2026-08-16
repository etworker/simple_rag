"""pdfplumber 后端 — 轻量文本+表格解析（doc_parser 默认后端）。

单次打开 PDF，两遍遍历：
  第一遍：收集每页文字行 + 提取表格 + 获取表格区域坐标 → 统计高频重复行
  第二遍：排除表格区域和重复行，拼接正文流 → 按语义分段 + 反查页码

依赖：pdfplumber（doc_parser 基础依赖）。
"""

import os
import re
from collections import Counter

import pdfplumber

from doc_parser.models import Document, Table


def _words_to_lines(words, y_tolerance=3, margin_number_x=0, margin_number_re=None):
    """
    将 pdfplumber 的 word 对象按 Y 坐标聚合为行
    返回: [(line_top_y, line_text), ...]

    当 margin_number_x > 0 时，启用编号列分离：
    - x0 < margin_number_x 且文本匹配 margin_number_re 的 word → 视为编号
    - 同一行内，输出格式固定为 "编号 正文"（编号在前）
    - 保证 chapter_patterns（^\\d+\\.\\d+ xxx）能稳定匹配
    """
    if not words:
        return []

    # 按 top 排序
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))

    lines = []
    current_line_words = [sorted_words[0]]
    current_top = sorted_words[0]["top"]

    for word in sorted_words[1:]:
        if abs(word["top"] - current_top) <= y_tolerance:
            current_line_words.append(word)
        else:
            # 新行
            line_text = _assemble_line(current_line_words, margin_number_x, margin_number_re)
            lines.append((current_top, line_text))
            current_line_words = [word]
            current_top = word["top"]

    # 最后一行
    if current_line_words:
        line_text = _assemble_line(current_line_words, margin_number_x, margin_number_re)
        lines.append((current_top, line_text))

    return lines


def _assemble_line(line_words, margin_number_x, margin_number_re):
    """
    将同一行的 word 列表拼接为文本。

    如果启用了 margin 编号列（margin_number_x > 0），则分离编号和正文，
    统一输出为 "编号 正文"，消除 PDF 文字流顺序的不确定性。
    """
    sorted_by_x = sorted(line_words, key=lambda w: w["x0"])

    if margin_number_x <= 0 or margin_number_re is None:
        # 未启用，保持原行为：按 x 坐标顺序拼接
        return " ".join(w["text"] for w in sorted_by_x)

    # 分离编号列和正文列
    number_parts = []
    body_parts = []
    for w in sorted_by_x:
        if w["x0"] < margin_number_x and margin_number_re.match(w["text"].strip()):
            number_parts.append(w["text"].strip())
        else:
            body_parts.append(w["text"])

    # 中文 word 间不加空格（PDF 文本层按词拆分，中文连续文本会被拆成
    # "策"、"略" 等多个 word，加空格得到 "策 略"；人眼阅读是 "策略"）。
    from doc_parser._text import join_cjk_lines

    body_text = join_cjk_lines(body_parts)

    if number_parts:
        # 多段编号片段（极少见）用 . 连接；通常只有一个
        number_text = ".".join(number_parts) if len(number_parts) > 1 else number_parts[0]
        return f"{number_text} {body_text}" if body_text else number_text
    else:
        return body_text


def _is_in_table_region(line_top, table_regions):
    """判断某行是否在表格区域内"""
    return any(table_top <= line_top <= table_bottom for table_top, table_bottom in table_regions)


def extract_pdf_with_pdfplumber(filepath, config=None, get_config=None):
    """使用 pdfplumber 解析 PDF，返回 Document 对象。"""
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

    with pdfplumber.open(filepath) as pdf:
        num_pages = len(pdf.pages)
        if num_pages == 0:
            return Document(filename=os.path.basename(filepath), paragraphs=[], tables=[])

        # 编译 margin 编号正则（一次编译，全页复用）
        margin_number_x = cfg.get("margin_number_x", 0)
        margin_number_re = None
        if margin_number_x > 0:
            margin_number_re = re.compile(cfg.get("margin_number_pattern", r"^(?:\d+(?:\.\d+)*|[A-Z])$"))

        # ========== 第一遍：收集行 + 提取表格 + 获取表格区域 ==========
        all_lines_flat = []  # 所有行的平面列表（用于统计重复）
        page_data = []  # [(page_num, page_lines, table_regions)]
        tables = []
        current_chapter = ""
        current_chapter_title = ""

        for page_num, page in enumerate(pdf.pages, 1):
            page_height = page.height

            # 计算页眉/页脚的 Y 坐标边界
            header_y = page_height * (cfg["header_margin_pct"] / 100)
            footer_y = page_height * (1 - cfg["footer_margin_pct"] / 100)

            # 获取文字对象（带坐标）
            words = page.extract_words(keep_blank_chars=False, use_text_flow=True)

            # 按行聚合（按 top 坐标分组，容差由配置控制）
            y_tol = cfg.get("y_tolerance", 3)
            page_lines = _words_to_lines(
                words, y_tolerance=y_tol, margin_number_x=margin_number_x, margin_number_re=margin_number_re
            )

            # 收集有效行用于重复统计
            for line_top, line_text in page_lines:
                if line_top < header_y or line_top > footer_y:
                    continue
                all_lines_flat.append(line_text.strip())

            # 提取表格 + 获取表格区域坐标
            page_tables = page.find_tables()
            table_regions = [(t.bbox[1], t.bbox[3]) for t in page_tables]  # (top, bottom)

            # 从页面文字中更新章节追踪
            text = page.extract_text()
            if text:
                for line in text.split("\n"):
                    ch = detect_chapter(line.strip(), cfg)
                    if ch:
                        current_chapter, current_chapter_title = ch

            # 提取表格数据
            for pt in page_tables:
                table_data = pt.extract()
                if table_data and len(table_data) >= 2:
                    cleaned = clean_table(table_data)
                    if cleaned and len(cleaned) >= 2:
                        tables.append(
                            Table(
                                rows=cleaned,
                                page=page_num,
                                chapter=current_chapter,
                                chapter_title=current_chapter_title,
                                source_file=os.path.basename(filepath),
                                index=len(tables) + 1,
                            )
                        )

            page_data.append((page_num, page_lines, table_regions, header_y, footer_y))

        # ========== 统计高频重复行（自动识别页眉页脚残留）==========
        repeat_threshold = max(3, int(num_pages * cfg["repeat_line_threshold_pct"] / 100))
        line_counts = Counter(all_lines_flat)
        repeated_lines = {line for line, count in line_counts.items() if count >= repeat_threshold and len(line) < 100}

        # ========== 第二遍：构建正文流（排除表格区域 + 重复行）==========
        full_text = ""
        page_boundaries = []  # [(start_offset, end_offset, page_num)]

        for page_num, page_lines, table_regions, header_y, footer_y in page_data:
            start_offset = len(full_text)
            for line_top, line_text in page_lines:
                # 跳过页眉/页脚
                if line_top < header_y or line_top > footer_y:
                    continue
                # 跳过重复行
                if line_text.strip() in repeated_lines:
                    continue
                # 跳过表格区域内的行
                if _is_in_table_region(line_top, table_regions):
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

    # ========== 数字断字后处理（PDF 文本层数字拆 run）==========
    normalize_number_spacing = cfg.get("normalize_number_spacing", True)
    if normalize_number_spacing:
        from doc_parser._text import normalize_number_spacing as _norm_num

        for para in paragraphs:
            para.text = _norm_num(para.text)

    # 为表格分配章节信息
    assign_table_chapters(tables, paragraphs)

    return Document(filename=os.path.basename(filepath), paragraphs=paragraphs, tables=tables)
