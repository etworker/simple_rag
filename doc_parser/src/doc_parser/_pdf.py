"""PDF 解析实现（pdfplumber 后端 + 智能后端选择）"""

import os
import re
from collections import Counter

import pdfplumber
from loguru import logger

from doc_parser._tables import (
    assign_table_chapters,
    clean_table,
    filter_template_tables,
    merge_cross_page_tables,
)
from doc_parser._text import detect_chapter, segment_and_locate
from doc_parser.models import Document, Table

# ============================================================
# 智能后端选择
# ============================================================


def _quick_scan_pdf(filepath, sample_pages=5):
    """
    快速预扫描 PDF，收集决策所需的统计信息。
    只读取前 N 页（默认 5 页），耗时通常 < 0.5s。

    返回 dict:
      num_pages, sampled, avg_text_per_page, avg_tables_per_page,
      large_image_ratio, has_drawings_no_tables, text_samples
    """
    result = {
        "num_pages": 0,
        "sampled": 0,
        "avg_text_per_page": 0,
        "avg_tables_per_page": 0,
        "large_image_ratio": 0.0,
        "has_drawings_no_tables": False,
        "text_samples": [],
    }

    try:
        with pdfplumber.open(filepath) as pdf:
            num_pages = len(pdf.pages)
            result["num_pages"] = num_pages
            if num_pages == 0:
                return result

            n = min(sample_pages, num_pages)
            total_text = 0
            total_tables = 0
            large_image_pages = 0
            drawings_no_tables_pages = 0

            for i in range(n):
                page = pdf.pages[i]
                text = page.extract_text() or ""
                tables = page.extract_tables()
                images = getattr(page, "images", [])
                # rects/lines/curves → 绘图对象
                rects = getattr(page, "rects", [])
                lines = getattr(page, "lines", [])
                curves = getattr(page, "curves", [])

                total_text += len(text)
                total_tables += len(tables)

                # 截取前 200 字符做样本
                if text:
                    result["text_samples"].append(text[:200])

                # 检测大图片（扫描件特征）
                page_area = page.width * page.height
                for img in images:
                    img_area = img.get("width", 0) * img.get("height", 0)
                    if page_area > 0 and img_area > page_area * 0.5:
                        large_image_pages += 1
                        break

                # 检测"有绘图线但无表格"（可能无框线表格被 pdfplumber 漏掉）
                has_drawings = len(rects) + len(lines) + len(curves) > 0
                if has_drawings and len(tables) == 0:
                    drawings_no_tables_pages += 1

            result["sampled"] = n
            result["avg_text_per_page"] = total_text / n
            result["avg_tables_per_page"] = total_tables / n
            result["large_image_ratio"] = large_image_pages / n
            result["has_drawings_no_tables"] = drawings_no_tables_pages > n * 0.4

    except Exception as e:
        logger.debug(f"预扫描失败: {e}")

    return result


def _detect_borderless_table_hint(scan):
    """
    基于文本样本检测无框线表格的线索。

    判断依据：
    - 文本中出现表头关键词（序号、名称、描述/风险/措施…）
    - 短行密集且包含连续空格或制表符（列对齐特征）
    """
    table_keywords = [
        "序号",
        "名称",
        "描述",
        "风险",
        "措施",
        "类别",
        "编号",
        "责任人",
        "频率",
        "要求",
        "备注",
        "检查项",
        "标准",
    ]
    keyword_hits = 0
    aligned_line_hits = 0

    for sample in scan.get("text_samples", []):
        # 关键词命中
        for kw in table_keywords:
            if kw in sample:
                keyword_hits += 1
                break
        # 连续空格/制表符 → 列对齐
        if re.search(r"\S+\s{3,}\S+\s{3,}\S+", sample):
            aligned_line_hits += 1

    total = max(1, len(scan.get("text_samples", [])))
    return (keyword_hits / total >= 0.5) and (aligned_line_hits / total >= 0.3)


