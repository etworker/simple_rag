"""PDF 解析分发器（后端路由 + 智能选择）与预扫描。

实际解析由 doc_parser.backend 中的各后端模块完成：
  - bk_pymupdf   ：PyMuPDF 快路径
  - bk_mineru     ：MinerU VLM/OCR
  - bk_docling    ：IBM Docling
本模块保留：
  - 预扫描 _quick_scan_pdf（当前用 pdfplumber 光读取）
  - 智能后端选择 select_backend
  - 统一分发 extract_pdf
  - 向后兼容 re-export（_words_to_lines / _assemble_line / _is_in_table_region）
"""

import re

import pdfplumber
from loguru import logger

from doc_parser.backend.bk_pdfplumber import _assemble_line, _is_in_table_region, _words_to_lines

# ============================================================
# 智能后端选择
# ============================================================


def _sample_page_indices(num_pages: int, sample_pages: int) -> list[int]:
    """返回均匀覆盖全文的预扫描页下标（0-based）。

    ``sample_pages=0`` 表示扫描全部页面；有限采样时始终包含首页和末页，
    避免只看前几页而漏掉文档后部的表格或扫描页。
    """
    if num_pages <= 0:
        return []
    if sample_pages <= 0 or sample_pages >= num_pages:
        return list(range(num_pages))
    if sample_pages == 1:
        return [0]

    indices = {round(i * (num_pages - 1) / (sample_pages - 1)) for i in range(sample_pages)}
    return sorted(indices)


