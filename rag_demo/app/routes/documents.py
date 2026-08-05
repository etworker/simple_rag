"""
文档管理路由 — 上传/解析/预审核/入库/删除
"""
import os
import uuid
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
import json

from app.services.doc_store import DocStore
from app.services.config_store import ConfigStore

log = logging.getLogger("rag_demo.routes.documents")

router = APIRouter()

# 全局引用（由 main.py 注入）
_doc_store: Optional[DocStore] = None
_config: Optional[ConfigStore] = None
_upload_dir: str = ""
_cache_dir: str = ""

# 预审核任务状态
_review_tasks: dict = {}  # {task_id: {status, progress, steps, result}}
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REVIEW_CACHE_PATH = os.path.join(_BASE_DIR, "data", "review_tasks.json")
_REVIEW_RESULT_CACHE = os.path.join(_BASE_DIR, "data", "review_results")
os.makedirs(_REVIEW_RESULT_CACHE, exist_ok=True)


def _save_review_cache():
    """将已完成的 review task 持久化（result + filepath）"""
    try:
        os.makedirs(os.path.dirname(_REVIEW_CACHE_PATH), exist_ok=True)
        to_save = {}
        for tid, task in _review_tasks.items():
            if task.get("status") == "done" and task.get("result"):
                to_save[tid] = {
                    "status": task["status"],
                    "filename": task["filename"],
                    "filepath": task["filepath"],
                    "result": task["result"],
                }
        with open(_REVIEW_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False)
    except Exception:
        pass


def _load_review_cache():
    """启动时恢复已完成的 review task"""
    if os.path.exists(_REVIEW_CACHE_PATH):
        try:
            with open(_REVIEW_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for tid, task in data.items():
                _review_tasks[tid] = task
            log.info(f"恢复 {len(data)} 个审核任务缓存")
        except Exception:
            pass


_load_review_cache()
_confirmed_or_rejected: set = set()


def init(doc_store: DocStore, config: ConfigStore, upload_dir: str, cache_dir: str = ""):
    """初始化路由依赖"""
    global _doc_store, _config, _upload_dir, _cache_dir
    _doc_store = doc_store
    _config = config
    _upload_dir = upload_dir
    _cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".cache", "simple_rag")
    os.makedirs(upload_dir, exist_ok=True)

    # 更新缓存路径为可配置目录
    global _REVIEW_CACHE_PATH, _REVIEW_RESULT_CACHE
    _REVIEW_CACHE_PATH = os.path.join(_cache_dir, "review_tasks.json")
    _REVIEW_RESULT_CACHE = os.path.join(_cache_dir, "review_results")
    os.makedirs(_REVIEW_RESULT_CACHE, exist_ok=True)


@router.get("/list")
async def list_documents():
    """列出所有已入库文档"""
    docs = _doc_store.list_documents()
    return {
        "documents": [
            {
                "filename": d.filename,
                "paragraph_count": d.paragraph_count,
                "table_count": d.table_count,
                "page_count": getattr(d, 'page_count', 0),
                "char_count": getattr(d, 'char_count', 0),
                "added_at": d.added_at,
                "status": d.status,
            }
            for d in docs
        ],
        "total": len(docs),
        "total_paragraphs": _doc_store.total_paragraphs,
    }


@router.get("/review/active")
async def get_active_review():
    """获取当前活跃的预审核任务（如果有）"""
    for task_id, task in _review_tasks.items():
        if task["status"] in ("pending", "running"):
            return {
                "task_id": task_id,
                "status": task["status"],
                "filename": task["filename"],
                "progress": task["progress"],
                "current_step": task["current_step"],
                "all_steps": task.get("all_steps", []),
                "completed_steps": task.get("completed_steps", []),
            }
        if task["status"] == "done" and task.get("result"):
            if task_id not in _confirmed_or_rejected:
                return {"task_id": task_id, "status": "done", "filename": task["filename"], "result": task["result"]}
    return {"task_id": None}


