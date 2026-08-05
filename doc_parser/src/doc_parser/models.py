"""数据模型"""
from dataclasses import dataclass, field
from typing import List


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


@dataclass
class Document:
    """文档解析结果"""
    filename: str
    paragraphs: List[Paragraph] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
