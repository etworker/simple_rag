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


@dataclass
class Document:
    """文档解析结果"""
    filename: str
    paragraphs: List[Paragraph] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