@router.post("/review/{task_id}/cancel")
async def cancel_review(task_id: str):
    """取消正在进行的预审核"""
    if task_id not in _review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = _review_tasks[task_id]
    if task["status"] in ("pending", "running"):
        task["status"] = "cancelled"
        task["current_step"] = "已取消"
        # 清理上传文件
        if os.path.exists(task["filepath"]):
            os.remove(task["filepath"])
        return {"message": "已取消"}
    else:
        return {"message": "任务已结束，无法取消"}


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    上传文档 → 启动预审核流程

    返回 task_id，前端用 SSE 监听进度
    """
    # 保存上传文件
    filename = file.filename

    # 重复检测
    existing = _doc_store.get_document(filename)
    if existing and existing.status == "active":
        raise HTTPException(status_code=409, detail=f"文档 '{filename}' 已存在库中，请勿重复上传")

    # 用 UUID 子目录避免文件名冲突，同时保留原始文件名
    sub_dir = os.path.join(_upload_dir, uuid.uuid4().hex[:8])
    os.makedirs(sub_dir, exist_ok=True)
    filepath = os.path.join(sub_dir, filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # 创建任务
    task_id = uuid.uuid4().hex[:12]
    _review_tasks[task_id] = {
        "status": "pending",
        "filename": filename,
        "filepath": filepath,
        "progress": 0,
        "current_step": f"正在处理: {filename}",
        "steps": [],
        "result": None,
    }

    # 延迟启动预审核（给前端 SSE 时间连接）
    await asyncio.sleep(0.1)
    asyncio.create_task(_run_pre_review(task_id))

    return {"task_id": task_id, "filename": filename}


@router.get("/review/{task_id}/progress")
async def review_progress(task_id: str):
    """SSE 进度推送"""
    if task_id not in _review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        task = _review_tasks[task_id]
        last_step_count = -1

        while True:
            cur_step_count = len(task["steps"])
            changed = (cur_step_count != last_step_count or
                       task["status"] in ("done", "error", "cancelled"))
            if changed:
                last_step_count = cur_step_count
                # 计算当前步骤已耗时
                import time as _t
                completed = task.get("completed_steps", [])
                current_elapsed = 0
                if completed and "started_at" in completed[-1] and "elapsed" not in completed[-1]:
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
    log.info(f"confirm_review: task_id={task_id}")

    if task_id not in _review_tasks:
        log.warning(f"confirm_review: task {task_id} not found")
        raise HTTPException(status_code=404, detail="任务不存在")

    task = _review_tasks[task_id]
    log.info(f"confirm_review: task status={task.get('status')}, filename={task.get('filename')}")

    if task["status"] != "done":
        raise HTTPException(status_code=400, detail=f"预审核未完成（当前状态: {task['status']}）")

    # 检查重复
    filename = task["filename"]
    existing = _doc_store.get_document(filename)
    if existing and existing.status == "active":
        raise HTTPException(status_code=409, detail=f"文档 '{filename}' 已存在，请勿重复入库")

    # 检查文件是否存在
    filepath = task["filepath"]
    if not os.path.exists(filepath):
        log.error(f"confirm_review: file not found: {filepath}")
        raise HTTPException(status_code=404, detail="上传文件已丢失，请重新上传文档")

    # 入库（可能耗时：解析+embedding+索引）
    log.info(f"confirm_review: adding document {filepath}")
    try:
        meta = await asyncio.to_thread(_doc_store.add_document, filepath)
    except Exception as e:
        log.error(f"confirm_review: add_document failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"入库失败: {str(e)}")

    task["status"] = "confirmed"
    _confirmed_or_rejected.add(task_id)
    log.info(f"confirm_review: success, {meta.filename} ({meta.paragraph_count} paras)")
    return {"message": "文档已入库", "filename": meta.filename, "paragraphs": meta.paragraph_count}


@router.post("/review/{task_id}/reject")
async def reject_review(task_id: str):
    """人工拒绝入库"""
    if task_id not in _review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = _review_tasks[task_id]
    task["status"] = "rejected"
    _confirmed_or_rejected.add(task_id)

    # 清理上传文件
    if os.path.exists(task["filepath"]):
        os.remove(task["filepath"])

    return {"message": "已拒绝，文档不入库"}


@router.post("/review/{task_id}/rerun")
async def rerun_review(task_id: str):
    """
    强制重新执行预审核（忽略缓存）

    删除预审核结果缓存，重置任务状态，重新启动预审核流程。
    用于调试提示词/阈值变化后需要重新审核的场景。
    """
    if task_id not in _review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = _review_tasks[task_id]
    filepath = task.get("filepath", "")

    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=400, detail="上传文件已不存在，请重新上传")

    # 1. 删除预审核结果缓存
    import hashlib as _hashlib
    try:
        _md5 = _hashlib.md5()
        with open(filepath, "rb") as _f:
            for chunk in iter(lambda: _f.read(8192), b""):
                _md5.update(chunk)
        file_md5 = _md5.hexdigest()
        cached_path = os.path.join(_REVIEW_RESULT_CACHE, f"{file_md5}.json")
        if os.path.exists(cached_path):
            os.remove(cached_path)
            log.info(f"🗑️ 已删除预审核结果缓存: {task['filename']} (MD5={file_md5[:8]})")
    except Exception as e:
        log.warning(f"删除预审核缓存失败: {e}")

    # 2. 重置任务状态
    task["status"] = "pending"
    task["progress"] = 0
    task["current_step"] = f"强制重新执行预审核: {task['filename']}"
    task["result"] = None
    task["steps"] = []
    task["completed_steps"] = []
    task["parsed_paragraphs"] = []
    task["all_steps"] = []

    # 从 confirmed/rejected 集合中移除（允许再次操作）
    _confirmed_or_rejected.discard(task_id)

    # 3. 重新启动预审核任务
    import asyncio
    await asyncio.sleep(0.1)
    asyncio.create_task(_run_pre_review(task_id))

    return {"message": "已开始重新执行预审核"}


@router.get("/review/paragraphs")
async def get_pending_paragraphs(file: str):
    """获取正在预审核的新文档段落（尚未入库）"""
    from urllib.parse import unquote
    file = unquote(file)
    for task in _review_tasks.values():
        paras = task.get("parsed_paragraphs", [])
        for p in paras:
            if p["source_file"] == file:
                return {"file": file, "paragraphs": paras}
    return {"file": file, "paragraphs": []}


@router.get("/pdf")
async def get_document_pdf(name: str):
    """返回原始 PDF 文件（用于浏览器内置阅读器 / PDF.js 渲染）"""
    from fastapi.responses import FileResponse
    from urllib.parse import unquote

    filename = unquote(name)
    meta = _doc_store.get_document(filename)
    if not meta or meta.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(meta.filepath, media_type="application/pdf")


@router.get("/info")
async def get_document_info(name: str):
    """获取文档基本信息（页数等）"""
    import asyncio
    from urllib.parse import unquote
    from app.services.page_renderer import PageRenderer

    filename = unquote(name)
    meta = _doc_store.get_document(filename)
    if not meta or meta.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    renderer = PageRenderer(cache_dir=os.path.join(_cache_dir, "page_cache"))
    page_count = await asyncio.to_thread(renderer.get_page_count, meta.filepath)
    return {"filename": filename, "page_count": page_count}


@router.get("/page")
async def get_document_page(name: str, page: int = 1, highlight: str = ""):
    """获取已入库文档指定页的 PNG 图片"""
    import asyncio
    from fastapi.responses import FileResponse
    from urllib.parse import unquote
    from app.services.page_renderer import PageRenderer

    filename = unquote(name)
    meta = _doc_store.get_document(filename)
    if not meta or meta.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")
    if not os.path.exists(meta.filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    renderer = PageRenderer(cache_dir=os.path.join(_cache_dir, "page_cache"))
    png_path = await asyncio.to_thread(renderer.get_page, meta.filepath, page, unquote(highlight))
    return FileResponse(png_path, media_type="image/png")


@router.get("/review/pdf")
async def get_review_pdf(task_id: str):
    """返回预审核文件的原始 PDF（用于 PDF.js 预览）"""
    from fastapi.responses import FileResponse

    if task_id not in _review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _review_tasks[task_id]
    filepath = task.get("filepath", "")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(filepath, media_type="application/pdf")


@router.get("/review/page")
async def get_review_page(task_id: str, page: int = 1, highlight: str = ""):
    """获取预审核文件指定页的 PNG 图片"""
    import asyncio
    from fastapi.responses import FileResponse
    from urllib.parse import unquote
    from app.services.page_renderer import PageRenderer

    if task_id not in _review_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = _review_tasks[task_id]
    filepath = task["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    renderer = PageRenderer(cache_dir=os.path.join(_cache_dir, "page_cache"))
    png_path = await asyncio.to_thread(renderer.get_page, filepath, page, unquote(highlight))
    return FileResponse(png_path, media_type="image/png")


@router.delete("/remove/{filename}")
async def delete_document(filename: str):
    """删除已入库文档"""
    success = _doc_store.remove_document(filename)
    if not success:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"message": f"已删除: {filename}"}


@router.post("/clear")
async def clear_all_documents():
    """清空知识库（删除所有文档、缓存、向量，完全从0开始）"""
    import shutil

    # 1. 清空 DocStore（内存 + 磁盘持久化）
    count = await asyncio.to_thread(_doc_store.clear_all)

    # 2. 清理预审核任务状态（内存 + 磁盘）
    _review_tasks.clear()
    if os.path.exists(_REVIEW_CACHE_PATH):
        os.remove(_REVIEW_CACHE_PATH)

    # 3. 清理预审核结果缓存（review_results/）
    if os.path.exists(_REVIEW_RESULT_CACHE):
        shutil.rmtree(_REVIEW_RESULT_CACHE, ignore_errors=True)
    os.makedirs(_REVIEW_RESULT_CACHE, exist_ok=True)

    # 4. 清理已确认/拒绝的记录
    _confirmed_or_rejected.clear()

    # 5. 清理解析缓存（parse_cache/）
    parse_cache_dir = os.path.join(_cache_dir, "parse_cache")
    if os.path.exists(parse_cache_dir):
        shutil.rmtree(parse_cache_dir, ignore_errors=True)
    os.makedirs(parse_cache_dir, exist_ok=True)

    # 6. 清理页面渲染缓存（page_cache/）
    page_cache_dir = os.path.join(_cache_dir, "page_cache")
    if os.path.exists(page_cache_dir):
        shutil.rmtree(page_cache_dir, ignore_errors=True)
    os.makedirs(page_cache_dir, exist_ok=True)

    # 7. 清理向量缓存（vector_cache/）
    vector_cache_dir = os.path.join(_cache_dir, "vector_cache")
    if os.path.exists(vector_cache_dir):
        shutil.rmtree(vector_cache_dir, ignore_errors=True)
    os.makedirs(vector_cache_dir, exist_ok=True)

    log.info(f"🗑️ 知识库完全重置: 清空 {count} 篇文档 + 全部缓存（解析/向量/页面/预审核）")
    return {"message": f"知识库已完全重置，共移除 {count} 篇文档，所有缓存已清除"}


# ============================================================
# 预审核后台任务
# ============================================================

def _load_existing_docs(engine):
    """同步加载已有文档到引擎（在线程中执行）"""
    for doc_meta in _doc_store.list_documents():
        engine.add(doc_meta.filepath)


async def _run_pre_review(task_id: str):
    """
    执行预审核（异步后台任务）

    优化：如果同一文档（MD5 相同）且库状态未变，直接复用旧结果。
    """
    # 等待前端 SSE 连接建立
    await asyncio.sleep(0.5)
    task = _review_tasks[task_id]
    filepath = task["filepath"]

    # ====== 0. 文件存在性检查 ======
    if not os.path.exists(filepath):
        log.warning(f"上传文件不存在: {filepath}")
        task["status"] = "error"
        task["current_step"] = "错误: 上传文件已丢失，请重新上传"
        task["result"] = {"error": "上传文件已丢失"}
        return

    # ====== 1. 快速路径：检查预审核结果缓存 ======
    import hashlib as _hashlib
    _md5 = _hashlib.md5()
    with open(filepath, "rb") as _f:
        for chunk in iter(lambda: _f.read(8192), b""):
            _md5.update(chunk)
    file_md5 = _md5.hexdigest()

    cached_result_path = os.path.join(_REVIEW_RESULT_CACHE, f"{file_md5}.json")
    if os.path.exists(cached_result_path):
        try:
            with open(cached_result_path, "r", encoding="utf-8") as _f:
                cached = json.load(_f)

            # 校验：库中文档是否变化（用文档名+总数做简单签名）
            doc_names = sorted(d.filename for d in _doc_store.list_documents())
            doc_sig = "|".join(doc_names) + f"|{_doc_store.total_paragraphs}"
            cache_doc_sig = cached.get("doc_signature", "")
            if cache_doc_sig != doc_sig:
                log.info(f"📦 缓存已过期（库文档变化），重新执行预审核: {task['filename']}")
            else:
                log.info(f"📦 命中预审核结果缓存: {task['filename']} (MD5={file_md5[:8]})")
                task["status"] = "done"
                task["progress"] = 100
                task["current_step"] = "预审核完成（使用缓存）"
                task["result"] = cached.get("result")
                task["parsed_paragraphs"] = cached.get("parsed_paragraphs", [])
                task["all_steps"] = [
                    {"id": "cache", "label": "读取预审核缓存"},
                    {"id": "done", "label": "完成"},
                ]
                task["completed_steps"] = [
                    {"id": "cache", "message": "读取预审核缓存", "started_at": 0, "elapsed": 0.01},
                ]
                _save_review_cache()
                return
        except Exception as e:
            log.warning(f"预审核缓存加载失败，重新执行: {e}")

    # ====== 2. 慢速路径：完整预审核流程 ======

    # 定义全流程步骤
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
        # 检查是否已取消
        if task["status"] == "cancelled":
            raise InterruptedError("用户取消")

        now = _time.time()
        task["progress"] = int(pct * 100)
        task["current_step"] = msg
        # 记录完成的步骤（去重）
        completed_ids = [s["id"] for s in task["completed_steps"]]
        if step not in completed_ids:
            # 上一个步骤标记耗时
            if task["completed_steps"]:
                task["completed_steps"][-1]["elapsed"] = round(now - step_start_time, 1)
            step_start_time = now
            task["completed_steps"].append({"id": step, "message": msg, "started_at": now})
        task["steps"].append({"step": step, "progress": int(pct * 100), "message": msg})

    try:
        task["status"] = "running"
        task["current_step"] = f"开始预审核: {task['filename']}"

        # 调用 version_diff 预审核
        from version_diff import DiffEngine

        engine_config = {
            "embedding": _config.get_section("embedding"),
            "llm": _config.get_section("llm"),
            "diff": _config.get_section("pre_review"),
            "cache": {"vector_cache_dir": os.path.join(_cache_dir, "vector_cache")},
        }
        engine = DiffEngine(config=engine_config)

        # 加载向量模型（首次可能耗时）
        on_progress("model", 0.05, "加载向量模型...")
        log.info("开始加载向量模型...")
        await asyncio.to_thread(engine._get_model)
        log.info("向量模型加载完成")

        # 把已有文档加入引擎
        on_progress("loading", 0.1, "加载已有文档到引擎...")
        log.info(f"开始加载已有文档 ({_doc_store.total_documents} 篇)...")
        await asyncio.to_thread(_load_existing_docs, engine)
        log.info("已有文档加载完成")

        # 解析新文档并暂存段落（供对比面板查看）
        from app.services.parse_cache import cached_parse as _parse
        log.info(f"开始解析新文档: {task['filename']}")
        new_doc = await asyncio.to_thread(_parse, filepath)
        log.info(f"新文档解析完成: {len(new_doc.paragraphs)} 段落")
        task["parsed_paragraphs"] = [
            {"text": p.text, "location": p.location, "source_file": new_doc.filename}
            for p in new_doc.paragraphs
        ]

        # 执行预审核
        result = await asyncio.to_thread(
            engine.pre_review, filepath, on_progress=on_progress
        )

        # 完成
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
            "message": "无矛盾，可安全入库" if result.is_safe else f"发现 {len(result.inconsistencies)} 处矛盾",
        }
        _save_review_cache()

        # 写入预审核结果缓存（供下次同文档快速返回）
        try:
            doc_names = sorted(d.filename for d in _doc_store.list_documents())
            doc_sig = "|".join(doc_names) + f"|{_doc_store.total_paragraphs}"
            cache_data = {
                "result": task["result"],
                "parsed_paragraphs": task.get("parsed_paragraphs", []),
                "filename": task["filename"],
                "cached_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
                "doc_signature": doc_sig,
            }
            with open(cached_result_path, "w", encoding="utf-8") as _f:
                json.dump(cache_data, _f, ensure_ascii=False)
            log.info(f"💾 已缓存预审核结果: {task['filename']} (MD5={file_md5[:8]})")
        except Exception as e:
            log.warning(f"预审核结果缓存写入失败: {e}")

    except InterruptedError:
        log.info(f"预审核已取消: {task['filename']}")
        task["status"] = "cancelled"
        task["current_step"] = "已取消"
    except Exception as e:
        log.error(f"预审核失败: {e}", exc_info=True)
        task["status"] = "error"
        task["current_step"] = f"错误: {str(e)}"
        task["result"] = {"error": str(e)}
