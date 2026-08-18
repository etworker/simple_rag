"""文档管理路由 — 列表/删除/清除/PDF/页面预览"""

import asyncio
import os

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse
from loguru import logger as log

from app.routes import _state
from app.routes._state import init  # noqa: F401 - re-export for main.py

router = APIRouter()


def _resolve_document(s, doc_id: str = "", name: str = ""):
    """优先按唯一 doc_id 定位；name 仅作为兼容参数。"""
    from urllib.parse import unquote

    requested = unquote(doc_id or name)
    return requested, s.doc_store.get_document(requested)


@router.get("/list")
async def list_documents():
    """列出可管理文档；问答/预审核仍由 DocStore.list_documents() 只取 active。"""
    s = _state.app
    docs = s.doc_store.list_all_documents()
    active_count = sum(1 for d in docs if d.status == "active")
    return {
        "documents": [
            {
                "filename": d.filename,
                "doc_id": d.doc_id,
                "file_hash": d.file_hash,
                "family_id": getattr(d, "family_id", ""),
                "paragraph_count": d.paragraph_count,
                "table_count": d.table_count,
                "page_count": d.page_count,
                "char_count": d.char_count,
                "added_at": d.added_at,
                "status": d.status,
                "is_primary": getattr(d, "is_primary", d.status == "active"),
                "version": getattr(d, "version", ""),
                "label": getattr(d, "label", ""),
            }
            for d in docs
        ],
        "total": len(docs),
        "active_total": active_count,
        "inactive_total": len(docs) - active_count,
        "total_paragraphs": s.doc_store.total_paragraphs,
    }


@router.get("/review/paragraphs")
async def get_pending_paragraphs(file: str = "", task_id: str = ""):
    """获取正在预审核的新文档段落（尚未入库）。"""
    from urllib.parse import unquote

    s = _state.app
    file = unquote(file)
    if task_id:
        task = s.review_tasks.get(task_id)
        if not task or task_id in s.confirmed_or_rejected:
            raise HTTPException(status_code=404, detail="审核任务不存在或已结束")
        if file and task.get("filename") != file:
            raise HTTPException(status_code=400, detail="任务与文档不匹配")
        return {"file": task.get("filename", file), "paragraphs": task.get("parsed_paragraphs", [])}

    # 兼容旧客户端：按 filename 匹配活跃 task。
    for tid, task in s.review_tasks.items():
        if tid in s.confirmed_or_rejected:
            continue
        if task.get("filename") == file:
            return {"file": file, "paragraphs": task.get("parsed_paragraphs", [])}
    # 兼容：fallback 到 source_file 字段匹配（仅匹配活跃 task）
    for tid, task in s.review_tasks.items():
        if tid in s.confirmed_or_rejected:
            continue
        paras = task.get("parsed_paragraphs", [])
        for p in paras:
            if p["source_file"] == file:
                return {"file": file, "paragraphs": paras}
    return {"file": file, "paragraphs": []}


@router.get("/paragraphs")
async def get_document_paragraphs(name: str):
    """获取已入库文档的段落列表（用于非 PDF 文档的文本预览）"""
    from urllib.parse import unquote

    s = _state.app
    filename = unquote(name)
    paras = s.doc_store.get_paragraphs_by_file(filename)
    return {
        "file": filename,
        "paragraphs": [
            {
                "text": p.text,
                "page": p.page,
                "chapter": p.chapter,
                "chapter_title": p.chapter_title,
                "location": p.location,
            }
            for p in paras
        ],
    }


@router.get("/pdf")
async def get_document_pdf(doc_id: str = "", name: str = ""):
    """返回原始 PDF；按 doc_id 精确定位，历史版本也允许预览。"""
    s = _state.app
    _, meta = _resolve_document(s, doc_id, name)
    if not meta or meta.status in ("deleted", "rejected"):
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(meta.filepath, media_type="application/pdf")


@router.get("/info")
async def get_document_info(doc_id: str = "", name: str = ""):
    """获取文档基本信息（页数等），支持 active/inactive。"""
    from app.services.page_renderer import PageRenderer

    s = _state.app
    _, meta = _resolve_document(s, doc_id, name)
    if not meta or meta.status in ("deleted", "rejected"):
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(s.cache_dir, "page_cache"))
    page_count = await asyncio.to_thread(renderer.get_page_count, meta.filepath)
    return {"filename": meta.filename, "doc_id": meta.doc_id, "page_count": page_count}


@router.get("/page")
async def get_document_page(doc_id: str = "", name: str = "", page: int = 1, highlight: str = ""):
    """获取指定文档页面；doc_id 保证同名历史版本不会串页。"""
    from urllib.parse import unquote

    from app.services.page_renderer import PageRenderer

    s = _state.app
    _, meta = _resolve_document(s, doc_id, name)
    if not meta or meta.status in ("deleted", "rejected"):
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(s.cache_dir, "page_cache"))
    png_path = await asyncio.to_thread(renderer.get_page, meta.filepath, page, unquote(highlight))
    return FileResponse(
        png_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.post("/primary")
async def set_primary_document(doc_id: str = Form(...)):
    """将历史版本设为当前版本，同族其他版本自动转为 inactive。"""
    s = _state.app
    try:
        meta = await asyncio.to_thread(s.doc_store.set_primary_document, doc_id)
    except Exception as e:
        log.error(f"切换当前文档版本失败: {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="切换当前版本失败") from e
    if not meta:
        raise HTTPException(status_code=404, detail="文档不存在或不可激活")
    return {"message": "已设为当前版本", "doc_id": meta.doc_id, "filename": meta.filename}


@router.delete("/remove/{filename}")
async def delete_document(filename: str):
    """删除已入库文档"""
    s = _state.app
    success = s.doc_store.remove_document(filename)
    if not success:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"message": f"已删除: {filename}"}


@router.post("/label")
async def update_document_label(doc_id: str = Form(...), label: str = Form("")):
    """更新文档的补充描述（label/tag）"""
    s = _state.app
    ok = s.doc_store.update_label(doc_id, label)
    if not ok:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"message": "已更新", "label": (label or "").strip()[:60]}


@router.post("/clear")
async def clear_all_documents():
    """清空知识库（删除所有文档、缓存、向量，完全从0开始）"""
    from app.services.utils import clear_cache_dir

    s = _state.app
    count = await asyncio.to_thread(s.doc_store.clear_all)
    s.review_tasks.clear()
    clear_cache_dir(s.review_cache_path)
    clear_cache_dir(s.review_result_cache)
    s.confirmed_or_rejected.clear()

    for sub in ("parse_cache", "page_cache", "vector_cache"):
        clear_cache_dir(os.path.join(s.cache_dir, sub))

    log.info(f"🗑️ 知识库完全重置: 清空 {count} 篇文档 + 全部缓存")
    return {"message": f"知识库已完全重置，共移除 {count} 篇文档，所有缓存已清除"}
