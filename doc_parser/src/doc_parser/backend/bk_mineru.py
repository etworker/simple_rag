"""
MinerU 后端 — 使用 MinerU VLM/OCR 引擎解析 PDF

MinerU 在复杂排版（无框线表格、多栏布局、扫描件）场景下
比 pdfplumber 有显著优势。

使用前需安装 MinerU:
    uv pip install -U "mineru[all]"

选择此后端:
    parse("file.pdf", config={"extract": {"backend": "mineru"}})

配置项:
    config = {
        "extract": {
            "backend": "mineru",
            "mineru_backend": "vlm",       # "vlm"(GPU) / "pipeline"(CPU) / "auto"(默认)
            "mineru_output_dir": "/path",  # 输出目录（默认临时目录）
            "mineru_timeout": 600,         # 超时秒数
        }
    }
"""

import json
import os
import re
import shutil
import tempfile
from html.parser import HTMLParser

from doc_parser.models import Document, Paragraph, Table

# ============================================================
# HTML 表格解析
# ============================================================


class _HTMLTableParser(HTMLParser):
    """解析 HTML 表格，提取行列数据。"""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell = ""
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._current_cell = ""
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag == "tr":
            if self._current_row:
                self.rows.append(self._current_row)
        elif tag in ("td", "th"):
            self._current_row.append(self._current_cell.strip())
            self._current_cell = ""
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell += data


def _parse_html_table(html_str: str) -> list[list[str]]:
    """将 HTML 表格字符串解析为二维数组。"""
    parser = _HTMLTableParser()
    parser.feed(html_str)
    return parser.rows


# ============================================================
# MinerU 输出查找
# ============================================================


def _find_content_list_json(output_dir: str) -> str | None:
    """在 MinerU 输出目录中查找 content_list.json。"""
    for root, _dirs, files in os.walk(output_dir):
        for f in files:
            if f.endswith("_content_list.json") or f == "content_list.json":
                return os.path.join(root, f)
    return None


def _find_markdown(output_dir: str) -> str | None:
    """在 MinerU 输出目录中查找 Markdown 文件。"""
    for root, _dirs, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".md") and f != "README.md":
                return os.path.join(root, f)
    return None


# ============================================================
# GPU 检测
# ============================================================


def _has_gpu() -> bool:
    """检测当前机器是否有可用的 NVIDIA GPU。"""
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        # torch 未安装 → 肯定没有 GPU
        return False


# ============================================================
# 主入口
# ============================================================


