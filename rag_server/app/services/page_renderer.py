"""
PDF 页面渲染缓存服务

将 PDF 每页渲染为 PNG 图片，支持文字高亮。
缓存结构：data/page_cache/{file_hash}/page_{N:03d}.png
高亮版本：data/page_cache/{file_hash}/page_{N:03d}_hl_{text_hash}.png
"""

import hashlib
import json
import os
from threading import Lock

from loguru import logger as log

from app.paths import cache_subdir


_RENDER_LOCK = Lock()


def _tolerant_search_rects(pg, highlight, min_len: int = 12):
    """忽略空白差异的文字定位（用于高亮）。

    PDF 文本层与解析出的 chunk 文本可能因空格/换行位置不同而无法被
    search_for 精确命中（例如“为 OKAIR” vs “为OKAIR”，或长句跨行）。
    这里去掉全部空白后按 word 拼接全文匹配；整串未命中时退回最长前缀匹配。

    Returns:
        fitz.Rect 列表；找不到时返回空列表
    """
    import bisect
    import re

    import fitz

    hl_norm = re.sub(r"\s+", "", highlight)
    if not hl_norm:
        return []
    words = pg.get_text("words", sort=True)
    if not words:
        return []
    parts, starts, cur = [], [], 0
    for w in words:
        starts.append(cur)
        parts.append(w[4])
        cur += len(w[4])
    full = "".join(parts)
    pos = full.find(hl_norm)
    matched_len = len(hl_norm)
    if pos < 0:
        # 最长前缀匹配（页面该句可能因分页/表格拆分而不完整）
        pos = -1
        for length in range(min(len(hl_norm), 80), min_len - 1, -1):
            p = full.find(hl_norm[:length])
            if p >= 0:
                pos, matched_len = p, length
                break
    if pos < 0:
        return []
    end = pos + matched_len
    i = max(bisect.bisect_right(starts, pos) - 1, 0)
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    while i < len(words) and starts[i] < end:
        w = words[i]
        x0 = min(x0, w[0])
        y0 = min(y0, w[1])
        x1 = max(x1, w[2])
        y1 = max(y1, w[3])
        i += 1
    if x1 < x0:
        return []
    return [fitz.Rect(x0, y0, x1, y1)]


class PageRenderer:
    """
    按需渲染 PDF 页面为 PNG 图片，支持文字高亮

    Example:
        renderer = PageRenderer(cache_dir="./data/page_cache")
        # 普通渲染
        png_path = renderer.get_page("doc.pdf", page=3)
        # 带高亮
        png_path = renderer.get_page("doc.pdf", page=3, highlight="备份频率")
    """

    def __init__(self, cache_dir: str = "", dpi: int = 150):
        # 默认缓存目录（<root>/page_cache/），root 解析见 app.paths
        if not cache_dir:
            cache_dir = cache_subdir("page_cache")
        self._cache_dir = cache_dir
        self._dpi = dpi
        os.makedirs(cache_dir, exist_ok=True)

    def get_page(
        self,
        pdf_path: str,
        page: int = 1,
        highlight: str = "",
        anchor_rects: list[list[float]] | None = None,
    ) -> str:
        """
        获取指定页的 PNG 图片路径（自动缓存）

        Args:
            pdf_path: PDF 文件路径
            page: 页码（从 1 开始）
            highlight: 需要高亮的文字（可选）
            anchor_rects: 解析阶段保存的本页 PDF 坐标矩形；提供后优先使用，
                None 才会回退到 search_for/容错文本搜索。

        Returns:
            PNG 图片的绝对路径
        """
        file_hash = self._hash_file(pdf_path)
        cache_subdir = os.path.join(self._cache_dir, file_hash)
        os.makedirs(cache_subdir, exist_ok=True)

        # 有高亮或坐标锚点时用不同的缓存文件名。坐标必须参与 hash，
        # 否则同页不同来源会复用错误的高亮图片。
        if highlight or anchor_rects is not None:
            cache_key = {
                "highlight": highlight,
                "anchor_rects": anchor_rects,
            }
            hl_hash = hashlib.sha256(json.dumps(cache_key, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[-8:].upper()
            png_path = os.path.join(cache_subdir, f"page_{page:03d}_hl_{hl_hash}.png")
        else:
            png_path = os.path.join(cache_subdir, f"page_{page:03d}.png")

        if os.path.exists(png_path):
            log.debug("页面缓存命中: {} 第{}页 highlight={}", os.path.basename(pdf_path), page, bool(highlight))
            return png_path

        # 同一页面并发请求时，二次检查避免重复打开 PDF 和渲染。
        with _RENDER_LOCK:
            if os.path.exists(png_path):
                log.debug("页面缓存命中（并发等待后）: {} 第{}页 highlight={}", os.path.basename(pdf_path), page, bool(highlight))
                return png_path
            log.debug("页面缓存未命中，开始渲染: {} 第{}页 highlight={}", os.path.basename(pdf_path), page, bool(highlight))
            self._render_page(pdf_path, page, png_path, highlight, anchor_rects)
        return png_path

    def get_page_count(self, pdf_path: str) -> int:
        """获取 PDF 总页数"""
        from app.services.utils import get_pdf_page_count

        return get_pdf_page_count(pdf_path)

    def _render_page(
        self,
        pdf_path: str,
        page: int,
        output_path: str,
        highlight: str = "",
        anchor_rects: list[list[float]] | None = None,
    ):
        """渲染单页为 PNG，可选使用精确坐标或文本回退高亮。"""
        import fitz

        doc = fitz.open(pdf_path)
        if page < 1 or page > len(doc):
            doc.close()
            raise ValueError(f"页码 {page} 超出范围 (1-{len(doc)})")

        pg = doc[page - 1]  # fitz 从 0 开始

        # anchor_rects 即使为空也代表“已建立锚点但本页没有该段落”，
        # 此时不能回退到重复文本的首个命中。
        if anchor_rects is not None:
            for values in anchor_rects:
                if len(values) != 4:
                    continue
                pg.add_highlight_annot(fitz.Rect(*[float(value) for value in values]))
        elif highlight:
            # 旧数据/无坐标文档：search_for 未命中时用容错搜索。
            rects = pg.search_for(highlight)
            if not rects:
                rects = _tolerant_search_rects(pg, highlight)
            for r in rects:
                pg.add_highlight_annot(r)

        # 渲染
        zoom = self._dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = pg.get_pixmap(matrix=mat)
        pix.save(output_path)
        doc.close()

        log.info(f"渲染PDF页面: {os.path.basename(pdf_path)} 第{page}页")

    @staticmethod
    def _hash_file(filepath: str) -> str:
        """文件路径 + 大小 + 修改时间 的 hash（避免读全文件）"""
        stat = os.stat(filepath)
        key = f"{filepath}|{stat.st_size}|{stat.st_mtime}"
        return hashlib.sha256(key.encode()).hexdigest()[-10:].upper()
