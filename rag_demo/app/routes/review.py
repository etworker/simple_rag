"""预审核路由 — 上传/预审核/确认/拒绝/重跑"""

import asyncio
import copy
import json
import logging
import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.routes import _state

log = logging.getLogger("rag_demo.routes.review")

router = APIRouter()


@router.get("/review/active")
async def get_active_review():
    """获取当前活跃的预审核任务（如果有）"""
    for task_id, task in _state._review_tasks.items():
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
            if task_id not in _state._confirmed_or_rejected:
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
    if task_id not in _state._review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state._review_tasks[task_id]
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

    import hashlib as _hl

    new_sha = _hl.sha256(content).hexdigest()

    existing = _state._doc_store.get_document(filename)
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
            _state._doc_store.remove_document(existing.doc_id)
            log.info(f"用户选择覆盖: 已删除旧文档 {existing.doc_id}")

    # ★ Fix: 用完整 SHA256 作为文件名，相同内容 → 相同路径 → 天然去重。
    #    记录原始文件名与 hash 的映射：doc_store.DocMeta.filename ↔ file_hash。
    #    无需 uuid8 子目录，原子写入避免孤儿文件。
    file_ext = os.path.splitext(filename)[1] or ".bin"
    safe_filename = f"{new_sha}{file_ext}"
    filepath = os.path.join(_state._upload_dir, safe_filename)

    # 原子写入：先写临时文件再 rename，防止并发同名文件写冲突
    tmp_filepath = filepath + ".tmp"
    with open(tmp_filepath, "wb") as f:
        f.write(content)
    os.replace(tmp_filepath, filepath)
    log.info(f"上传保存: {filename} -> {safe_filename} ({len(content)} bytes)")

    task_id = uuid.uuid4().hex[:12]
    _state._review_tasks[task_id] = {
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
    if task_id not in _state._review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        task = _state._review_tasks[task_id]
        last_step_count = -1
        while True:
            cur_step_count = len(task["steps"])
            changed = cur_step_count != last_step_count or task["status"] in (
                "done",
                "error",
                "cancelled",
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
    if task_id not in _state._review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state._review_tasks[task_id]
    if task["status"] != "done":
        raise HTTPException(
            status_code=400, detail=f"预审核未完成（当前状态: {task['status']}）"
        )

    filename = task["filename"]
    filepath = task["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="上传文件已丢失，请重新上传文档")

    import hashlib as _hl

    with open(filepath, "rb") as _f:
        file_hash = _hl.sha256(_f.read()).hexdigest()
    existing = _state._doc_store.get_document(f"{filename}#{file_hash[-8:].upper()}")
    if existing and existing.status == "active":
        raise HTTPException(
            status_code=409, detail=f"文档 '{filename}' 已入库（相同内容）"
        )

    # ★ Fix: uploads 目录已经是 SHA256 命名（不可变、去重），
    #    直接复用即可。无需 adata 二次复制——相同内容任何时刻路径一致。
    try:
        meta = await asyncio.to_thread(
            _state._doc_store.add_document, filepath, task["filename"]
        )
    except Exception as e:
        log.error(f"入库失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"入库失败: {e!s}")

    task["status"] = "confirmed"
    _state._confirmed_or_rejected.add(task_id)
    log.info(f"入库成功: {meta.filename} ({meta.paragraph_count} paras) @ {filepath}")
    return {
        "message": "文档已入库",
        "filename": meta.filename,
        "paragraphs": meta.paragraph_count,
    }


@router.post("/review/{task_id}/reject")
async def reject_review(task_id: str):
    """人工拒绝入库"""
    if task_id not in _state._review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state._review_tasks[task_id]
    task["status"] = "rejected"
    _state._confirmed_or_rejected.add(task_id)
    if os.path.exists(task["filepath"]):
        os.remove(task["filepath"])
    return {"message": "已拒绝，文档不入库"}


@router.post("/review/{task_id}/rerun")
async def rerun_review(task_id: str):
    """强制重新执行预审核（忽略缓存）"""
    if task_id not in _state._review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state._review_tasks[task_id]

    # 已确认/拒绝的任务不允许重跑（防止并发踩踏丢失确认状态）
    if task["status"] in ("confirmed", "rejected"):
        raise HTTPException(
            status_code=400,
            detail=f"任务已{task['status']}，无法重跑",
        )

    filepath = task.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=400, detail="上传文件已不存在，请重新上传")

    from app.services.utils import compute_sha256

    try:
        file_md5 = compute_sha256(filepath)
        cached_path = os.path.join(_state._REVIEW_RESULT_CACHE, f"{file_md5}.json")
        if os.path.exists(cached_path):
            os.remove(cached_path)
            log.info(
                f"🗑️ 已删除预审核结果缓存: {task['filename']} (SHA256={file_md5[-8:].upper()})"
            )
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
    _state._confirmed_or_rejected.discard(task_id)

    await asyncio.sleep(0.1)
    asyncio.create_task(_run_pre_review(task_id))
    return {"message": "已开始重新执行预审核"}


@router.get("/review/pdf")
async def get_review_pdf(task_id: str):
    """返回预审核文件的原始 PDF"""
    if task_id not in _state._review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state._review_tasks[task_id]
    filepath = task.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(filepath, media_type="application/pdf")


@router.get("/review/page")
async def get_review_page(task_id: str, page: int = 1, highlight: str = ""):
    """获取预审核文件指定页的 PNG 图片"""
    from urllib.parse import unquote

    from app.services.page_renderer import PageRenderer

    if task_id not in _state._review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state._review_tasks[task_id]
    filepath = task["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(_state._cache_dir, "page_cache"))
    png_path = await asyncio.to_thread(
        renderer.get_page, filepath, page, unquote(highlight)
    )
    return FileResponse(png_path, media_type="image/png")


# ============================================================
# 预审核后台任务
# ============================================================


def _load_existing_docs(engine):
    """同步加载已有文档到引擎（在线程中执行）"""
    # ★ Fix: 文件缺失或损坏时跳过，不中断整个预审核流程
    for doc_meta in _state._doc_store.list_documents():
        if not doc_meta.filepath or not os.path.exists(doc_meta.filepath):
            log.warning(f"跳过加载已有文档（文件不存在）: {doc_meta.filename} -> {doc_meta.filepath}")
            continue
        try:
            engine.add(doc_meta.filepath)
        except Exception as e:
            log.error(f"加载已有文档失败（跳过）: {doc_meta.filename}: {e}")


async def _run_pre_review(task_id: str):
    """执行预审核（异步后台任务）"""
    await asyncio.sleep(0.5)
    task = _state._review_tasks[task_id]
    filepath = task["filepath"]

    if not os.path.exists(filepath):
        task["status"] = "error"
        task["current_step"] = "错误: 上传文件已丢失，请重新上传"
        task["result"] = {"error": "上传文件已丢失"}
        return

    # ====== 1. 快速路径：检查预审核结果缓存 ======
    from app.services.utils import compute_sha256

    file_md5 = compute_sha256(filepath)

    cached_result_path = os.path.join(_state._REVIEW_RESULT_CACHE, f"{file_md5}.json")
    if os.path.exists(cached_result_path):
        try:
            with open(cached_result_path, "r", encoding="utf-8") as _f:
                cached = json.load(_f)
            doc_names = sorted(d.filename for d in _state._doc_store.list_documents())
            doc_sig = "|".join(doc_names) + f"|{_state._doc_store.total_paragraphs}"
            cache_doc_sig = cached.get("doc_signature", "")
            if cache_doc_sig != doc_sig:
                log.info(
                    f"📦 缓存已过期（库文档变化），重新执行预审核: {task['filename']}"
                )
            else:
                log.info(
                    f"📦 命中预审核结果缓存: {task['filename']} (SHA256={file_md5[-8:].upper()})"
                )
                task["status"] = "done"
                task["progress"] = 100
                task["current_step"] = "预审核完成（使用缓存）"
                task["result"] = copy.deepcopy(cached.get("result"))
                task["parsed_paragraphs"] = copy.deepcopy(
                    cached.get("parsed_paragraphs", [])
                )
                task["all_steps"] = [
                    {"id": "cache", "label": "读取预审核缓存"},
                    {"id": "done", "label": "完成"},
                ]
                task["completed_steps"] = [
                    {
                        "id": "cache",
                        "message": "读取预审核缓存",
                        "started_at": 0,
                        "elapsed": 0.01,
                    },
                ]
                _state._save_review_cache()
                return
        except Exception as e:
            log.warning(f"预审核缓存加载失败，重新执行: {e}")

    # ====== 2. 慢速路径：完整预审核流程 ======
    all_steps = [
        {"id": "model", "label": "加载向量模型"},
        {"id": "loading", "label": "加载已有文档"},
        {"id": "parsing", "label": "解析文档"},
        {"id": "embedding", "label": "计算语义向量"},
        {"id": "searching", "label": "跨文档语义检索"},
        {"id": "diffing", "label": "计算文本差异"},
        {"id": "judging", "label": "LLM 矛盾判定"},
        {"id": "done", "label": "汇总结果"},
    ]
    task["all_steps"] = all_steps
    task["completed_steps"] = []

    import time as _time

    step_start_time = _time.time()

    def on_progress(step: str, pct: float, msg: str):
        nonlocal step_start_time
        if task["status"] == "cancelled":
            raise InterruptedError("用户取消")
        now = _time.time()
        task["progress"] = int(pct * 100)
        task["current_step"] = msg
        completed_ids = [s["id"] for s in task["completed_steps"]]
        if step not in completed_ids:
            if task["completed_steps"]:
                task["completed_steps"][-1]["elapsed"] = round(now - step_start_time, 1)
            step_start_time = now
            task["completed_steps"].append(
                {"id": step, "message": msg, "started_at": now}
            )
        task["steps"].append({"step": step, "progress": int(pct * 100), "message": msg})

    try:
        task["status"] = "running"
        task["current_step"] = f"开始预审核: {task['filename']}"

        from version_diff import DiffEngine

        engine_config = {
            "embedding": _state._config.get_section("embedding"),
            "llm": _state._config.get_section("llm"),
            "diff": _state._config.get_section("pre_review"),
            "cache": {
                "vector_cache_dir": os.path.join(_state._cache_dir, "vector_cache")
            },
        }
        engine = DiffEngine(config=engine_config)

        on_progress("model", 0.05, "加载向量模型...")
        log.info("开始加载向量模型...")
        await asyncio.to_thread(engine._get_model)
        log.info("向量模型加载完成")

        on_progress("loading", 0.1, "加载已有文档到引擎...")
        log.info(f"开始加载已有文档 ({_state._doc_store.total_documents} 篇)...")
        await asyncio.to_thread(_load_existing_docs, engine)
        log.info("已有文档加载完成")

        from app.services.parse_cache import cached_parse as _parse

        log.info(f"开始解析新文档: {task['filename']}")
        new_doc = await asyncio.to_thread(_parse, filepath)
        log.info(f"新文档解析完成: {len(new_doc.paragraphs)} 段落")
        task["parsed_paragraphs"] = [
            {"text": p.text, "location": p.location, "source_file": new_doc.filename}
            for p in new_doc.paragraphs
        ]

        result = await asyncio.to_thread(
            engine.pre_review, filepath, on_progress=on_progress
        )

        task["progress"] = 100
        task["current_step"] = "预审核完成"
        task["status"] = "done"
        task["result"] = {
            "is_safe": result.is_safe,
            "new_filename": task["filename"],
            "inconsistencies": [
                {
                    "point": inc.point,
                    "doc_a_file": inc.doc_a_file,
                    "doc_a_location": inc.doc_a_location,
                    "doc_a_says": inc.doc_a_says,
                    "doc_b_file": inc.doc_b_file,
                    "doc_b_location": inc.doc_b_location,
                    "doc_b_says": inc.doc_b_says,
                }
                for inc in result.inconsistencies
            ],
            "message": "无矛盾，可安全入库"
            if result.is_safe
            else f"发现 {len(result.inconsistencies)} 处矛盾",
        }
        _state._save_review_cache()

        try:
            doc_names = sorted(d.filename for d in _state._doc_store.list_documents())
            doc_sig = "|".join(doc_names) + f"|{_state._doc_store.total_paragraphs}"
            cache_data = {
                "result": task["result"],
                "parsed_paragraphs": task.get("parsed_paragraphs", []),
                "filename": task["filename"],
                "cached_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
                "doc_signature": doc_sig,
            }
            with open(cached_result_path, "w", encoding="utf-8") as _f:
                json.dump(cache_data, _f, ensure_ascii=False)
            log.info(
                f"💾 已缓存预审核结果: {task['filename']} (SHA256={file_md5[-8:].upper()})"
            )
        except Exception as e:
            log.warning(f"预审核结果缓存写入失败: {e}")

    except InterruptedError:
        log.info(f"预审核已取消: {task['filename']}")
        task["status"] = "cancelled"
        task["current_step"] = "已取消"
    except Exception as e:
        log.error(f"预审核失败: {e}", exc_info=True)
        task["status"] = "error"
        task["current_step"] = f"错误: {e!s}"
        task["result"] = {"error": str(e)}
