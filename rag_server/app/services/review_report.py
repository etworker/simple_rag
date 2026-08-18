"""离线预审核/比对报告生成。"""

from __future__ import annotations

from datetime import datetime
from html import escape


def _s(value, fallback="") -> str:
    text = str(value if value is not None else fallback)
    return escape(text, quote=True)


def _raw(value, fallback="（无）") -> str:
    text = str(value or "").strip()
    return _s(text, fallback=fallback) if text else _s(fallback)


def _normalize_sides(value: str, b_tag: str) -> str:
    """把 LLM 习惯生成的 A/B 文案统一为 N/Bn。"""
    text = str(value or "")
    for old, new in (
        ("文档 A", "N"),
        ("文档A", "N"),
        ("文档 a", "N"),
        ("文档a", "N"),
        ("A文档", "N"),
        ("文档 B", b_tag),
        ("文档B", b_tag),
        ("文档 b", b_tag),
        ("文档b", b_tag),
        ("B文档", b_tag),
    ):
        text = text.replace(old, new)
    return text


def _doc_name(filename: str, label: str = "", file_hash: str = "") -> str:
    result = filename or "（未命名文档）"
    if label:
        result += f" [{label}]"
    if file_hash:
        result += f" [{file_hash[-8:].upper()}]"
    return result


def _location_table(old_location: str, new_location: str) -> str:
    return (
        '<table class="locations"><tr><th>版本</th><th>定位</th></tr>'
        f"<tr><td>旧版 / 对比文档</td><td>{_raw(old_location)}</td></tr>"
        f"<tr><td>新版 / N</td><td>{_raw(new_location)}</td></tr></table>"
    )


def _table_details(change: dict) -> str:
    table_name = change.get("table_name") or ""
    cells = change.get("cell_changes") or []
    if not table_name and not cells:
        return ""
    table_name = table_name or str(change.get("section", "")).removeprefix("表格: ") or "未命名表格"
    context = f"<p class=\"table-context\"><b>表格：</b>{_s(table_name)}"
    if change.get("row_index"):
        context += f"　<b>数据行：</b>第 {_s(change.get('row_index'))} 行"
    if change.get("row_key"):
        context += f"　<b>行标识：</b>{_s(change.get('row_key'))}"
    context += "</p>"
    if not cells:
        return f'<div class="table-details">{context}</div>'
    rows = []
    for cell in cells:
        rows.append(
            f"<tr><td>{_s(cell.get('column', ''))}</td>"
            f"<td>{_s(cell.get('old_value', ''))}</td>"
            f"<td>{_s(cell.get('new_value', ''))}</td></tr>"
        )
    return (
        f'<div class="table-details">{context}'
        '<table><tr><th>列</th><th>旧版值</th><th>新版值</th></tr>'
        + "".join(rows)
        + "</table></div>"
    )


def _change_card(change: dict, b_tag: str, index: int, minor: bool = False) -> str:
    change_type = {"added": "新增", "removed": "删除", "modified": "修改"}.get(
        change.get("type", ""), change.get("type", "变更")
    )
    summary = change.get("summary") or (
        f"[新增] {change.get('new_text', '')[:160]}"
        if change.get("type") == "added"
        else f"[删除] {change.get('old_text', '')[:160]}"
        if change.get("type") == "removed"
        else "[修改] 内容发生变化"
    )
    category = change.get("category") or "content"
    title = f"{index}. {change_type}{'（细微差异）' if minor else ''}"
    return (
        '<article class="change-card">'
        f'<h4>{_s(title)} <span class="badge">{_s(category)}</span></h4>'
        f'<p class="summary">{_raw(summary)}</p>'
        f"{_location_table(change.get('old_location', ''), change.get('location', ''))}"
        f"{_table_details(change)}"
        '<div class="text-grid">'
        f'<div><h5>旧版 / {b_tag}</h5><pre>{_raw(change.get("old_text"))}</pre></div>'
        '<div><h5>新版 / N</h5><pre>'
        f'{_raw(change.get("new_text"))}</pre></div>'
        '</div></article>'
    )


