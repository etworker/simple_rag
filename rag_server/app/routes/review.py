"""预审核路由 — 上传/预审核/确认/拒绝/重跑

后台任务逻辑已拆分到 app.services.review_runner。
"""

import asyncio
import json
import os
import tempfile
import threading
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from loguru import logger as log

from app.routes import _state
from app.services.review_runner import run_pre_review as _run_pre_review
from app.services.review_report import build_review_report_html
from app.services.utils import compute_sha256, compute_sha256_bytes, get_pdf_page_count

router = APIRouter()

# 上传的文件名由内容 hash 决定；同一进程内必须把“查重→写文件→登记任务”作为一个原子操作，
# 否则两个相同文件的并发上传会在 Windows 上竞争同一个目标文件。
_upload_lock = threading.Lock()


def _find_unfinished_review_by_hash(file_hash: str):
    """查找尚未确认/拒绝的同内容审核任务，避免重复占用同一路径。"""
    for task_id, task in reversed(list(_state.app.review_tasks.items())):
        if task.get("file_hash") != file_hash:
            continue
        if task.get("status") not in ("pending", "running", "paused", "done", "error"):
            continue
        if task_id in _state.app.confirmed_or_rejected:
            continue
        return task_id, task
    return None, None


def _persist_upload_content(content: bytes, file_hash: str, filename: str) -> str:
    """安全保存上传内容，使用唯一临时文件并尽量复用已校验的同 hash 文件。"""
    file_ext = (os.path.splitext(filename)[1] or ".bin").lower()
    safe_filename = f"{file_hash}{file_ext}"
    filepath = os.path.join(_state.app.upload_dir, safe_filename)
    os.makedirs(_state.app.upload_dir, exist_ok=True)

    # 重复请求或服务重启后遗留的同 hash 文件无需再次替换，避免 Windows 文件锁冲突。
    if os.path.exists(filepath):
        try:
            if compute_sha256(filepath) == file_hash:
                log.info(f"复用已保存上传文件: {safe_filename}")
                return filepath
        except OSError as exc:
            log.warning(f"校验已有上传文件失败，将尝试修复: {filepath}: {exc}")

    tmp_filepath = None
    try:
        fd, tmp_filepath = tempfile.mkstemp(
            dir=_state.app.upload_dir,
            prefix=f".{file_hash}.",
            suffix=".tmp",
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        last_error = None
        for attempt in range(4):
            try:
                os.replace(tmp_filepath, filepath)
                tmp_filepath = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt == 3:
                    raise HTTPException(
                        status_code=503,
                        detail="上传文件正在被其他审核或预览任务占用，请稍后重试",
                    ) from exc
                time.sleep(0.15 * (attempt + 1))
        if last_error and os.path.exists(tmp_filepath or ""):
            raise last_error
    finally:
        if tmp_filepath and os.path.exists(tmp_filepath):
            try:
                os.remove(tmp_filepath)
            except OSError:
                log.warning(f"上传临时文件清理失败，稍后可手动清理: {tmp_filepath}")

    log.info(f"上传保存: {filename} -> {safe_filename} ({len(content)} bytes)")
    return filepath



@router.get("/review/active")
async def get_active_review():
    """获取当前活跃的预审核任务（如果有）

    优先级:
      1. 最新 pending/running 任务（正在处理中）
      2. 最新 done 且未确认/拒绝的任务（待用户确认）

    错误任务若带有完整 result，也会返回，供用户查看失败的比较组并重试；
    已 confirmed/rejected/cancelled 的任务会被跳过。
    """
    # 先查找 pending/running（按插入逆序，取最新的）
    items = list(_state.app.review_tasks.items())
    for task_id, task in reversed(items):
        if task["status"] in ("pending", "running", "paused"):
            return {
                "task_id": task_id,
                "status": task["status"],
                "filename": task["filename"],
                "file_hash": task.get("file_hash", ""),
                "label": task.get("label", ""),
                "progress": task["progress"],
                "current_step": task["current_step"],
                "all_steps": task.get("all_steps", []),
                "completed_steps": task.get("completed_steps", []),
                "result": task.get("result"),
                "old_version_filepath": os.path.basename(task.get("old_version_filepath", "")),
                "old_doc_filename": task.get("old_doc_filename", ""),
            }
    # 再查找最近的 done/error 终态任务，避免旧成功任务遮住更新的失败结果。
    for task_id, task in reversed(items):
        if task["status"] == "done" and task.get("result") and task_id not in _state.app.confirmed_or_rejected:
            old_vf = task.get("old_version_filepath", "")
            old_vf_basename = os.path.basename(old_vf) if old_vf else ""
            old_doc_filename = task.get("old_doc_filename", "")
            return {
                "task_id": task_id,
                "status": "done",
                "filename": task["filename"],
                "file_hash": task.get("file_hash", ""),
                "label": task.get("label", ""),
                "result": task["result"],
                "old_version_filepath": old_vf_basename,
                "old_doc_filename": old_doc_filename,
                "old_version_fullpath": old_vf,
            }
        if task["status"] == "error" and task.get("result"):
            old_vf = task.get("old_version_filepath", "")
            old_vf_basename = os.path.basename(old_vf) if old_vf else ""
            return {
                "task_id": task_id,
                "status": "error",
                "filename": task["filename"],
                "file_hash": task.get("file_hash", ""),
                "label": task.get("label", ""),
                "progress": task.get("progress", 100),
                "current_step": task.get("current_step", "预审核失败"),
                "result": task["result"],
                "old_version_filepath": old_vf_basename,
                "old_doc_filename": task.get("old_doc_filename", ""),
                "old_version_fullpath": old_vf,
            }
    return {"task_id": None}


@router.post("/review/{task_id}/cancel")
async def cancel_review(task_id: str):
    """取消正在进行的预审核"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task["status"] in ("pending", "running", "paused"):
        task["status"] = "cancelled"
        task["current_step"] = "已取消"
        task["state_seq"] = task.get("state_seq", 0) + 1
        if os.path.exists(task["filepath"]):
            os.remove(task["filepath"])
        return {"message": "已取消"}
    return {"message": "任务已结束，无法取消"}


@router.post("/review/{task_id}/pause")
async def pause_review(task_id: str):
    """暂停正在进行的预审核（逐文档比较循环中生效）"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task["status"] == "running":
        task["status"] = "paused"
        task["current_step"] = "已暂停（点击续跑继续）"
        task["state_seq"] = task.get("state_seq", 0) + 1
        _state.app.save_review_cache()
        return {"message": "已暂停"}
    return {"message": f"当前状态无法暂停（{task['status']}）"}


@router.post("/review/{task_id}/resume")
async def resume_review(task_id: str):
    """续跑已暂停的预审核"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task["status"] == "paused":
        task["status"] = "running"
        task["current_step"] = "继续检测中..."
        task["state_seq"] = task.get("state_seq", 0) + 1
        _state.app.save_review_cache()
        return {"message": "已续跑"}
    return {"message": f"当前状态无法续跑（{task['status']}）"}


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), choice: str = Form(""), label: str = Form("")):
    """
    上传文档 → 启动预审核流程

    若同名不同内容文档已存在，第一次返回 needs_choice=True，
    前端带 choice=overwrite|coexist 重新上传。
    """
    filename = file.filename
    content = await file.read()
    new_sha = compute_sha256_bytes(content)

    if choice not in ("", "overwrite", "coexist", "new_primary", "keep_current"):
        raise HTTPException(status_code=400, detail="choice 必须是 new_primary 或 keep_current")

    # 保护“查重→保存→登记任务”整个临界区，避免同 hash 并发请求共享 .tmp/目标路径。
    with _upload_lock:
        existing_task_id, existing_task = _find_unfinished_review_by_hash(new_sha)
        if existing_task_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"该文档已有审核任务：{existing_task_id}"
                    f"（当前状态：{existing_task.get('status', 'unknown')}），请直接查看或重跑该任务"
                ),
            )

        duplicate = _state.app.doc_store.get_document_by_hash(new_sha)
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"文档内容已存在：{duplicate.filename}"
                    f"（{duplicate.doc_id}，{duplicate.status}），相同内容不会重复入库"
                ),
            )

        # 记录旧版本信息（如果是同名文档更新）
        old_version_filepath = ""
        old_doc_filename = ""
        replace_doc_id = ""
        existing = _state.app.doc_store.get_latest_document_by_filename(filename)
        if existing and existing.status == "active":
            if not choice:
                return {
                    "needs_choice": True,
                    "filename": filename,
                    "file_hash": new_sha,
                    "existing": {
                        "filename": existing.filename,
                        "doc_id": existing.doc_id,
                        "family_id": getattr(existing, "family_id", ""),
                        "file_hash": existing.file_hash,
                        "added_at": existing.added_at,
                        "paragraph_count": existing.paragraph_count,
                        "label": existing.label,
                    },
                    "options": [
                        {"id": "coexist", "label": "保留旧版本并进行版本审核"},
                        {"id": "new_primary", "label": "审核后使用新版本"},
                    ],
                }
            # 预审核期间始终保留旧主版本；最终是否切换由 confirm 的 mode 决定。
            old_doc_filename = existing.filename
            old_version_filepath = existing.filepath
            log.info(f"检测到同文档版本: {existing.doc_id}，新版本待审核后选择主版本")
            if choice in ("overwrite", "new_primary"):
                # 兼容旧客户端：overwrite 仅代表审核后倾向新版本，不再删除旧文件。
                replace_doc_id = existing.doc_id

        filepath = _persist_upload_content(content, new_sha, filename)
        task_id = uuid.uuid4().hex[:12]
        _state.app.review_tasks[task_id] = {
            "status": "pending",
            "filename": filename,
            "filepath": filepath,
            "file_hash": new_sha,
            "progress": 0,
            "state_seq": 0,
            "current_step": f"正在处理: {filename}",
            "steps": [],
            "result": None,
            "old_version_filepath": old_version_filepath,  # 版本对比用（文件路径）
            "old_doc_filename": old_doc_filename,  # 版本对比用（可读文件名）
            "replace_doc_id": replace_doc_id,  # 兼容旧客户端；不再删除旧版本
            "existing_primary_doc_id": existing.doc_id if existing else "",
            "family_id": getattr(existing, "family_id", "") if existing else "",
            "version_action": "new_primary" if (not existing or choice in ("overwrite", "new_primary")) else "ask",
            "label": label.strip()[:60],  # 用户补充描述（如版本号），入库时写入 DocMeta
        }

    await asyncio.sleep(0.1)
    asyncio.create_task(_run_pre_review(task_id))
    return {"task_id": task_id, "filename": filename, "file_hash": new_sha}


@router.post("/review/{task_id}/label")
async def update_review_label(task_id: str, label: str = Form("")):
    """更新尚未确认/拒绝的待审核文档补充描述。"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task.get("status") not in ("pending", "running", "paused", "done", "error"):
        raise HTTPException(status_code=409, detail="该审核任务已结束，不能修改文档描述")

    normalized = (label or "").strip()[:60]
    previous = task.get("label", "")
    previous_result_label = None
    task["label"] = normalized
    result = task.get("result")
    if isinstance(result, dict):
        previous_result_label = result.get("new_doc_label")
        result["new_doc_label"] = normalized
        task["_result_seq"] = task.get("_result_seq", 0) + 1
    task["state_seq"] = task.get("state_seq", 0) + 1
    try:
        _state.app.save_review_cache()
    except Exception as e:
        task["label"] = previous
        if isinstance(result, dict):
            if previous_result_label is None:
                result.pop("new_doc_label", None)
            else:
                result["new_doc_label"] = previous_result_label
        raise HTTPException(status_code=500, detail="文档描述保存失败，请重试") from e
    return {"message": "已更新", "label": normalized}


@router.get("/review/{task_id}/progress")
async def review_progress(task_id: str):
    """SSE 进度推送"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        task = _state.app.review_tasks[task_id]
        last_step_count = -1
        last_result_seq = None
        last_state_seq = None
        while True:
            cur_step_count = len(task["steps"])
            cur_result_seq = task.get("_result_seq", 0)
            cur_state_seq = task.get("state_seq", task.get("_state_seq", 0))
            # 步骤、结果或任务控制状态发生变化时推送；state_seq 覆盖暂停/续跑等无新步骤的变化。
            changed = (
                cur_step_count != last_step_count
                or cur_result_seq != last_result_seq
                or cur_state_seq != last_state_seq
                or task["status"] in ("done", "error", "cancelled")
            )
            if changed:
                last_step_count = cur_step_count
                last_result_seq = cur_result_seq
                last_state_seq = cur_state_seq
                import time as _t

                completed = task.get("completed_steps", [])
                current_elapsed = 0
                if completed and "started_at" in completed[-1] and "elapsed" not in completed[-1]:
                    current_elapsed = round(_t.time() - completed[-1]["started_at"], 1)
                payload = {
                    "status": task["status"],
                    "progress": task["progress"],
                    "state_seq": cur_state_seq,
                    "current_step": task["current_step"],
                    "steps": task["steps"],
                    "all_steps": task.get("all_steps", []),
                    "completed_steps": task.get("completed_steps", []),
                    "step_states": task.get("step_states", {}),
                    "current_elapsed": current_elapsed,
                    "result": task["result"],
                    "old_version_filepath": os.path.basename(task.get("old_version_filepath", "")),
                    "old_doc_filename": task.get("old_doc_filename", ""),
                    "new_doc_label": task.get("label", ""),
                    "old_version_fullpath": task.get("old_version_filepath", ""),
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


@router.get("/review/{task_id}/report.html")
async def export_review_report(task_id: str):
    """导出当前审核任务为可离线打开的单文件 HTML 报告。"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task.get("status") not in ("done", "error"):
        raise HTTPException(status_code=409, detail="逐文档比对尚未完成，暂不能导出")
    if not task.get("result"):
        raise HTTPException(status_code=409, detail="审核结果尚未生成，暂不能导出")
    report = build_review_report_html(task_id, task, _state.app.doc_store.list_documents())
    return HTMLResponse(
        content=report,
        headers={
            "Content-Disposition": f'attachment; filename="review-report-{task_id}.html"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/review/{task_id}/confirm")
async def confirm_review(task_id: str, mode: str = Form("")):
    """人工确认入库；同文档版本可选择新版本或旧版本作为当前版本。"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task["status"] != "done":
        raise HTTPException(status_code=400, detail=f"预审核未完成（当前状态: {task['status']}）")
    result = task.get("result")
    if (
        not isinstance(result, dict)
        or result.get("phase") != "done"
        or result.get("incomplete") is True
        or result.get("cancelled") is True
    ):
        raise HTTPException(status_code=400, detail="预审核结果未完成，不能确认入库")

    filepath = task["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="上传文件已丢失，请重新上传文档")

    file_hash = compute_sha256(filepath)
    existing_hash_doc = _state.app.doc_store.get_document_by_hash(file_hash)
    ingested_doc_id = task.get("ingested_doc_id", "")
    # 并发确认校验；只有本任务此前已经写入同一条记录时允许重试。
    if existing_hash_doc and existing_hash_doc.doc_id != ingested_doc_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"文档内容已存在：{existing_hash_doc.filename}"
                f"（{existing_hash_doc.doc_id}），相同内容不会重复入库"
            ),
        )

    existing_primary_id = task.get("existing_primary_doc_id", "")
    if not existing_primary_id and task.get("replace_doc_id"):
        legacy_primary = _state.app.doc_store.get_document(task["replace_doc_id"])
        if legacy_primary and legacy_primary.status == "active":
            existing_primary_id = legacy_primary.doc_id
            task["existing_primary_doc_id"] = existing_primary_id
            task["family_id"] = getattr(legacy_primary, "family_id", "")
    if not existing_primary_id and task.get("old_doc_filename"):
        legacy_primary = _state.app.doc_store.get_latest_document_by_filename(task["old_doc_filename"])
        if legacy_primary:
            existing_primary_id = legacy_primary.doc_id
            task["existing_primary_doc_id"] = existing_primary_id
            task["family_id"] = getattr(legacy_primary, "family_id", "")
    existing_primary = _state.app.doc_store.get_document(existing_primary_id) if existing_primary_id else None
    requested_mode = mode or task.get("version_action", "")
    if existing_primary and requested_mode not in ("new_primary", "keep_current"):
        raise HTTPException(status_code=400, detail="同文档版本需要选择当前版本")
    if requested_mode not in ("", "new_primary", "keep_current"):
        raise HTTPException(status_code=400, detail="mode 必须是 new_primary 或 keep_current")

    try:
        # 新版本先以 inactive 写入，随后按用户选择切换主版本；这样旧版本始终可回滚。
        if existing_hash_doc and existing_hash_doc.doc_id == ingested_doc_id:
            meta = existing_hash_doc
            log.info(f"继续未完成的确认: 已存在本任务文档 {meta.doc_id}")
        else:
            meta = await asyncio.to_thread(
                _state.app.doc_store.add_document,
                filepath,
                task["filename"],
                task.get("label", ""),
                task.get("family_id", ""),
                "inactive" if existing_primary else "active",
                False if existing_primary else True,
            )
            task["ingested_doc_id"] = meta.doc_id
        if existing_primary and requested_mode == "new_primary" and meta.status != "active":
            promoted = await asyncio.to_thread(_state.app.doc_store.set_primary_document, meta.doc_id)
            if not promoted:
                raise RuntimeError("新版本入库成功，但切换当前版本失败")
        log.info(
            f"入库成功: {meta.filename} ({meta.paragraph_count} paras, "
            f"status={meta.status}, family={meta.family_id})"
        )
    except Exception as e:
        log.error(f"入库失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"入库失败: {e!s}") from e

    task["status"] = "confirmed"
    _state.app.confirmed_or_rejected.add(task_id)
    try:
        _state.app.save_review_cache()
    except Exception as e:
        task["status"] = "done"
        _state.app.confirmed_or_rejected.discard(task_id)
        log.error(f"文档已入库，但确认状态保存失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="文档已入库，但确认状态保存失败；请重试确认") from e
    log.info(f"入库成功: {meta.filename} ({meta.paragraph_count} paras) @ {filepath}")
    return {
        "message": "文档已入库",
        "filename": meta.filename,
        "doc_id": meta.doc_id,
        "family_id": meta.family_id,
        "status": meta.status,
        "is_primary": meta.is_primary,
        "paragraphs": meta.paragraph_count,
    }


@router.post("/review/{task_id}/reject")
async def reject_review(task_id: str):
    """人工拒绝入库"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    if task["status"] in ("confirmed", "rejected"):
        raise HTTPException(status_code=400, detail=f"任务已{task['status']}，无法拒绝")
    if task["status"] not in ("done", "error"):
        raise HTTPException(status_code=400, detail=f"预审核未完成（当前状态: {task['status']}）")
    previous_status = task["status"]
    task["status"] = "rejected"
    _state.app.confirmed_or_rejected.add(task_id)
    try:
        _state.app.save_review_cache()
    except Exception as e:
        task["status"] = previous_status
        _state.app.confirmed_or_rejected.discard(task_id)
        raise HTTPException(status_code=500, detail="拒绝状态保存失败，请重试") from e
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
            status_code=400,
            detail=f"任务已{task['status']}，无法重跑",
        )
    if task["status"] in ("pending", "running", "paused"):
        raise HTTPException(status_code=409, detail=f"任务正在{task['status']}，请先取消或等待完成")

    filepath = task.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=400, detail="上传文件已不存在，请重新上传")

    try:
        file_md5 = compute_sha256(filepath)
        cached_path = os.path.join(_state.app.review_result_cache, f"{file_md5}.json")
        if os.path.exists(cached_path):
            os.remove(cached_path)
            log.info(f"🗑️ 已删除预审核结果缓存: {task['filename']} (SHA256={file_md5[-8:].upper()})")
    except Exception as e:
        log.warning(f"删除预审核缓存失败: {e}")

    task["status"] = "pending"
    task["state_seq"] = task.get("state_seq", 0) + 1
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
    png_path = await asyncio.to_thread(renderer.get_page, filepath, page, unquote(highlight))
    return FileResponse(
        png_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.get("/review/info")
async def get_review_info(task_id: str):
    """获取预审核文档的信息（页数等）"""
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    filepath = task.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        return {"exists": False, "page_count": 0}
    page_count = get_pdf_page_count(filepath)
    return {"exists": page_count > 0, "page_count": page_count}


@router.get("/review/old/info")
async def get_review_old_info(task_id: str, doc_id: str = ""):
    """获取对比文档的信息（页数等）。

    doc_id 指定时按库内文档取（分组模式下 B 侧任意文档）；缺省用任务旧版。
    """
    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    old_filepath = ""
    if doc_id:
        meta = _state.app.doc_store.get_document(doc_id)
        if meta and meta.filepath and os.path.exists(meta.filepath):
            old_filepath = meta.filepath
    else:
        old_filepath = task.get("old_version_filepath", "")
    if not old_filepath or not os.path.exists(old_filepath):
        return {"exists": False, "page_count": 0}
    page_count = get_pdf_page_count(old_filepath)
    return {"exists": page_count > 0, "page_count": page_count}


@router.get("/review/old/page")
async def get_review_old_page(task_id: str, page: int = 1, highlight: str = "", doc_id: str = ""):
    """获取对比文档指定页的 PNG 图片。

    doc_id 指定时按库内文档取（分组模式下 B 侧任意文档）；缺省用任务旧版。
    """
    from urllib.parse import unquote

    from app.services.page_renderer import PageRenderer

    if task_id not in _state.app.review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _state.app.review_tasks[task_id]
    old_filepath = ""
    if doc_id:
        meta = _state.app.doc_store.get_document(doc_id)
        if meta and meta.filepath and os.path.exists(meta.filepath):
            old_filepath = meta.filepath
    else:
        old_filepath = task.get("old_version_filepath", "")
    if not old_filepath or not os.path.exists(old_filepath):
        raise HTTPException(status_code=404, detail="对比文档文件不存在")
    renderer = PageRenderer(cache_dir=os.path.join(_state.app.cache_dir, "page_cache"))
    png_path = await asyncio.to_thread(renderer.get_page, old_filepath, page, unquote(highlight))
    return FileResponse(
        png_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