def select_backend(filepath, cfg):
    """
    智能选择解析后端。

    决策流程：
    1. 扫描件检测  → 文本量极低 或 大图片覆盖页面 → MinerU
    2. 无框线表格  → 有绘图对象但 pdfplumber 提取不到表格 → MinerU
    3. 文本表格线索 → 文本中出现表头关键词且列对齐 → MinerU
    4. 正常文档    → pdfplumber

    返回 (backend_name, reason)
    """
    scan = _quick_scan_pdf(filepath)

    if scan["num_pages"] == 0:
        return "pdfplumber", "空 PDF"

    # 1. 扫描件：平均每页文字 < 50 字符
    if scan["avg_text_per_page"] < 50:
        return "mineru", f"疑似扫描件（平均 {scan['avg_text_per_page']:.0f} 字/页）"

    # 2. 大图片覆盖 > 50% 采样页
    if scan["large_image_ratio"] > 0.5:
        return "mineru", f"疑似扫描件（大图片覆盖率 {scan['large_image_ratio']:.0%}）"

    # 3. 有绘图线但 pdfplumber 提取不到表格 → 可能无框线表格
    if scan["has_drawings_no_tables"] and scan["avg_tables_per_page"] < 0.3:
        return "mineru", "检测到绘图对象但 pdfplumber 未提取到表格（疑似无框线表格）"

    # 4. 文本中出现表头关键词 + 列对齐特征
    if _detect_borderless_table_hint(scan) and scan["avg_tables_per_page"] < 0.3:
        return "mineru", "文本中出现表格关键词及列对齐特征（疑似无框线表格）"

    # 5. pdfplumber 足以应对
    if scan["avg_tables_per_page"] >= 1:
        return "pdfplumber", f"表格提取正常（平均 {scan['avg_tables_per_page']:.1f} 表/页）"

    return "pdfplumber", "文档特征正常"


# ============================================================
# PDF 解析（pdfplumber）
# ============================================================


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

    body_text = " ".join(body_parts)

    if number_parts:
        # 多段编号片段（极少见）用 . 连接；通常只有一个
        number_text = ".".join(number_parts) if len(number_parts) > 1 else number_parts[0]
        return f"{number_text} {body_text}" if body_text else number_text
    else:
        return body_text


def _is_in_table_region(line_top, table_regions):
    """判断某行是否在表格区域内"""
    return any(table_top <= line_top <= table_bottom for table_top, table_bottom in table_regions)


def extract_pdf(filepath, config=None, get_config=None):
    """
    通用 PDF 解析

    策略（单次打开 PDF，两遍遍历）：
    第一遍：收集每页文字行 + 提取表格 + 获取表格区域坐标
    → 统计高频重复行
    第二遍：排除表格区域和重复行，拼接正文流
    → 在正文流上按语义分段 + 反查页码

    后端选择：
    config["extract"]["backend"] = "mineru" → 使用 MinerU VLM/OCR 引擎
    config["extract"]["backend"] = "auto"   → 智能选择（预扫描后决定）
    默认使用 pdfplumber
    """
    if get_config is None:
        from doc_parser.parser import get_extract_config

        get_config = get_extract_config
    cfg = get_config(config)

    # 后端选择
    backend = cfg.get("backend", "pdfplumber")
    if backend == "mineru":
        from doc_parser.mineru_backend import extract_pdf_with_mineru

        return extract_pdf_with_mineru(filepath, config)
    elif backend == "auto":
        chosen, reason = select_backend(filepath, cfg)
        if chosen == "mineru":
            try:
                from doc_parser.mineru_backend import extract_pdf_with_mineru

                logger.info(f"自动选择 MinerU 后端：{reason}")
                return extract_pdf_with_mineru(filepath, config)
            except RuntimeError:
                # MinerU 未安装，降级到 pdfplumber
                logger.warning(f"{reason}，但 MinerU 未安装，降级到 pdfplumber")
        else:
            if reason:
                logger.info(f"自动选择 pdfplumber 后端：{reason}")

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

            # 按行聚合（按 top 坐标分组，容差 3pt）
            page_lines = _words_to_lines(
                words, y_tolerance=3, margin_number_x=margin_number_x, margin_number_re=margin_number_re
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

    # 为表格分配章节信息
    assign_table_chapters(tables, paragraphs)

    return Document(filename=os.path.basename(filepath), paragraphs=paragraphs, tables=tables)