def extract_pdf_with_mineru(filepath: str, config: dict | None = None):
    """使用 MinerU 解析 PDF，返回 Document 对象。

    流程:
        1. 调用 MinerU Python API (do_parse) 解析 PDF
        2. 读取 content_list.json（优先）或 Markdown（降级）
        3. 转换为 Document(paragraphs, tables)
        4. 复用 doc_parser 的后处理逻辑（章节检测、跨页合并等）
    """
    # 延迟导入避免循环依赖
    from doc_parser.parser import (
        get_extract_config,
    )

    cfg = get_extract_config(config)

    # ── 检查 MinerU 是否安装 ──
    try:
        from mineru.cli.common import do_parse
    except ImportError:
        raise RuntimeError('MinerU 未安装。请运行: uv pip install -U "mineru[all]"') from None

    # ── 后端选择（mineru 3.x 枚举：pipeline / vlm-engine / hybrid-engine / *-http-client）──
    # 旧值 "vlm" 兼容映射为 "vlm-engine"（mineru>=3.4 起更名）
    _BACKEND_MAP = {
        "vlm": "vlm-engine",
        "vlm-engine": "vlm-engine",
        "hybrid-engine": "hybrid-engine",
        "pipeline": "pipeline",
    }
    backend = cfg.get("mineru_backend", "auto")
    if backend == "auto":
        # 自动检测：有 GPU → vlm-engine，无 GPU → pipeline
        backend = "vlm-engine" if _has_gpu() else "pipeline"
    backend = _BACKEND_MAP.get(backend, backend)

    # ── 模型源：优先 HuggingFace（已有缓存），CPU 时用 modelscope 可能更慢 ──
    if not os.environ.get("MINERU_MODEL_SOURCE"):
        os.environ["MINERU_MODEL_SOURCE"] = "huggingface"

    # ── 准备输出目录 ──
    output_dir = cfg.get("mineru_output_dir", "")
    cleanup = False
    if not output_dir:
        output_dir = tempfile.mkdtemp(prefix="mineru_")
        cleanup = True

    try:
        # ── 读取 PDF ──
        with open(filepath, "rb") as f:
            pdf_bytes = f.read()

        # ── 调用 MinerU Python API ──
        do_parse(
            output_dir=output_dir,
            pdf_file_names=[os.path.basename(filepath)],
            pdf_bytes_list=[pdf_bytes],
            p_lang_list=["ch"],
            backend=backend,
            parse_method="auto",
            formula_enable=cfg.get("mineru_formula", True),
            table_enable=True,
            f_draw_layout_bbox=False,
            f_draw_span_bbox=False,
            f_dump_md=True,
            f_dump_middle_json=False,
            f_dump_model_output=False,
            f_dump_orig_pdf=False,
            f_dump_content_list=True,
            start_page_id=cfg.get("mineru_start_page", 0),
            end_page_id=cfg.get("mineru_end_page", None),
            # VLM 推理 batch（0=按显存自动；T4 16GB 建议 1，避免 OOM；多进程并行时每进程 batch=1 更稳）
            batch_size=cfg.get("mineru_batch_size", 0),
        )

        # ── 查找并解析输出 ──
        json_path = _find_content_list_json(output_dir)

        if json_path:
            with open(json_path, encoding="utf-8") as f:
                content_list = json.load(f)
            return _convert_json_to_document(content_list, filepath, cfg)

        # 降级：解析 Markdown
        md_path = _find_markdown(output_dir)
        if md_path:
            with open(md_path, encoding="utf-8") as f:
                md_content = f.read()
            return _convert_markdown_to_document(md_content, filepath, cfg)

        raise RuntimeError(f"MinerU 输出文件未找到。输出目录: {output_dir}")

    finally:
        if cleanup:
            shutil.rmtree(output_dir, ignore_errors=True)


# ============================================================
# JSON → Document 转换
# ============================================================


def _convert_json_to_document(content_list: list, filepath: str, cfg: dict):
    """将 MinerU 的 content_list.json 转换为 Document 对象。

    MinerU JSON 格式:
        [
            {"type": "text", "text": "...", "page_idx": 0, "bbox": [...]},
            {"type": "title", "text": "...", "page_idx": 0},
            {"type": "table", "text": "<html>...</html>", "page_idx": 1},
            ...
        ]
    """
    from doc_parser.parser import (
        _assign_table_chapters,
        _clean_table,
        _detect_chapter,
        _filter_template_tables,
        _merge_cross_page_tables,
    )

    paragraphs: list[Paragraph] = []
    tables: list[Table] = []
    current_chapter = ""
    current_chapter_title = ""
    min_len = cfg.get("min_paragraph_length", 10)

    for block_order, block in enumerate(content_list, 1):
        block_type = block.get("type", "text")
        text = block.get("text", "").strip()
        page_idx = block.get("page_idx", 0)
        page = page_idx + 1  # MinerU 使用 0-based 页码

        if block_type == "table":
            # MinerU table block: table_body 包含 HTML，text 可能为空
            html_text = block.get("table_body", "") or text
            if not html_text:
                continue

            rows = _parse_html_table(html_text)
            if not rows:
                continue

            cleaned = _clean_table(rows)
            if not cleaned or len(cleaned) < 2:
                continue

            tables.append(
                Table(
                    rows=cleaned,
                    page=page,
                    chapter=current_chapter,
                    chapter_title=current_chapter_title,
                    source_file=os.path.basename(filepath),
                    index=len(tables) + 1,
                    order=block_order,
                )
            )

        elif block_type in ("text", "title", "para"):
            # 跳过 header / page_number / image 等非正文类型
            # 章节检测
            ch = _detect_chapter(text, cfg)
            if ch:
                current_chapter, current_chapter_title = ch

            # 长度过滤（章节标题段免过滤）
            if not ch and len(text) < min_len:
                continue

            paragraphs.append(
                Paragraph(
                    text=text,
                    page=page,
                    chapter=current_chapter,
                    chapter_title=current_chapter_title,
                    source_file=os.path.basename(filepath),
                    index=len(paragraphs) + 1,
                    order=block_order,
                )
            )

    # ── 后处理（复用 pdfplumber 后端的逻辑）──
    tables = _merge_cross_page_tables(tables)
    tables = _filter_template_tables(tables, cfg)
    _assign_table_chapters(tables, paragraphs)

    return Document(
        filename=os.path.basename(filepath),
        paragraphs=paragraphs,
        tables=tables,
    )


