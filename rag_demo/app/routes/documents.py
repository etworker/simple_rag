"""文档管理路由 — 列表/删除/清除/PDF/页面预览"""

import asyncio
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.routes import _state
from app.routes._state import init  # noqa: F401 - re-export for main.py

router = APIRouter()


@router.get("/list")
async def list_documents():
    """列出所有已入库文档"""
    docs = _state._doc_store.list_documents()
    return {
        "documents": [
            {
                "filename": d.filename,
                "doc_id": getattr(d, "doc_id", d.filename),
                "file_hash": getattr(d, "file_hash", ""),
                "paragraph_count": d.paragraph_count,
                "table_count": d.table_count,
                "page_count": getattr(d, "page_count", 0),
                "char_count": getattr(d, "char_count", 0),
                "added_at": d.added_at,
                "status": d.status,
            }
            for d in docs
        ],
        "total": len(docs),
        "total_paragraphs": _state._doc_store.total_paragraphs,
    }


@router.get("/review/paragraphs")
async def get_pending_paragraphs(file: str):
    """获取正在预审核的新文档段落（尚未入库）"""
    from urllib.parse import unquote

    file = unquote(file)
    for task in _state._review_tasks.values():
        paras = task.get("parsed_paragraphs", [])
        for p in paras:
            if p["source_file"] == file:
                return {"file": file, "paragraphs": paras}
    return {"file": file, "paragraphs": []}


@router.get("/pdf")
async def get_document_pdf(name: str):
    """返回原始 PDF 文件"""
    from urllib.parse import unquote

    filename = unquote(name)
    meta = _state._doc_store.get_document(filename)
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

    filename = unquote(name)
    meta = _state._doc_store.get_document(filename)
    if not meta or meta.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(_state._cache_dir, "page_cache"))
    page_count = await asyncio.to_thread(renderer.get_page_count, meta.filepath)
    return {"filename": filename, "page_count": page_count}


@router.get("/page")
async def get_document_page(name: str, page: int = 1, highlight: str = ""):
    """获取已入库文档指定页的 PNG 图片"""
    from urllib.parse import unquote

    from app.services.page_renderer import PageRenderer

    filename = unquote(name)
    meta = _state._doc_store.get_document(filename)
    if not meta or meta.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(_state._cache_dir, "page_cache"))
    png_path = await asyncio.to_thread(
        renderer.get_page, meta.filepath, page, unquote(highlight)
    )
    return FileResponse(png_path, media_type="image/png")


@router.delete("/remove/{filename}")
async def delete_document(filename: str):
    """删除已入库文档"""
    success = _state._doc_store.remove_document(filename)
    if not success:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"message": f"已删除: {filename}"}


@router.post("/clear")
async def clear_all_documents():
    """清空知识库（删除所有文档、缓存、向量，完全从0开始）"""
    import shutil

    count = await asyncio.to_thread(_state._doc_store.clear_all)
    _state._review_tasks.clear()
    if os.path.exists(_state._REVIEW_CACHE_PATH):
        os.remove(_state._REVIEW_CACHE_PATH)
    if os.path.exists(_state._REVIEW_RESULT_CACHE):
        shutil.rmtree(_state._REVIEW_RESULT_CACHE, ignore_errors=True)
    os.makedirs(_state._REVIEW_RESULT_CACHE, exist_ok=True)
    _state._confirmed_or_rejected.clear()
    parse_cache_dir = os.path.join(_state._cache_dir, "parse_cache")
    if os.path.exists(parse_cache_dir):
        shutil.rmtree(parse_cache_dir, ignore_errors=True)
    os.makedirs(parse_cache_dir, exist_ok=True)
    page_cache_dir = os.path.join(_state._cache_dir, "page_cache")
    if os.path.exists(page_cache_dir):
        shutil.rmtree(page_cache_dir, ignore_errors=True)
    os.makedirs(page_cache_dir, exist_ok=True)
    vector_cache_dir = os.path.join(_state._cache_dir, "vector_cache")
    if os.path.exists(vector_cache_dir):
        shutil.rmtree(vector_cache_dir, ignore_errors=True)
    os.makedirs(vector_cache_dir, exist_ok=True)
    import logging

    logging.getLogger("rag_demo.routes").info(
        f"🗑️ 知识库完全重置: 清空 {count} 篇文档 + 全部缓存"
    )
    return {"message": f"知识库已完全重置，共移除 {count} 篇文档，所有缓存已清除"}
