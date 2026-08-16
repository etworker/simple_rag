"""
Docling 后端 — IBM Docling 2.x（TableFormer 深度学习表格识别）。

在复杂排版（无框线表格、多栏、扫描件）场景下表现好；
对跨页表格不重复表头（分页处插分隔线），章节标题层级识别准确。

使用前提：
    uv pip install "docling[pdf]"

选择此后端:
    parse("file.pdf", config={"extract": {"backend": "docling"}})

配置项:
    config = {
        "extract": {
            "backend": "docling",
            "docling_start_page": 1,   # 起始页（1-based，None=全部）
            "docling_end_page": None,  # 结束页（含，None=全部）
            "docling_ocr": False,      # 文本型 PDF 无需 OCR
            "docling_device": "auto",  # auto/cpu/cuda/mps（auto=自动探测，有 CUDA 即用 GPU）
        }
    }

说明:
  - 无 MSVC 环境必须禁用 torch.compile（环境变量 TORCH_COMPILE_DISABLE=1，
    否则布局模型初始化失败）。
  - 中文 Windows 下 HF 缓存 .files 读取默认 GBK 会崩，需强制 UTF-8。
    本模块在 import 时已设置这两个环境变量。
  - docling 数字会带空格（"28. 55 万小时"），复用 doc_parser 的
    normalize_number_spacing 后处理修复。
"""

import os
import sys

# 无 MSVC 环境必须禁用 torch.compile（否则布局模型初始化失败）
os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
# 中文 Windows 下文件系统默认 GBK，HF 缓存 .files 解码会崩 → 强制 UTF-8 模式
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from doc_parser.models import Document, Paragraph, Table


def _page_no_of(item) -> int:
    """取元素所在页码（docling prov 的 page_no，1-based）。"""
    prov = getattr(item, "prov", None)
    if prov:
        return int(prov[0].page_no)
    return 0


def _row_cells(df_rows: list) -> list[list[str]]:
    """把 docling 表格的 DataFrame 行转成 [[str]]，去掉 pandas NaN。"""
    import math

    out = []
    for row in df_rows:
        cells = []
        for c in row:
            if c is None:
                cells.append("")
                continue
            if isinstance(c, float) and math.isnan(c):
                cells.append("")
            else:
                cells.append(str(c).strip())
        out.append(cells)
    return out


