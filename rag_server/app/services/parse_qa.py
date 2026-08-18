"""可选的 LLM 解析质量审查服务。

规则候选由 ``doc_parser.qa`` 生成，本模块只负责把有限高风险候选交给现有
``version_diff.call_llm_json``，不修改原始 Document 或解析缓存。
"""

from __future__ import annotations

import json
from typing import Any

from doc_parser.models import Document, Paragraph, Table
from doc_parser.qa import ParseQAIssue, ParseQAReport, inspect_document
from loguru import logger as log


def _item_markdown(item: Paragraph | Table, kind: str) -> str:
    page = getattr(item, "page", 0)
    page_end = getattr(item, "page_end", 0) or page
    metadata = f"<!-- type={kind} index={item.index} page={page} page_end={page_end} -->"
    return f"{metadata}\n{item.to_markdown()}"


def _candidate_context(document: Document, issue: ParseQAIssue) -> str:
    if issue.paragraph_index:
        for paragraph in document.paragraphs:
            if paragraph.index == issue.paragraph_index:
                return _item_markdown(paragraph, "paragraph")
    if issue.table_index:
        for table in document.tables:
            if table.index == issue.table_index:
                return _item_markdown(table, "table")
    return json.dumps(issue.evidence, ensure_ascii=False)


def _build_prompt(document: Document, candidates: list[ParseQAIssue]) -> str:
    payload = []
    for issue in candidates:
        payload.append(
            {
                "issue_id": issue.issue_id,
                "kind": issue.kind,
                "rule_risk": issue.risk,
                "page": issue.page,
                "page_end": issue.page_end,
                "suggested_page_range": issue.suggested_page_range,
                "rule_message": issue.message,
                "evidence": issue.evidence,
                "markdown": _candidate_context(document, issue),
            }
        )
    return (
        "你是 PDF 解析质量审查器。请只审查给出的解析 Markdown 片段和规则证据。\n"
        "目标是发现明显的段落断裂、重复提取、表格错列、跨页表格误合并或漏合并迹象。\n"
        "重要限制：你没有看到原始 PDF 页面，不能仅凭 Markdown 断言原文一定缺失；证据不足时返回 review。\n"
        "对每个 issue_id 必须返回一条结果，不得新增或遗漏 issue_id。\n"
        "只返回 JSON 数组，不要 Markdown，不要解释数组之外的内容：\n"
        '[{"issue_id":"...","decision":"pass|review|fail",'
        '"is_parse_error":false,"confidence":0.0,"reason":"...",'
        '"repair_backend":"none|docling|mineru|manual","page_range":[1,2]}]\n\n'
        f"候选解析结果：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _apply_llm_results(report: ParseQAReport, candidates: list[ParseQAIssue], results: list[dict[str, Any]]) -> bool:
    by_id = {item.issue_id: item for item in candidates}
    seen: set[str] = set()
    allowed_decisions = {"pass", "review", "fail"}
    allowed_backends = {"none", "docling", "mineru", "manual"}
    for result in results:
        if not isinstance(result, dict):
            return False
        issue_id = result.get("issue_id")
        if issue_id not in by_id or issue_id in seen:
            return False
        decision = result.get("decision")
        if decision not in allowed_decisions:
            return False
        is_parse_error = result.get("is_parse_error")
        if not isinstance(is_parse_error, bool):
            return False
        confidence = result.get("confidence")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))):
            return False
        backend = result.get("repair_backend", "none")
        if backend not in allowed_backends:
            return False
        page_range = result.get("page_range")
        if not (
            isinstance(page_range, list)
            and len(page_range) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in page_range)
            and page_range[0] <= page_range[1]
        ):
            return False

        issue = by_id[issue_id]
        issue.llm_decision = decision
        issue.llm_reason = str(result.get("reason", ""))
        issue.llm_confidence = float(confidence) if confidence is not None else None
        issue.is_parse_error = is_parse_error
        if backend != "none":
            issue.suggested_backend = backend
        issue.suggested_page_range = page_range
        seen.add(issue_id)
    return seen == set(by_id)


def review_document_parse_quality(
    document: Document,
    llm_config: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> ParseQAReport:
    """执行规则检查，并可选地用 LLM 审查有限高风险候选。

    LLM 失败时保留原始 Document 和规则报告，只将 ``incomplete`` 置为 True。
    """
    cfg = config or {}
    report = inspect_document(document, cfg)
    candidates = report.llm_candidates(int(cfg.get("max_candidates", 20)))
    report.stats["llm_candidates"] = len(candidates)
    report.stats["llm_checked"] = 0
    report.stats["llm_parse_errors"] = 0
    report.stats["llm_failures"] = 0
    if not candidates:
        return report

    from version_diff.llm_util import call_llm_json

    batch_size = max(1, int(cfg.get("batch_size", 10)))
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        results = call_llm_json(
            _build_prompt(document, batch),
            llm_config,
            max_retries=int(cfg.get("max_retries", 2)),
            retry_backoff=float(cfg.get("retry_backoff", 1.0)),
        )
        if results is None or not _apply_llm_results(report, batch, results):
            report.incomplete = True
            log.warning("解析质量 LLM 审查失败或返回不完整，保留规则报告: {}", [x.issue_id for x in batch])
            continue
        report.stats["llm_checked"] += len(batch)

    checked_issues = [issue for issue in report.issues if issue.llm_decision]
    report.stats["llm_parse_errors"] = sum(1 for issue in checked_issues if issue.is_parse_error)
    report.stats["llm_failures"] = sum(1 for issue in checked_issues if issue.llm_decision == "fail")
    decisions = [issue.llm_decision for issue in checked_issues]
    has_parse_error = any(issue.is_parse_error for issue in checked_issues)
    if report.incomplete:
        # 未覆盖或未成功解析的候选不能被当作通过。
        report.status = "review"
    elif "fail" in decisions or has_parse_error:
        report.status = "fail"
    elif len(candidates) == len(report.issues) and all(decision == "pass" for decision in decisions):
        # 所有规则候选均被 LLM 判定为非解析错误时，允许降级规则层的保守告警。
        report.status = "pass"
    elif report.status == "fail":
        report.status = "fail"
    elif report.issues or "review" in decisions:
        report.status = "review"
    else:
        report.status = "pass"
    return report


__all__ = ["review_document_parse_quality"]