# ============================================================
# Markdown → Document 转换（降级方案）
# ============================================================


def _convert_markdown_to_document(md_content: str, filepath: str, cfg: dict):
    """当 JSON 不可用时，从 MinerU 的 Markdown 输出解析为 Document。

    MinerU 的 Markdown 已经包含:
    - # / ## / ### 标题
    - | col | col | 表格
    - 普通段落
    """
    from doc_parser.parser import (
        _assign_table_chapters,
        _clean_table,
        _detect_chapter,
        _filter_template_tables,
        _merge_cross_page_tables,
    )

    paragraphs: list[Paragraph] = []
    tables: list[Table] = []
    current_chapter = ""
    current_chapter_title = ""
    min_len = cfg.get("min_paragraph_length", 10)

    lines = md_content.split("\n")
    i = 0
    block_order = 0
    while i < len(lines):
        line = lines[i].strip()

        if not line:
            i += 1
            continue

        # Markdown 标题
        if line.startswith("#"):
            block_order += 1
            heading = re.sub(r"^#{1,6}\s+", "", line)
            ch = _detect_chapter(heading, cfg)
            if ch:
                current_chapter, current_chapter_title = ch

            if len(heading) >= 2 or ch:
                paragraphs.append(
                    Paragraph(
                        text=heading,
                        page=0,
                        chapter=current_chapter,
                        chapter_title=current_chapter_title,
                        source_file=os.path.basename(filepath),
                        index=len(paragraphs) + 1,
                        order=block_order,
                    )
                )
            i += 1
            continue

        # Markdown 表格
        if line.startswith("|") and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line.startswith("|") and "---" in next_line:
                block_order += 1
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1

                rows = []
                for tl in table_lines:
                    if re.match(r"^\|[\s\-:|]+\|\s*$", tl):
                        continue
                    cells = [c.strip() for c in tl.strip("|").split("|")]
                    rows.append(cells)

                if len(rows) >= 2:
                    cleaned = _clean_table(rows)
                    if cleaned and len(cleaned) >= 2:
                        tables.append(
                            Table(
                                rows=cleaned,
                                page=0,
                                chapter=current_chapter,
                                chapter_title=current_chapter_title,
                                source_file=os.path.basename(filepath),
                                index=len(tables) + 1,
                                order=block_order,
                            )
                        )
                continue

        # 普通文本
        block_order += 1
        ch = _detect_chapter(line, cfg)
        if ch:
            current_chapter, current_chapter_title = ch

        if not ch and len(line) < min_len:
            i += 1
            continue

        paragraphs.append(
            Paragraph(
                text=line,
                page=0,
                chapter=current_chapter,
                chapter_title=current_chapter_title,
                source_file=os.path.basename(filepath),
                index=len(paragraphs) + 1,
                order=block_order,
            )
        )
        i += 1

    # 后处理
    tables = _merge_cross_page_tables(tables)
    tables = _filter_template_tables(tables, cfg)
    _assign_table_chapters(tables, paragraphs)

    return Document(
        filename=os.path.basename(filepath),
        paragraphs=paragraphs,
        tables=tables,
    )
