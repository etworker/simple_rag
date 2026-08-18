"""解析结果质量检查：结构化 Markdown、规则候选和审查报告。

本模块只读消费 :class:`Document`，不修改解析结果、不调用 LLM，也不依赖应用层。
LLM 审查由 ``rag_server.app.services.parse_qa`` 负责，以保持 doc_parser 的轻量依赖。
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

from doc_parser.models import Document, Paragraph, Table

_TERMINAL_CHARS = tuple("。！？.!?；;")
_SUSPICIOUS_END_CHARS = tuple("，,、：:")


def _ordered_items(document: Document) -> list[tuple[str, Paragraph | Table]]:
    """按解析器提供的原始顺序合并段落和表格。"""
    items: list[tuple[int, int, int, str, Paragraph | Table]] = []
    for paragraph in document.paragraphs:
        items.append((paragraph.page, paragraph.order or paragraph.index, 0, "paragraph", paragraph))
    for table in document.tables:
        items.append((table.page, table.order or table.index, 1, "table", table))
    items.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(kind, item) for _, _, _, kind, item in items]


def _metadata_comment(kind: str, item: Paragraph | Table) -> str:
    page_end = getattr(item, "page_end", 0) or getattr(item, "page", 0)
    fields = {
        "type": kind,
        "index": getattr(item, "index", 0),
        "page": getattr(item, "page", 0),
        "page_end": page_end,
        "chapter": getattr(item, "chapter", ""),
        "source_file": getattr(item, "source_file", ""),
    }
    if kind == "table":
        header = item.headers or (item.rows[0] if item.rows else [])
        fields.update({"columns": len(header), "rows": len(item.rows)})
    body = " ".join(f"{key}={value!r}" for key, value in fields.items())
    return f"<!-- parse_qa {body} -->"


def document_to_markdown(document: Document) -> str:
    """将 Document 转成适合质检的 Markdown，并保留页码/表格元数据。"""
    lines = [f"<!-- parse_qa filename={document.filename!r} -->", ""]
    for kind, item in _ordered_items(document):
        lines.append(_metadata_comment(kind, item))
        rendered = item.to_markdown()
        if rendered:
            lines.extend([rendered, ""])
    return "\n".join(lines).rstrip() + "\n"


def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def _page_range(item: Paragraph | Table, padding: int = 0) -> list[int]:
    page = max(1, int(getattr(item, "page", 0) or 1) - padding)
    end = int(getattr(item, "page_end", 0) or getattr(item, "page", 0) or page) + padding
    return [page, max(page, end)]


@dataclass
class ParseQAIssue:
    """一个可供人工或 LLM 审查的解析风险项。"""

    issue_id: str
    kind: str
    risk: str
    message: str
    page: int = 0
    page_end: int = 0
    paragraph_index: int = 0
    table_index: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_backend: str = ""
    suggested_page_range: list[int] = field(default_factory=list)
    llm_decision: str = ""
    llm_reason: str = ""
    llm_confidence: float | None = None
    is_parse_error: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "kind": self.kind,
            "risk": self.risk,
            "message": self.message,
            "page": self.page,
            "page_end": self.page_end,
            "paragraph_index": self.paragraph_index,
            "table_index": self.table_index,
            "evidence": self.evidence,
            "suggested_backend": self.suggested_backend,
            "suggested_page_range": self.suggested_page_range,
            "llm_decision": self.llm_decision,
            "llm_reason": self.llm_reason,
            "llm_confidence": self.llm_confidence,
            "is_parse_error": self.is_parse_error,
        }


@dataclass
class ParseQAReport:
    """规则或规则+LLM 质检报告。"""

    status: str
    stats: dict[str, Any]
    issues: list[ParseQAIssue] = field(default_factory=list)
    incomplete: bool = False
    markdown: str = ""

    @property
    def high_risk_items(self) -> list[ParseQAIssue]:
        return [item for item in self.issues if item.risk == "high"]

    def llm_candidates(self, max_items: int = 20) -> list[ParseQAIssue]:
        """按风险排序返回有限候选，避免把整份文档发送给 LLM。"""
        rank = {"high": 0, "medium": 1, "low": 2}
        candidates = sorted(self.issues, key=lambda item: (rank.get(item.risk, 9), item.issue_id))
        return candidates[: max(0, int(max_items))]

    def to_dict(self, include_markdown: bool = False) -> dict[str, Any]:
        result = {
            "status": self.status,
            "incomplete": self.incomplete,
            "stats": self.stats,
            "issues": [item.to_dict() for item in self.issues],
        }
        if include_markdown:
            result["markdown"] = self.markdown
        return result


def _new_issue(
    issues: list[ParseQAIssue],
    *,
    kind: str,
    risk: str,
    message: str,
    item: Paragraph | Table,
    evidence: dict[str, Any] | None = None,
    suggested_backend: str = "",
    suggested_page_range: list[int] | None = None,
) -> None:
    base_issue_id = f"{kind}-{getattr(item, 'index', len(issues) + 1)}"
    issue_id = base_issue_id
    suffix = 2
    existing_ids = {issue.issue_id for issue in issues}
    while issue_id in existing_ids:
        issue_id = f"{base_issue_id}-{suffix}"
        suffix += 1
    issues.append(
        ParseQAIssue(
            issue_id=issue_id,
            kind=kind,
            risk=risk,
            message=message,
            page=int(getattr(item, "page", 0) or 0),
            page_end=int(getattr(item, "page_end", 0) or 0),
            paragraph_index=getattr(item, "index", 0) if isinstance(item, Paragraph) else 0,
            table_index=getattr(item, "index", 0) if isinstance(item, Table) else 0,
            evidence=evidence or {},
            suggested_backend=suggested_backend,
            suggested_page_range=suggested_page_range or [],
        )
    )


def inspect_document(document: Document, config: dict[str, Any] | None = None) -> ParseQAReport:
    """执行只读的确定性解析质量检查。"""
    cfg = config or {}
    issues: list[ParseQAIssue] = []
    paragraphs = document.paragraphs
    tables = document.tables
    min_length = int(cfg.get("min_paragraph_length", 10))
    max_length = int(cfg.get("max_paragraph_length", 600))

    for paragraph in paragraphs:
        text = paragraph.text.strip()
        is_heading = bool(paragraph.chapter and paragraph.chapter_title and len(text) <= max_length)
        if paragraph.page_end and paragraph.page_end > paragraph.page:
            suspicious_end = bool(text) and text[-1] in _SUSPICIOUS_END_CHARS
            _new_issue(
                issues,
                kind="cross_page_paragraph",
                risk="high" if suspicious_end else "medium",
                message="段落跨页，需检查是否错误拆分或与下一页错误合并。",
                item=paragraph,
                evidence={"text": text[-500:], "ends_with": text[-1:] if text else ""},
                suggested_backend="docling" if suspicious_end else "",
                suggested_page_range=_page_range(paragraph, padding=1),
            )
        elif not is_heading and len(text) < min_length:
            _new_issue(
                issues,
                kind="short_fragment",
                risk="low",
                message="段落过短，可能是标题、列表项或解析碎片。",
                item=paragraph,
                evidence={"text": text},
            )
        elif not is_heading and len(text) > max_length * 2:
            _new_issue(
                issues,
                kind="long_paragraph",
                risk="low",
                message="段落明显超过默认长度，需检查是否多个段落被错误合并。",
                item=paragraph,
                evidence={"text": text[:300] + "…"},
            )

    for previous, current in itertools.pairwise(paragraphs):
        if len(previous.text.strip()) >= 20 and _normalise_text(previous.text) == _normalise_text(current.text):
            _new_issue(
                issues,
                kind="duplicate_paragraph",
                risk="high",
                message="相邻段落文本重复，可能是页眉、页脚或跨页重复提取。",
                item=current,
                evidence={"previous_index": previous.index, "text": current.text[:500]},
            )

    for table in tables:
        header = table.headers or (table.rows[0] if table.rows else [])
        expected_columns = len(header)
        row_lengths = [len(row) for row in table.rows]
        inconsistent = expected_columns > 0 and any(length != expected_columns for length in row_lengths)
        if not table.rows:
            _new_issue(
                issues,
                kind="empty_table",
                risk="high",
                message="表格没有有效数据行。",
                item=table,
                evidence={},
            )
        elif inconsistent:
            _new_issue(
                issues,
                kind="table_structure",
                risk="high",
                message="表格行列数不一致，可能存在错列、截断或跨页合并问题。",
                item=table,
                evidence={"expected_columns": expected_columns, "row_lengths": row_lengths[:20]},
                suggested_backend="docling",
                suggested_page_range=_page_range(table, padding=1),
            )
        if table.page_end and table.page_end > table.page:
            _new_issue(
                issues,
                kind="cross_page_table",
                risk="high",
                message="表格跨页，需检查表头重复、行顺序和是否误合并。",
                item=table,
                evidence={
                    "columns": expected_columns,
                    "rows": len(table.rows),
                    "first_rows": table.rows[:2],
                    "last_rows": table.rows[-2:],
                },
                suggested_backend="docling",
                suggested_page_range=_page_range(table, padding=1),
            )

    high_count = sum(issue.risk == "high" for issue in issues)
    medium_count = sum(issue.risk == "medium" for issue in issues)
    status = (
        "fail"
        if any(issue.kind in {"empty_table", "table_structure", "duplicate_paragraph"} for issue in issues)
        else ("review" if issues else "pass")
    )
    stats = {
        "paragraphs": len(paragraphs),
        "tables": len(tables),
        "cross_page_paragraphs": sum(1 for p in paragraphs if p.page_end and p.page_end > p.page),
        "cross_page_tables": sum(1 for t in tables if t.page_end and t.page_end > t.page),
        "issues": len(issues),
        "high_risk": high_count,
        "medium_risk": medium_count,
        "low_risk": len(issues) - high_count - medium_count,
    }
    return ParseQAReport(status=status, stats=stats, issues=issues, markdown=document_to_markdown(document))


__all__ = ["ParseQAIssue", "ParseQAReport", "document_to_markdown", "inspect_document"]
