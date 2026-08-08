"""预审核路由 — 上传/预审核/确认/拒绝/重跑

后台任务逻辑已拆分到 app.services.review_runner。
"""

import asyncio
import hashlib
import json
import logging
import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.routes import _state
from app.services.review_runner import run_pre_review as _run_pre_review

log = logging.getLogger("rag_demo.routes.review")

router = APIRouter()


@router.get("/review/active")
async def get_active_review():
    """获取当前活跃的预审核任务（如果有）"""
    for task_id, task in _state.app.review_tasks.items():
        if task["status"] in ("pending", "running"):
            return {
                "task_id": task_id,
                "status": task["status"],
                "filename": task["filename"],
                "file_hash": task.get("file_hash", ""),
                "progress": task["progress"],
                "current_step": task["current_step"],
                "all_steps": task.get("all_steps", []),
                "completed_steps": task.get("completed_steps", []),
            }
        if task["status"] == "done" and task.get("result"):
            if task_id not in _state.app.confirmed_or_rejected:
                return {
                    "task_id": task_id,
                    "status": "done",
                    "filename": task["filename"],
                    "file_hash": task.get("file_hash", ""),
                    "result": task["result"],
                }
    return {"task_id": None}


@router.post("/review/{task_id}/cancel")
async def cancel_review(task_id: str):
    """取消正在进行的预审核"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task["status"] in ("pending", "running"):
        task["status"] = "cancelled"
        task["current_step"] = "已取消"
        if os.path.exists(task["filepath"]):
            os.remove(task["filepath"])
        return {"message": "已取消"}
    return {"message": "任务已结束，无法取消"}


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), choice: str = Form("")):
    """
    上传文档 → 启动预审核流程

    若同名不同内容文档已存在，第一次返回 needs_choice=True，
    前端带 choice=overwrite|coexist 重新上传。
    """
    filename = file.filename
    content = await file.read()

    new_sha = hashlib.sha256(content).hexdigest()

    existing = _state.app.doc_store.get_document(filename)
    if existing and existing.status == "active":
        if existing.file_hash == new_sha:
            raise HTTPException(
                status_code=409, detail=f"文档 '{filename}' 内容完全相同，请勿重复上传"
            )
        if not choice:
            return {
                "needs_choice": True,
                "filename": filename,
                "file_hash": new_sha,
                "existing": {
                    "filename": existing.filename,
                    "doc_id": existing.doc_id,
                    "file_hash": existing.file_hash,
                    "added_at": existing.added_at,
                    "paragraph_count": existing.paragraph_count,
                },
                "options": [
                    {"id": "overwrite", "label": "覆盖已有文档"},
                    {"id": "coexist", "label": "作为新版本并存"},
                ],
            }
        if choice == "overwrite":
            _state.app.doc_store.remove_document(existing.doc_id)
            log.info(f"用户选择覆盖: 已删除旧文档 {existing.doc_id}")

    file_ext = os.path.splitext(filename)[1] or ".bin"
    safe_filename = f"{new_sha}{file_ext}"
    filepath = os.path.join(_state.app.upload_dir, safe_filename)

    # 原子写入
    tmp_filepath = filepath + ".tmp"
    with open(tmp_filepath, "wb") as f:
        f.write(content)
    os.replace(tmp_filepath, filepath)
    log.info(f"上传保存: {filename} -> {safe_filename} ({len(content)} bytes)")

    task_id = uuid.uuid4().hex[:12]
    _state.app.review_tasks[task_id] = {
        "status": "pending",
        "filename": filename,
        "filepath": filepath,
        "file_hash": new_sha,
        "progress": 0,
        "current_step": f"正在处理: {filename}",
        "steps": [],
        "result": None,
    }

    await asyncio.sleep(0.1)
    asyncio.create_task(_run_pre_review(task_id))
    return {"task_id": task_id, "filename": filename, "file_hash": new_sha}


@router.get("/review/{task_id}/progress")
async def review_progress(task_id: str):
    """SSE 进度推送"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        task = _state.app.review_tasks[task_id]
        last_step_count = -1
        while True:
            cur_step_count = len(task["steps"])
            changed = cur_step_count != last_step_count or task["status"] in (
                "done", "error", "cancelled",
            )
            if changed:
                last_step_count = cur_step_count
                import time as _t

                completed = task.get("completed_steps", [])
                current_elapsed = 0
                if (
                    completed
                    and "started_at" in completed[-1]
                    and "elapsed" not in completed[-1]
                ):
                    current_elapsed = round(_t.time() - completed[-1]["started_at"], 1)
                payload = {
                    "status": task["status"],
                    "progress": task["progress"],
                    "current_step": task["current_step"],
                    "steps": task["steps"],
                    "all_steps": task.get("all_steps", []),
                    "completed_steps": task.get("completed_steps", []),
                    "current_elapsed": current_elapsed,
                    "result": task["result"],
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if task["status"] in ("done", "error", "cancelled"):
                    break
            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/review/{task_id}/confirm")
async def confirm_review(task_id: str):
    """人工确认入库"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task["status"] != "done":
        raise HTTPException(
            status_code=400, detail=f"预审核未完成（当前状态: {task['status']}）"
        )

    filename = task["filename"]
    filepath = task["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="上传文件已丢失，请重新上传文档")

    with open(filepath, "rb") as _f:
        file_hash = hashlib.sha256(_f.read()).hexdigest()
    existing = _state.app.doc_store.get_document(f"{filename}#{file_hash[-8:].upper()}")
    if existing and existing.status == "active":
        raise HTTPException(
            status_code=409, detail=f"文档 '{filename}' 已入库（相同内容）"
        )

    try:
        meta = await asyncio.to_thread(
            _state.app.doc_store.add_document, filepath, task["filename"]
        )
    except Exception as e:
        log.error(f"入库失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"入库失败: {e!s}")

    task["status"] = "confirmed"
    _state.app.confirmed_or_rejected.add(task_id)
    log.info(f"入库成功: {meta.filename} ({meta.paragraph_count} paras) @ {filepath}")
    return {
        "message": "文档已入库",
        "filename": meta.filename,
        "paragraphs": meta.paragraph_count,
    }


@router.post("/review/{task_id}/reject")
async def reject_review(task_id: str):
    """人工拒绝入库"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    task["status"] = "rejected"
    _state.app.confirmed_or_rejected.add(task_id)
    if os.path.exists(task["filepath"]):
        os.remove(task["filepath"])
    return {"message": "已拒绝，文档不入库"}


@router.post("/review/{task_id}/rerun")
async def rerun_review(task_id: str):
    """强制重新执行预审核（忽略缓存）"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]

    if task["status"] in ("confirmed", "rejected"):
        raise HTTPException(
            status_code=400, detail=f"任务已{task['status']}，无法重跑",
        )

    filepath = task.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=400, detail="上传文件已不存在，请重新上传")

    from app.services.utils import compute_sha256

    try:
        file_md5 = compute_sha256(filepath)
        cached_path = os.path.join(_state.app.review_result_cache, f"{file_md5}.json")
        if os.path.exists(cached_path):
            os.remove(cached_path)
            log.info(f"🗑️ 已删除预审核结果缓存: {task['filename']} (SHA256={file_md5[-8:].upper()})")
    except Exception as e:
        log.warning(f"删除预审核缓存失败: {e}")

    task["status"] = "pending"
    task["progress"] = 0
    task["current_step"] = f"强制重新执行预审核: {task['filename']}"
    task["result"] = None
    task["steps"] = []
    task["completed_steps"] = []
    task["parsed_paragraphs"] = []
    task["all_steps"] = []
    _state.app.confirmed_or_rejected.discard(task_id)

    await asyncio.sleep(0.1)
    asyncio.create_task(_run_pre_review(task_id))
    return {"message": "已开始重新执行预审核"}


@router.get("/review/pdf")
async def get_review_pdf(task_id: str):
    """返回预审核文件的原始 PDF"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    filepath = task.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(filepath, media_type="application/pdf")


@router.get("/review/page")
async def get_review_page(task_id: str, page: int = 1, highlight: str = ""):
    """获取预审核文件指定页的 PNG 图片"""
    from urllib.parse import unquote

    from app.services.page_renderer import PageRenderer

    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    filepath = task["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(_state.app.cache_dir, "page_cache"))
    png_path = await asyncio.to_thread(
        renderer.get_page, filepath, page, unquote(highlight)
    )
    return FileResponse(png_path, media_type="image/png")