def extract_pdf_with_docling(filepath: str, config: dict | None = None):
    """使用 Docling 解析 PDF，返回 Document 对象。"""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
    )

    from doc_parser.parser import get_extract_config

    cfg = get_extract_config(config)
    min_len = cfg.get("min_paragraph_length", 10)

    # 构建 Docling 选项
    opts = PdfPipelineOptions()
    opts.do_ocr = cfg.get("docling_ocr", False)
    opts.do_table_structure = True  # TableFormer 表格结构
    device_cfg = str(cfg.get("docling_device", "auto")).lower()
    device = {
        "auto": AcceleratorDevice.AUTO,
        "cpu": AcceleratorDevice.CPU,
        "cuda": AcceleratorDevice.CUDA,
        "mps": AcceleratorDevice.MPS,
    }.get(device_cfg, AcceleratorDevice.AUTO)
    opts.accelerator_options = AcceleratorOptions(device=device)

    # 推理 batch：默认 4；T4/较大显存可调大（layout/table/ocr 多页一起推理，
    # 提升 GPU 利用率，减少逐页小 batch 的算力浪费）。0=docling 默认（4）。
    batch_size = int(cfg.get("docling_batch_size", 0) or 0)
    if batch_size > 0:
        opts.layout_batch_size = batch_size
        opts.table_batch_size = batch_size
        opts.ocr_batch_size = batch_size

    fmt = PdfFormatOption(pipeline_options=opts)
    conv = DocumentConverter(format_options={fmt.backend: fmt})

    # 页数范围（docling 的 page_range 是 1-based 含端点）
    start = cfg.get("docling_start_page", None)
    end = cfg.get("docling_end_page", None)
    if start is not None or end is not None:
        page_range = (start or 1, end or 10**9)
    else:
        page_range = (1, 10**9)

    res = conv.convert(filepath, page_range=page_range)
    if res is None or getattr(res, "status", None) and res.status.value != "success":
        raise RuntimeError(f"Docling 转换失败: {getattr(res, 'errors', 'status unknown')}")

    docling_doc = res.document
    filename = os.path.basename(filepath)

    paragraphs: list[Paragraph] = []
    tables: list[Table] = []
    current_chapter = ""
    current_chapter_title = ""
    order = 0

    for item, _level in docling_doc.iterate_items():
        label = type(item).__name__
        page = _page_no_of(item)
        order += 1

        if label == "TableItem":
            try:
                df = item.export_to_dataframe()
            except Exception:
                continue
            # DataFrame 转行；列为表头后的数据
            header = list(df.columns) if df.columns is not None else []
            rows = _row_cells(df.to_numpy().tolist())
            if not rows:
                continue
            # 若首行与表头重复则去重
            if header and rows and all(str(c).strip() == str(h).strip() for c, h in zip(rows[0], header)):
                rows = rows[1:]
            if not rows:
                continue
            tables.append(
                Table(
                    rows=rows,
                    headers=[str(h).strip() for h in header],
                    page=page,
                    chapter=current_chapter,
                    chapter_title=current_chapter_title,
                    source_file=filename,
                    index=len(tables) + 1,
                    order=order,
                )
            )
            continue

        # SectionHeaderItem → 章节标题
        text = getattr(item, "text", "").strip()
        if not text:
            continue

        from doc_parser._text import detect_chapter

        ch = detect_chapter(text, cfg)
        if ch:
            current_chapter, current_chapter_title = ch

        if label == "SectionHeaderItem":
            # 标题段也作为一个短段落保留（供 to_markdown 识别章节标题）
            paragraphs.append(
                Paragraph(
                    text=text,
                    page=page,
                    chapter=current_chapter,
                    chapter_title=current_chapter_title,
                    source_file=filename,
                    index=len(paragraphs) + 1,
                    order=order,
                )
            )
            continue

        # TextItem → 普通段落
        if len(text) < min_len:
            continue
        paragraphs.append(
            Paragraph(
                text=text,
                page=page,
                chapter=current_chapter,
                chapter_title=current_chapter_title,
                source_file=filename,
                index=len(paragraphs) + 1,
                order=order,
            )
        )

    # ── 合并被 docling 拆碎的相邻 TextItem 段落 ──
    # docling 布局模型有时把同一视觉段落拆成多个 TextItem（如"…带锁文件柜中。更" +
    # "换口令后及时更新电子版、纸质版密码本。"被拆成两段）。合并条件：
    #   同页 + 前段不以句末标点结尾（半句）+ 后段较短（<60 字）→ 拼接合并。
    _SENT_END = cfg.get("sentence_end_chars", "。；！？.;!？")
    if cfg.get("docling_merge_split_paras", True):
        merged_paras = []
        for p in paragraphs:
            if p.order and not p.text:  # 跳过空段
                continue
            if (
                merged_paras
                and p.page == merged_paras[-1].page
                and merged_paras[-1].text
                and not merged_paras[-1].text.rstrip().endswith(tuple(_SENT_END))
                and len(p.text) < 60
            ):
                merged_paras[-1].text += p.text
            else:
                merged_paras.append(p)
        paragraphs = merged_paras
        for i, p in enumerate(paragraphs, 1):
            p.index = i

    # ── 剥离高频页眉前缀（docling 把每页顶部的手册名拼到段落开头）──
    # 例："网络与信息安全管理手册"在每页顶部作为页眉出现，docling 未剥离，
    # 导致多个段落以手册名开头 → 版本对比时产生大量"手册名前缀增删"假差异。
    # 统计段落开头的稳定前缀：按长度 20→8 递减统计，捕获"完整手册名"（11 字）
    # 而非被正文污染的固定长度片段（如 [:18] 含正文导致频率分散）。
    if cfg.get("docling_strip_header_prefix", True) and len(paragraphs) >= 8:
        from collections import Counter

        _min_cnt = max(3, int(len(paragraphs) * 0.08))
        _common = set()
        for _L in range(20, 7, -1):
            _cnt = Counter(p.text[:_L] for p in paragraphs)
            for _h, _n in _cnt.items():
                if _n >= _min_cnt and _h.strip() and len(_h.strip()) >= 8:
                    _common.add(_h)
        if _common:
            # 按长度降序剥离（先剥离长前缀，避免残留）
            _common_sorted = sorted(_common, key=len, reverse=True)
            for p in paragraphs:
                for h in _common_sorted:
                    if p.text.startswith(h):
                        p.text = p.text[len(h):].lstrip()
                        break
            # 剥离后清空段重排
            paragraphs = [p for p in paragraphs if p.text.strip()]
            for i, p in enumerate(paragraphs, 1):
                p.index = i

    # ── 后处理：跨页表格合并 / 模板表过滤 / 章节分配 ──
    from doc_parser._tables import (
        assign_table_chapters,
        filter_template_tables,
        merge_cross_page_tables,
    )

    tables = merge_cross_page_tables(tables)
    tables = filter_template_tables(tables, cfg)
    assign_table_chapters(tables, paragraphs)

    # ── 数字断字后处理 ──
    if cfg.get("normalize_number_spacing", True):
        from doc_parser._text import normalize_number_spacing as _norm_num

        for para in paragraphs:
            para.text = _norm_num(para.text)
        for table in tables:
            table.rows = [[_norm_num(str(c)) for c in row] for row in table.rows]
            table.headers = [_norm_num(str(h)) for h in table.headers]

    # 释放 GPU 显存缓存：docling 模型（布局+TableFormer）加载后 torch caching
    # allocator 占用的显存不会自动归还，长驻服务下多次解析会累积泄漏。
    # 这里清空缓存块，让显存可被复用（下次解析/embedding 可用）。
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return Document(filename=filename, paragraphs=paragraphs, tables=tables)