def build_review_report_html(task_id: str, task: dict, documents: list) -> str:
    """根据指定审核任务生成不依赖服务器的单文件 HTML。"""
    result = task.get("result") or {}
    new_filename = task.get("filename") or result.get("new_filename", "")
    new_label = task.get("label") or result.get("new_doc_label", "")
    new_hash = task.get("file_hash", "")
    groups = result.get("compare_groups") or []
    doc_by_id = {getattr(doc, "doc_id", ""): doc for doc in documents}
    group_tags = {getattr(doc, "doc_id", ""): f"B{i + 1}" for i, doc in enumerate(documents)}
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_status = result.get("message") or task.get("current_step") or task.get("status", "")
    conclusion = "通过" if result.get("is_safe") else "发现差异/矛盾"

    doc_rows = []
    for i, doc in enumerate(documents, 1):
        tag = f"B{i}"
        doc_rows.append(
            "<tr>"
            f"<td><strong>{tag}</strong></td>"
            f"<td>{_s(_doc_name(doc.filename, doc.label, doc.file_hash))}</td>"
            f"<td>{_s(doc.doc_id)}</td><td>{_s(doc.page_count)}</td>"
            "</tr>"
        )

    group_sections = []
    for group_index, group in enumerate(groups, 1):
        doc_id = group.get("doc_id", "")
        doc = doc_by_id.get(doc_id)
        b_tag = group_tags.get(doc_id, f"B{group_index}")
        filename = group.get("doc_filename") or getattr(doc, "filename", "")
        label = group.get("label") or getattr(doc, "label", "")
        file_hash = group.get("file_hash") or getattr(doc, "file_hash", "")
        compare_type = "版本差异" if group.get("compare_type") == "version_diff" else "跨文档矛盾"
        group_title = _doc_name(filename, label, file_hash)
        body = []
        changes = group.get("version_changes") or []
        minor_changes = group.get("minor_changes") or []
        inconsistencies = group.get("inconsistencies") or []
        for i, change in enumerate(changes, 1):
            body.append(_change_card(change, b_tag, i))
        for i, change in enumerate(minor_changes, 1):
            body.append(_change_card(change, b_tag, i, minor=True))
        for i, inconsistency in enumerate(inconsistencies, 1):
            point = _normalize_sides(inconsistency.get("point", "内容差异"), b_tag)
            n_says = _normalize_sides(inconsistency.get("doc_a_says", ""), b_tag)
            b_says = _normalize_sides(inconsistency.get("doc_b_says", ""), b_tag)
            body.append(
                '<article class="change-card conflict-card">'
                f"<h4>{i}. {_s(point)}</h4>"
                f'<p class="summary">相似度：{_s(inconsistency.get("similarity", "—"))}</p>'
                '<div class="text-grid">'
                f'<div><h5>N 新文档 · {_raw(inconsistency.get("doc_a_location"))}</h5><pre>{_raw(n_says)}</pre></div>'
                f'<div><h5>{b_tag} 对比文档 · {_raw(inconsistency.get("doc_b_location"))}</h5><pre>{_raw(b_says)}</pre></div>'
                '</div></article>'
            )
        if not body:
            body.append('<p class="empty">该文档未发现差异或矛盾。</p>')
        group_sections.append(
            '<section class="group">'
            f'<h3>{_s(b_tag)} · {_s(compare_type)} · {_s(group_title)}</h3>'
            f'<p class="group-meta">相似度：{_s(group.get("similarity", "—"))}　状态：{_s(group.get("status", ""))}</p>'
            + "".join(body)
            + "</section>"
        )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>文档预审核与比对报告</title>
<style>
:root{{--bg:#f4f6f8;--card:#fff;--line:#dfe3e8;--text:#20252b;--muted:#66717d;--primary:#1769aa;--warn:#a15c00}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 22px 60px}} h1{{margin:0 0 8px;font-size:26px}} h2{{margin:28px 0 12px;border-bottom:2px solid var(--primary);padding-bottom:6px}} h3{{margin:0 0 4px;color:var(--primary)}} h4{{margin:0 0 8px;font-size:15px}} h5{{margin:0 0 5px;color:var(--muted);font-size:12px}}
.card,.group,.change-card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px #0000000b}}
.meta,.locations{{width:100%;border-collapse:collapse}} .meta td,.locations td,.locations th{{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}} .meta td:first-child,.locations th{{width:150px;background:#f8fafb;color:var(--muted);font-weight:600}}
.summary{{margin:5px 0 10px;color:#333;font-weight:600}} .group-meta{{margin:0 0 10px;color:var(--muted)}} .badge{{font-size:11px;color:var(--muted);border:1px solid var(--line);padding:1px 6px;border-radius:10px;font-weight:400}}
.table-details{{margin:10px 0;padding:9px 10px;background:#f8fafb;border:1px solid var(--line);border-radius:5px}}.table-context{{margin:0 0 7px}}.table-details table{{width:100%;border-collapse:collapse}}.table-details th,.table-details td{{border:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top;word-break:break-word}}.table-details th{{color:var(--muted);background:#fff;font-weight:600}} .text-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}} pre{{white-space:pre-wrap;word-break:break-word;background:#f8fafb;border:1px solid var(--line);border-radius:5px;padding:10px;margin:0;min-height:54px;font:13px/1.6 "Microsoft YaHei",sans-serif}} .conflict-card{{border-left:4px solid var(--warn)}} .empty{{color:var(--muted)}} footer{{margin-top:28px;color:var(--muted);font-size:12px}}
@media(max-width:760px){{main{{padding:18px 10px}}.text-grid{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>文档预审核与比对报告</h1><p class="group-meta">生成时间：{generated_at}　任务：{_s(task_id)}</p>
<h2>一、审核对象</h2><div class="card"><table class="meta">
<tr><td>新上传文档</td><td><strong>N</strong> · {_s(_doc_name(new_filename, new_label, new_hash))}</td></tr>
<tr><td>文件哈希</td><td>{_s(new_hash)}</td></tr><tr><td>审核状态</td><td>{_s(task.get('status', ''))}</td></tr>
<tr><td>审核结论</td><td>{_s(conclusion)}；{_s(safe_status)}</td></tr></table></div>
<h2>二、知识库对比文档</h2><div class="card"><table class="meta"><tr><th>编号</th><th>文档</th><th>唯一 ID</th><th>页数</th></tr>{''.join(doc_rows) or '<tr><td colspan="4">无知识库文档</td></tr>'}</table></div>
<h2>三、逐文档比对结果</h2>{''.join(group_sections) or '<div class="card empty">没有可用的比对结果。</div>'}
<footer>本报告为离线静态 HTML，不依赖服务器、脚本、图片或外部网络资源。报告中的 N、Bn 与审核时的文档唯一 ID 一一对应。</footer>
</main></body></html>"""
