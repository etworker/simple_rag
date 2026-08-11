"""文档管理路由 — 列表/删除/清除/PDF/页面预览"""

import asyncio
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from loguru import logger as log

from app.routes import _state
from app.routes._state import init  # noqa: F401 - re-export for main.py

router = APIRouter()


@router.get("/list")
async def list_documents():
    """列出所有已入库文档"""
    s = _state.app
    docs = s.doc_store.list_documents()
    return {
        "documents": [
            {
                "filename": d.filename,
                "doc_id": d.doc_id,
                "file_hash": d.file_hash,
                "paragraph_count": d.paragraph_count,
                "table_count": d.table_count,
                "page_count": d.page_count,
                "char_count": d.char_count,
                "added_at": d.added_at,
                "status": d.status,
            }
            for d in docs
        ],
        "total": len(docs),
        "total_paragraphs": s.doc_store.total_paragraphs,
    }


@router.get("/review/paragraphs")
async def get_pending_paragraphs(file: str):
    """获取正在预审核的新文档段落（尚未入库）"""
    from urllib.parse import unquote

    s = _state.app
    file = unquote(file)
    # 优先匹配当前活跃的（未确认/拒绝）task 的 filename
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
            {"text": p.text, "page": p.page, "chapter": p.chapter,
             "chapter_title": p.chapter_title, "location": p.location}
            for p in paras
        ],
    }


@router.get("/pdf")
async def get_document_pdf(name: str):
    """返回原始 PDF 文件"""
    from urllib.parse import unquote

    s = _state.app
    filename = unquote(name)
    meta = s.doc_store.get_document(filename)
    if not meta or meta.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(meta.filepath, media_type="application/pdf")


@router.get("/info")
async def get_document_info(name: str):
    """获取文档基本信息（页数等）"""
    from urllib.parse import unquote

    from app.services.page_renderer import PageRenderer

    s = _state.app
    filename = unquote(name)
    meta = s.doc_store.get_document(filename)
    if not meta or meta.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(s.cache_dir, "page_cache"))
    page_count = await asyncio.to_thread(renderer.get_page_count, meta.filepath)
    return {"filename": filename, "page_count": page_count}


@router.get("/page")
async def get_document_page(name: str, page: int = 1, highlight: str = ""):
    """获取已入库文档指定页的 PNG 图片"""
    from urllib.parse import unquote

    from app.services.page_renderer import PageRenderer

    s = _state.app
    filename = unquote(name)
    meta = s.doc_store.get_document(filename)
    if not meta or meta.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(s.cache_dir, "page_cache"))
    png_path = await asyncio.to_thread(
        renderer.get_page, meta.filepath, page, unquote(highlight)
    )
    return FileResponse(png_path, media_type="image/png")


@router.delete("/remove/{filename}")
async def delete_document(filename: str):
    """删除已入库文档"""
    s = _state.app
    success = s.doc_store.remove_document(filename)
    if not success:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"message": f"已删除: {filename}"}


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
