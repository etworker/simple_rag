"""
PDF 页面渲染缓存服务

将 PDF 每页渲染为 PNG 图片，支持文字高亮。
缓存结构：data/page_cache/{file_hash}/page_{N:03d}.png
高亮版本：data/page_cache/{file_hash}/page_{N:03d}_hl_{text_hash}.png
"""

import hashlib
import logging
import os

log = logging.getLogger("rag_demo.page_renderer")


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
        # 默认缓存目录（~/.simple_rag/page_cache/）
        if not cache_dir:
            cache_dir = os.path.join(
                os.path.expanduser("~"), ".simple_rag", "page_cache"
            )
        self._cache_dir = cache_dir
        self._dpi = dpi
        os.makedirs(cache_dir, exist_ok=True)

    def get_page(self, pdf_path: str, page: int = 1, highlight: str = "") -> str:
        """
        获取指定页的 PNG 图片路径（自动缓存）

        Args:
            pdf_path: PDF 文件路径
            page: 页码（从 1 开始）
            highlight: 需要高亮的文字（可选）

        Returns:
            PNG 图片的绝对路径
        """
        file_hash = self._hash_file(pdf_path)
        cache_subdir = os.path.join(self._cache_dir, file_hash)
        os.makedirs(cache_subdir, exist_ok=True)

        # 有高亮时用不同的缓存文件名
        if highlight:
            hl_hash = hashlib.sha256(highlight.encode()).hexdigest()[-8:].upper()
            png_path = os.path.join(cache_subdir, f"page_{page:03d}_hl_{hl_hash}.png")
        else:
            png_path = os.path.join(cache_subdir, f"page_{page:03d}.png")

        if os.path.exists(png_path):
            return png_path

        # 按需渲染
        self._render_page(pdf_path, page, png_path, highlight)
        return png_path

    def get_page_count(self, pdf_path: str) -> int:
        """获取 PDF 总页数"""
        import fitz

        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count

    def _render_page(
        self, pdf_path: str, page: int, output_path: str, highlight: str = ""
    ):
        """渲染单页为 PNG，可选高亮文字"""
        import fitz

        doc = fitz.open(pdf_path)
        if page < 1 or page > len(doc):
            doc.close()
            raise ValueError(f"页码 {page} 超出范围 (1-{len(doc)})")

        pg = doc[page - 1]  # fitz 从 0 开始

        # 高亮文字
        if highlight:
            rects = pg.search_for(highlight)
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
