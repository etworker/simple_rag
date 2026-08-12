"""数据模型"""

from dataclasses import dataclass, field


@dataclass
class Paragraph:
    """带定位信息的段落"""

    text: str
    page: int = 0
    page_end: int = 0
    chapter: str = ""
    chapter_title: str = ""
    source_file: str = ""
    index: int = 0

    @property
    def location(self) -> str:
        parts = []
        if self.page:
            if self.page_end and self.page_end != self.page:
                parts.append(f"第{self.page}-{self.page_end}页")
            else:
                parts.append(f"第{self.page}页")
        if self.chapter:
            parts.append(f"§{self.chapter}")
        if self.chapter_title:
            parts.append(self.chapter_title)
        return " / ".join(parts) if parts else f"段落#{self.index}"

    def to_dict(self) -> dict:
        """序列化为 JSON 友好的 dict（字段单一来源，避免各处手抄）"""
        return {
            "text": self.text,
            "page": self.page,
            "page_end": self.page_end,
            "chapter": self.chapter,
            "chapter_title": self.chapter_title,
            "source_file": self.source_file,
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Paragraph":
        return cls(
            text=d.get("text", ""),
            page=d.get("page", 0),
            page_end=d.get("page_end", 0),
            chapter=d.get("chapter", ""),
            chapter_title=d.get("chapter_title", ""),
            source_file=d.get("source_file", ""),
            index=d.get("index", 0),
        )

    def to_markdown(self) -> str:
        """转为 Markdown 片段。

        - 有章节号 + 标题 → Markdown 标题（层级按编号深度推断：1 → H1，1.1 → H2，…）
        - 无章节信息 → 纯文本段落
        """
        if self.chapter and self.chapter_title:
            level = self.chapter.count(".") + 1
            level = min(level, 6)  # Markdown 最多 6 级
            return f"{'#' * level} {self.chapter} {self.chapter_title}\n\n{self.text}"
        if self.chapter_title:
            return f"# {self.chapter_title}\n\n{self.text}"
        return self.text


@dataclass
class Table:
    """文档中的表格"""

    rows: list = field(default_factory=list)
    headers: list = field(default_factory=list)
    page: int = 0
    page_end: int = 0
    chapter: str = ""
    chapter_title: str = ""
    context_before: str = ""
    source_file: str = ""
    index: int = 0

    @property
    def location(self) -> str:
        parts = []
        if self.page:
            parts.append(f"第{self.page}页")
        if self.chapter:
            parts.append(f"§{self.chapter}")
        if self.chapter_title:
            parts.append(self.chapter_title)
        return " / ".join(parts) if parts else f"表格#{self.index}"

    def to_dict(self) -> dict:
        """序列化表格为 JSON 友好的 dict（字段单一来源）"""
        return {
            "rows": self.rows,
            "headers": self.headers,
            "page": self.page,
            "page_end": self.page_end,
            "chapter": self.chapter,
            "chapter_title": self.chapter_title,
            "context_before": self.context_before,
            "source_file": self.source_file,
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Table":
        return cls(
            rows=d.get("rows", []),
            headers=d.get("headers", []),
            page=d.get("page", 0),
            page_end=d.get("page_end", 0),
            chapter=d.get("chapter", ""),
            chapter_title=d.get("chapter_title", ""),
            context_before=d.get("context_before", ""),
            source_file=d.get("source_file", ""),
            index=d.get("index", 0),
        )

    def to_markdown(self) -> str:
        """转为 Markdown 表格。

        格式：
        ```markdown
        | 列1 | 列2 | 列3 |
        |-----|-----|-----|
        | a   | b   | c   |
        ```

        - 有 headers → 用 headers 作表头
        - 无 headers → 用第一行作表头
        - 空表格 → 空字符串
        """
        if not self.rows:
            return ""

        header = self.headers or self.rows[0]
        data_rows = self.rows if self.headers else self.rows[1:]
        if not header:
            return ""

        def _escape(cell) -> str:
            """转义 Markdown 表格中的特殊字符。"""
            s = str(cell) if cell is not None else ""
            return s.replace("|", "\\|").replace("\n", " ")

        lines = []
        # 表头
        lines.append("| " + " | ".join(_escape(c) for c in header) + " |")
        # 分隔行
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        # 数据行
        for row in data_rows:
            # 列数对齐
            cells = list(row) + [""] * (len(header) - len(row))
            cells = cells[: len(header)]
            lines.append("| " + " | ".join(_escape(c) for c in cells) + " |")

        result = "\n".join(lines)

        # 表格前上下文
        if self.context_before:
            result = f"> {self.context_before}\n\n{result}"

        return result


@dataclass
class Document:
    """文档解析结果"""

    filename: str
    paragraphs: list[Paragraph] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化为 JSON 友好的 dict（字段单一来源，避免各处手抄）"""
        return {
            "filename": self.filename,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "tables": [t.to_dict() for t in self.tables],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        return cls(
            filename=d.get("filename", ""),
            paragraphs=[Paragraph.from_dict(p) for p in d.get("paragraphs", [])],
            tables=[Table.from_dict(t) for t in d.get("tables", [])],
        )

    def to_markdown(self) -> str:
        """将整个文档转为 Markdown。

        策略：
        1. 把段落和表格合并为一个有序列表，按 (page, index) 排序
        2. 跳过目录条目（末尾有 0.1-1 或 N 页 格式的页码引用）
        3. 逐段判断是否为章节标题（文本 == 章节号 + 标题），是则渲染为 Markdown 标题
        4. 若某章节无独立标题段，在首次遇到该章节正文时自动补插标题
        5. 表格只渲染表格本身，不触发标题插入

        Returns:
            Markdown 格式的字符串
        """
        import re

        # 目录条目特征：末尾跟章节页码引用（如 0.1-1、1.3-12）或页数（如 35 页）
        _toc_re = re.compile(r"\d+\.\d+-\d+\s*$|\s+\d+\s*页\s*$")
        # 页数后缀剥离（如 "35 页"）
        _page_suffix_re = re.compile(r"\s+\d+\s*页\s*$")
        # TOC 页码引用剥离（如 "0.1-1"、"1.3-12"）
        _toc_ref_re = re.compile(r"\s+\d+\.\d+-\d+\s*$")

        def _heading_level(chapter: str) -> int:
            return min(chapter.count(".") + 1, 6)

        def _clean_title(title: str) -> str:
            """剥离页数后缀和 TOC 页码引用"""
            title = _page_suffix_re.sub("", title)
            title = _toc_ref_re.sub("", title)
            return title.strip()

        def _norm(s: str) -> str:
            """归一化用于标题比较：去空白 + 去标题分隔符"""
            return re.sub(r"[\s、（）()]+", "", s)

        # 合并段落和表格，按 (page, index) 排序
        items = []
        for p in self.paragraphs:
            items.append((p.page, p.index, "para", p))
        for t in self.tables:
            items.append((t.page, t.index, "table", t))
        items.sort(key=lambda x: (x[0], x[1]))

        lines = []
        rendered_chapters = set()  # 已输出过标题的章节号
        chapter_titles = {}  # 章节号 → 清洁标题（从 TOC 条目收集）

        # 第一遍：从 TOC 条目收集章节标题信息
        for _, _, kind, item in items:
            if kind == "para" and item.chapter and item.chapter_title:
                clean = _clean_title(f"{item.chapter} {item.chapter_title}".strip())
                chapter_titles.setdefault(item.chapter, clean)

        def _ensure_parent_chapters(chapter: str):
            """确保父章节标题已输出（如遇到 1.1 时补插 # 1）"""
            parts = chapter.split(".")
            for i in range(len(parts) - 1, 0, -1):
                parent = ".".join(parts[:i])
                if parent and parent not in rendered_chapters and parent in chapter_titles:
                    level = _heading_level(parent)
                    lines.append(f"\n{'#' * level} {chapter_titles[parent]}\n")
                    rendered_chapters.add(parent)
                    lines.append("")

        for _, _, kind, item in items:
            if kind == "para":
                text = item.text.strip()

                # 有章节号 + 标题 → 可能是标题段或目录条目
                if item.chapter and item.chapter_title:
                    heading_text = f"{item.chapter} {item.chapter_title}".strip()
                    clean_heading = _clean_title(heading_text)

                    # 目录条目 → 跳过（标题信息已在第一遍收集）
                    if _toc_re.search(text):
                        continue

                    # 文本与标题一致 → 渲染为 Markdown 标题
                    if (
                        _norm(text) == _norm(heading_text)
                        or _norm(text) == _norm(item.chapter_title)
                        or _norm(text) == _norm(clean_heading)
                    ):
                        _ensure_parent_chapters(item.chapter)
                        level = _heading_level(item.chapter)
                        lines.append(f"\n{'#' * level} {clean_heading}\n")
                        rendered_chapters.add(item.chapter)
                        lines.append("")
                        continue

                    # 标题后紧跟正文（归一化前缀匹配）→ 分离输出
                    if _norm(text).startswith(_norm(heading_text)) or _norm(text).startswith(_norm(clean_heading)):
                        _ensure_parent_chapters(item.chapter)
                        level = _heading_level(item.chapter)
                        lines.append(f"\n{'#' * level} {clean_heading}\n")
                        rendered_chapters.add(item.chapter)
                        # 找到标题在原文中的结束位置，提取剩余正文
                        remaining = None
                        for prefix in (heading_text, clean_heading):
                            if text.startswith(prefix):
                                remaining = text[len(prefix) :].strip()
                                break
                        if remaining is None:
                            # 归一化匹配但前缀不完全一致（如 "（一）标题．正文" vs "一 标题"）
                            # 在原文中搜索标题末尾，从标题之后切分
                            title_end = text.find(item.chapter_title)
                            if title_end >= 0:
                                remaining = text[title_end + len(item.chapter_title) :].strip()
                                # 去除开头可能残留的终止符（．。：:）
                                if remaining and remaining[0] in "．。：:":
                                    remaining = remaining[1:].strip()
                            else:
                                remaining = text[len(heading_text) :].strip()
                        if remaining:
                            lines.append(remaining)
                        lines.append("")
                        continue

                    # 正文段：若该章节尚未输出标题，自动补插
                    if item.chapter not in rendered_chapters:
                        _ensure_parent_chapters(item.chapter)
                        level = _heading_level(item.chapter)
                        lines.append(f"\n{'#' * level} {clean_heading}\n")
                        rendered_chapters.add(item.chapter)

                # 普通正文段
                lines.append(text)
            else:
                # 表格 → 只渲染表格，不插入标题
                md = item.to_markdown()
                if md:
                    lines.append(md)

            lines.append("")  # 空行分隔

        return "\n".join(lines).strip() + "\n"
