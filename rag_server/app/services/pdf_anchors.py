"""基于 PyMuPDF 字符 bbox 的 PDF 段落定位锚点。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _PdfChar:
    page: int
    char: str
    bbox: tuple[float, float, float, float]
    y_center: float


def _normalized_char(value: str) -> str:
    """将一个 PDF 字符归一化为可匹配的字符序列。"""
    value = unicodedata.normalize("NFKC", value or "")
    return _WHITESPACE_RE.sub("", value)


def normalize_anchor_text(value: str) -> str:
    """归一化段落/PDF 文本，忽略空白并统一 Unicode 兼容字符。"""
    return "".join(_normalized_char(ch) for ch in str(value or ""))


def _page_chars(page, page_number: int) -> list[_PdfChar]:
    """从 rawdict 提取按阅读顺序排列的字符及其 bbox。"""
    raw = page.get_text("rawdict", sort=True)
    chars: list[_PdfChar] = []
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for item in span.get("chars", []):
                    text = item.get("c", "")
                    bbox = item.get("bbox")
                    if not text or not bbox or len(bbox) != 4:
                        continue
                    normalized = _normalized_char(text)
                    if not normalized:
                        continue
                    x0, y0, x1, y1 = (float(v) for v in bbox)
                    for char in normalized:
                        chars.append(
                            _PdfChar(
                                page=page_number,
                                char=char,
                                bbox=(x0, y0, x1, y1),
                                y_center=(y0 + y1) / 2,
                            )
                        )
    return chars


def _merge_char_rects(chars: list[_PdfChar], y_tolerance: float = 3.0) -> list[list[float]]:
    """将命中的字符按视觉行合并成多个高亮矩形。"""
    if not chars:
        return []
    lines: list[list[_PdfChar]] = []
    for char in sorted(chars, key=lambda item: (item.y_center, item.bbox[0])):
        target = None
        for line in lines:
            line_y = sum(item.y_center for item in line) / len(line)
            if abs(char.y_center - line_y) <= y_tolerance:
                target = line
                break
        if target is None:
            lines.append([char])
        else:
            target.append(char)

    rects = []
    for line in sorted(lines, key=lambda group: min(item.bbox[1] for item in group)):
        rects.append(
            [
                min(item.bbox[0] for item in line),
                min(item.bbox[1] for item in line),
                max(item.bbox[2] for item in line),
                max(item.bbox[3] for item in line),
            ]
        )
    return rects


def build_pdf_anchors(filepath: str, paragraphs: list) -> int:
    """为段落生成可持久化的页级行矩形锚点。

    ``Paragraph.pdf_spans`` 的格式为：
    ``[{"page": 1, "rects": [[x0, y0, x1, y1], ...]}]``。
    段落文本和 PDF 文本层之间的空格、全角兼容字符差异会被忽略，
    但矩形仍然使用原始 PDF 坐标系，和 PageRenderer 完全一致。

    返回成功处理的段落数量。无法匹配的段落也会写入空列表，表示该段落
    已尝试建立锚点，调用方可以避免错误地回退到重复文本搜索。
    """
    if not filepath.lower().endswith(".pdf") or not paragraphs:
        return 0

    import fitz

    with fitz.open(filepath) as pdf:
        all_chars: list[_PdfChar] = []
        page_offsets: dict[int, tuple[int, int]] = {}
        for page_number in range(1, pdf.page_count + 1):
            start = len(all_chars)
            all_chars.extend(_page_chars(pdf.load_page(page_number - 1), page_number))
            page_offsets[page_number] = (start, len(all_chars))

    normalized = "".join(char.char for char in all_chars)
    cursor = 0
    processed = 0
    for paragraph in paragraphs:
        target = normalize_anchor_text(getattr(paragraph, "text", ""))
        paragraph.pdf_spans = []
        if not target:
            processed += 1
            continue

        page_start = int(getattr(paragraph, "page", 0) or 1)
        page_end = int(getattr(paragraph, "page_end", 0) or page_start)
        page_start = max(1, page_start)
        page_end = max(page_start, page_end)
        allowed_start = page_offsets.get(page_start, (0, len(all_chars)))[0]
        allowed_end = page_offsets.get(page_end, (0, len(all_chars)))[1]
        allowed_end = max(allowed_start, allowed_end)

        position = normalized.find(target, max(cursor, allowed_start), allowed_end)
        if position < 0:
            # 页面边界/解析页码可能不完整时，仍只在段落声明页码之后搜索，
            # 避免把同名文本定位到前面版本或前面页。
            position = normalized.find(target, max(cursor, allowed_start))
        if position >= 0:
            matched = all_chars[position : position + len(target)]
            grouped: dict[int, list[_PdfChar]] = {}
            for char in matched:
                grouped.setdefault(char.page, []).append(char)
            paragraph.pdf_spans = [
                {"page": page, "rects": _merge_char_rects(page_chars)}
                for page, page_chars in sorted(grouped.items())
                if page_chars
            ]
            cursor = position + len(target)
        processed += 1
    return processed


def page_rects_from_spans(pdf_spans: list[dict] | None, page: int) -> list[list[float]] | None:
    """从段落锚点取指定页面矩形；None 表示该段落没有锚点。"""
    if pdf_spans is None:
        return None
    for span in pdf_spans:
        if int(span.get("page", 0) or 0) != int(page):
            continue
        return [list(rect) for rect in span.get("rects", []) if len(rect) == 4]
    return []
