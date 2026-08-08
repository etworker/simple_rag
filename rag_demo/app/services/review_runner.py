"""
预审核后台任务执行器

从 review.py 拆分出来的 _run_pre_review + _load_existing_docs，
减轻路由文件体积（530 → ~340 行）。
"""

import asyncio
import copy
import json
import logging
import os
import time

from app.routes import _state
from app.services.utils import compute_sha256

log = logging.getLogger("rag_demo.review_runner")


def load_existing_docs(engine):
    """同步加载已有文档到引擎（在线程中执行）"""
    # ★ Fix: 文件缺失或损坏时跳过，不中断整个预审核流程
    for doc_meta in _state.app.doc_store.list_documents():
        if not doc_meta.filepath or not os.path.exists(doc_meta.filepath):
            log.warning(f"跳过加载已有文档（文件不存在）: {doc_meta.filename} -> {doc_meta.filepath}")
            continue
        try:
            engine.add(doc_meta.filepath)
        except Exception as e:
            log.error(f"加载已有文档失败（跳过）: {doc_meta.filename}: {e}")


async def run_pre_review(task_id: str):
    """执行预审核（异步后台任务）"""
    await asyncio.sleep(0.5)
    task = _state.app.review_tasks[task_id]
    filepath = task["filepath"]

    if not os.path.exists(filepath):
        task["status"] = "error"
        task["current_step"] = "错误: 上传文件已丢失，请重新上传"
        task["result"] = {"error": "上传文件已丢失"}
        return

    # ====== 1. 快速路径：检查预审核结果缓存 ======
    file_md5 = compute_sha256(filepath)
    cached_result_path = os.path.join(_state.app.review_result_cache, f"{file_md5}.json")

    if os.path.exists(cached_result_path):
        try:
            with open(cached_result_path, "r", encoding="utf-8") as _f:
                cached = json.load(_f)
            doc_names = sorted(d.filename for d in _state.app.doc_store.list_documents())
            doc_sig = "|".join(doc_names) + f"|{_state.app.doc_store.total_paragraphs}"
            cache_doc_sig = cached.get("doc_signature", "")
            if cache_doc_sig != doc_sig:
                log.info(f"📦 缓存已过期（库文档变化），重新执行预审核: {task['filename']}")
            else:
                log.info(f"📦 命中预审核结果缓存: {task['filename']} (SHA256={file_md5[-8:].upper()})")
                task["status"] = "done"
                task["progress"] = 100
                task["current_step"] = "预审核完成（使用缓存）"
                task["result"] = copy.deepcopy(cached.get("result"))
                task["parsed_paragraphs"] = copy.deepcopy(cached.get("parsed_paragraphs", []))
                task["all_steps"] = [
                    {"id": "cache", "label": "读取预审核缓存"},
                    {"id": "done", "label": "完成"},
                ]
                task["completed_steps"] = [
                    {"id": "cache", "message": "读取预审核缓存", "started_at": 0, "elapsed": 0.01},
                ]
                _state.app.save_review_cache()
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

    step_start_time = time.time()

    def on_progress(step: str, pct: float, msg: str):
        nonlocal step_start_time
        if task["status"] == "cancelled":
            raise InterruptedError("用户取消")
        now = time.time()
        task["progress"] = int(pct * 100)
        task["current_step"] = msg
        completed_ids = [s["id"] for s in task["completed_steps"]]
        if step not in completed_ids:
            if task["completed_steps"]:
                task["completed_steps"][-1]["elapsed"] = round(now - step_start_time, 1)
            step_start_time = now
            task["completed_steps"].append({"id": step, "message": msg, "started_at": now})
        task["steps"].append({"step": step, "progress": int(pct * 100), "message": msg})

    try:
        task["status"] = "running"
        task["current_step"] = f"开始预审核: {task['filename']}"

        from version_diff import DiffEngine

        engine_config = {
            "embedding": _state.app.config.get_section("embedding"),
            "llm": _state.app.config.get_llm_profile("pre_review"),
            "diff": _state.app.config.get_section("pre_review"),
            "cache": {
                "vector_cache_dir": os.path.join(_state.app.cache_dir, "vector_cache")
            },
        }
        engine = DiffEngine(config=engine_config)

        on_progress("model", 0.05, "加载向量模型...")
        log.info("开始加载向量模型...")
        await asyncio.to_thread(engine._get_model)
        log.info("向量模型加载完成")

        on_progress("loading", 0.1, "加载已有文档到引擎...")
        log.info(f"开始加载已有文档 ({_state.app.doc_store.total_documents} 篇)...")
        await asyncio.to_thread(load_existing_docs, engine)
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
        _state.app.save_review_cache()

        # 写入预审核结果缓存
        try:
            doc_names = sorted(d.filename for d in _state.app.doc_store.list_documents())
            doc_sig = "|".join(doc_names) + f"|{_state.app.doc_store.total_paragraphs}"
            cache_data = {
                "result": task["result"],
                "parsed_paragraphs": task.get("parsed_paragraphs", []),
                "filename": task["filename"],
                "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "doc_signature": doc_sig,
            }
            with open(cached_result_path, "w", encoding="utf-8") as _f:
                json.dump(cache_data, _f, ensure_ascii=False)
            log.info(f"💾 已缓存预审核结果: {task['filename']} (SHA256={file_md5[-8:].upper()})")
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