def _quick_scan_pdf(filepath, sample_pages=5, cfg=None):
    """
    快速预扫描 PDF，收集决策所需的统计信息。
    有限采样时均匀覆盖全文（默认 5 页），不是只读取前 N 页；
    ``sample_pages=0`` 扫描全部页面。

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

            page_indices = _sample_page_indices(num_pages, int(sample_pages or 0))
            n = len(page_indices)
            total_text = 0
            total_tables = 0
            large_image_pages = 0
            drawings_no_tables_pages = 0

            for i in page_indices:
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
                large_img_ratio = (cfg or {}).get("scan_large_image_ratio", 0.5)
                for img in images:
                    img_area = img.get("width", 0) * img.get("height", 0)
                    if page_area > 0 and img_area > page_area * large_img_ratio:
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
            drawings_ratio = (cfg or {}).get("scan_drawings_no_tables_ratio", 0.4)
            result["has_drawings_no_tables"] = drawings_no_tables_pages >= n * drawings_ratio

    except Exception as e:
        logger.debug(f"预扫描失败: {e}")

    return result


def _sample_borderless_table_hint(sample: str, table_keywords: list[str]) -> bool:
    """判断单页文本样本是否包含无框线表格的强线索。"""
    normalized = re.sub(r"\s+", " ", sample).strip()
    compact = re.sub(r"\s+", "", sample)
    keyword_hits = sum(1 for kw in table_keywords if kw and kw in normalized)

    # 规则 PDF 常把“序号”拆成“序\n号”，所以同时检查去空白版本。
    has_serial_header = "序号" in compact
    has_table_header_pair = has_serial_header and any(
        kw in normalized for kw in ("名称", "描述", "风险", "措施", "涉及", "责任")
    )
    aligned_lines = bool(re.search(r"\S+\s{3,}\S+\s{3,}\S+", sample))

    # 有些 PDF 文本层只保留单空格，但表头仍呈现为多个短列。
    short_column_lines = sum(1 for line in sample.splitlines() if len(line.split()) >= 3 and len(line.strip()) <= 80)
    return has_table_header_pair or (keyword_hits >= 2 and (aligned_lines or short_column_lines >= 2))


def _detect_borderless_table_hint(scan, cfg=None):
    """
    基于文本样本检测无框线表格的线索。

    判断依据：
    - 文本中出现多个表头关键词；
    - 表头存在“序号 + 风险/名称/涉及”等组合；
    - 短行密集且包含连续空格或制表符（列对齐特征）。

    单个采样页出现强表头组合即可触发，避免短通报中只有一页表格时被
    全文平均值稀释；普通文本则仍需满足原有比例条件。
    """
    table_keywords = (cfg or {}).get(
        "table_keyword_list",
        ["序号", "名称", "描述", "风险", "措施", "类别", "编号", "责任人", "频率", "要求", "备注", "检查项", "标准"],
    )
    samples = scan.get("text_samples", [])
    if any(_sample_borderless_table_hint(sample, table_keywords) for sample in samples):
        return True

    keyword_hits = 0
    aligned_line_hits = 0
    for sample in samples:
        for kw in table_keywords:
            if kw in sample:
                keyword_hits += 1
                break
        if re.search(r"\S+\s{3,}\S+\s{3,}\S+", sample):
            aligned_line_hits += 1

    total = max(1, len(samples))
    keyword_ratio = (cfg or {}).get("table_keyword_hit_ratio", 0.5)
    aligned_ratio = (cfg or {}).get("table_aligned_line_ratio", 0.3)
    return (keyword_hits / total >= keyword_ratio) and (aligned_line_hits / total >= aligned_ratio)


def select_backend(filepath, cfg):
    """
    智能选择解析后端。

    决策流程：
    1. 扫描件检测  → 文本量极低 或 大图片覆盖页面 → MinerU
    2. 无框线表格  → 有绘图对象但规则后端提取不到表格 → Docling（深度学习表格）
    3. 文本表格线索 → 文本中出现表头关键词且列对齐 → Docling
    4. 正常文档    → PyMuPDF（快路径；不可用时降级 pdfplumber）

    所有阈值均从 cfg 读取，可通过配置覆盖。

    返回 (backend_name, reason)
    """
    sample_pages = cfg.get("scan_sample_pages", 5)
    scan = _quick_scan_pdf(filepath, sample_pages=sample_pages, cfg=cfg)

    if scan["num_pages"] == 0:
        return "pdfplumber", "空 PDF"

    text_threshold = cfg.get("scan_text_per_page_threshold", 50)
    image_ratio = cfg.get("scan_large_image_ratio", 0.5)
    low_table_rate = cfg.get("scan_low_table_rate", 0.3)

    # 1. 扫描件：平均每页文字低于阈值
    if scan["avg_text_per_page"] < text_threshold:
        return "mineru", f"疑似扫描件（平均 {scan['avg_text_per_page']:.0f} 字/页）"

    # 2. 大图片覆盖率超阈值，且文本量仍然偏低 → 疑似扫描件
    #    大图本身不是扫描件证据：公文红头/印章/配图也会触发大图检测，
    #    但文本型文档（红头+正文）每页文字量通常充足。
    #    仅当"大图覆盖 + 文本仍少"同时成立才判扫描件，避免公文误走 MinerU。
    image_text_threshold = cfg.get("scan_image_text_per_page_threshold", 300)
    if scan["large_image_ratio"] > image_ratio and scan["avg_text_per_page"] < image_text_threshold:
        return (
            "mineru",
            f"疑似扫描件（大图片覆盖率 {scan['large_image_ratio']:.0%}，文本仅 {scan['avg_text_per_page']:.0f} 字/页）",
        )

    # 3. 有绘图线但规则后端提取不到表格 → 可能无框线表格 → Docling（深度学习表格识别）
    if scan["has_drawings_no_tables"] and scan["avg_tables_per_page"] < low_table_rate:
        return "docling", "检测到绘图对象但规则后端未提取到表格（疑似无框线表格，用 Docling）"

    # 4. 文本中出现表头关键词 + 列对齐特征（疑似无框线表格）→ Docling
    if _detect_borderless_table_hint(scan, cfg) and scan["avg_tables_per_page"] < low_table_rate:
        return "docling", "文本中出现表格表头或列对齐特征（疑似无框线表格，用 Docling）"

    # 5. 表格正常 → 数字文本快路径 PyMuPDF（秒级；相比 pdfplumber 快 10-50 倍）
    if scan["avg_tables_per_page"] >= 1:
        return "pymupdf", f"数字文本 + 表格提取正常（平均 {scan['avg_tables_per_page']:.1f} 表/页），走快路径"

    return "pymupdf", "文档特征正常，走 PyMuPDF 快路径"


# ============================================================
# 统一分发
# ============================================================


def extract_pdf(filepath, config=None, get_config=None):
    """
    通用 PDF 解析（后端分发）。

    后端选择：
    config["extract"]["backend"] = "mineru"     → 使用 MinerU VLM/OCR 引擎
    config["extract"]["backend"] = "docling"    → 使用 Docling TableFormer
    config["extract"]["backend"] = "pdfplumber" → 强制使用 pdfplumber
    config["extract"]["backend"] = "auto"        → 智能选择（默认）
    """
    if get_config is None:
        from doc_parser.parser import get_extract_config

        get_config = get_extract_config
    cfg = get_config(config)

    # 后端选择（默认 auto）
    backend = cfg.get("backend", "auto")
    if backend == "mineru":
        from doc_parser.backend import load_backend

        return load_backend("mineru").extract_pdf_with_mineru(filepath, config)
    elif backend in ("docling", "docling_cpu"):
        from doc_parser.backend import load_backend

        return load_backend("docling").extract_pdf_with_docling(filepath, config)
    elif backend in ("pymupdf", "pymupdf_cpu"):
        from doc_parser.backend import load_backend

        return load_backend("pymupdf").extract_pdf_with_pymupdf(filepath, config)
    elif backend in ("pdfplumber", "pdfplumber_cpu"):
        from doc_parser.backend import load_backend

        return load_backend("pdfplumber").extract_pdf_with_pdfplumber(filepath, config)
    elif backend == "auto":
        chosen, reason = select_backend(filepath, cfg)
        if chosen == "mineru":
            try:
                from doc_parser.backend import load_backend

                logger.info(f"自动选择 MinerU 后端：{reason}")
                return load_backend("mineru").extract_pdf_with_mineru(filepath, config)
            except Exception as e:
                # MinerU 不可用，降级到 pdfplumber
                logger.warning(f"{reason}，但 MinerU 不可用（{e}），降级到 pdfplumber")
        elif chosen == "docling":
            try:
                from doc_parser.backend import load_backend

                logger.info(f"自动选择 Docling 后端：{reason}")
                return load_backend("docling").extract_pdf_with_docling(filepath, config)
            except Exception as e:
                # Docling 不可用，降级到 pdfplumber
                logger.warning(f"{reason}，但 Docling 不可用（{e}），降级到 pdfplumber")
        elif chosen == "pymupdf":
            try:
                from doc_parser.backend import load_backend

                logger.info(f"自动选择 PyMuPDF 后端：{reason}")
                return load_backend("pymupdf").extract_pdf_with_pymupdf(filepath, config)
            except Exception as e:
                # PyMuPDF 不可用，降级到 pdfplumber
                logger.warning(f"{reason}，但 PyMuPDF 不可用（{e}），降级到 pdfplumber")
        else:
            if reason:
                logger.info(f"自动选择 pdfplumber 后端：{reason}")
    else:
        logger.warning(f"未知后端 {backend!r}，使用 pdfplumber")

    from doc_parser.backend import load_backend

    return load_backend("pdfplumber").extract_pdf_with_pdfplumber(filepath, config)


__all__ = [
    "_assemble_line",
    "_detect_borderless_table_hint",
    "_is_in_table_region",
    "_quick_scan_pdf",
    "_words_to_lines",
    "extract_pdf",
    "select_backend",
]
