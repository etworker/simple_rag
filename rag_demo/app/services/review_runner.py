"""
预审核后台任务执行器

从 review.py 拆分出来的 _run_pre_review + _load_existing_docs，
减轻路由文件体积（530 → ~340 行）。
"""

import asyncio
import copy
import json
import os
import time

from loguru import logger as log

from app.routes import _state
from app.services.utils import compute_sha256


def load_existing_docs(engine):
    """同步加载已有文档到引擎（在线程中执行）"""
    # ★ Fix: 文件缺失或损坏时跳过，不中断整个预审核流程
    for doc_meta in _state.app.doc_store.list_documents():
        if not doc_meta.filepath or not os.path.exists(doc_meta.filepath):
            log.warning(f"跳过加载已有文档（文件不存在）: {doc_meta.filename} -> {doc_meta.filepath}")
            continue
        try:
            engine.add(doc_meta.filepath)
            # ★ 将 source_file 从 SHA256 哈希名替换为可读的原始文件名
            hash_key = os.path.basename(doc_meta.filepath)
            doc = engine._documents.get(hash_key)
            if doc and doc_meta.filename:
                doc.filename = doc_meta.filename
                engine._documents[doc_meta.filename] = doc
                engine._documents.pop(hash_key, None)
                for p in doc.paragraphs:
                    p.source_file = doc_meta.filename
                for t in doc.tables:
                    t.source_file = doc_meta.filename
        except Exception as e:
            log.error(f"加载已有文档失败（跳过）: {doc_meta.filename}: {e}")


def _compute_doc_signature() -> str:
    """计算已有文档库的签名（用于预审核结果缓存失效判断）"""
    doc_names = sorted(d.filename for d in _state.app.doc_store.list_documents())
    return "|".join(doc_names) + f"|{_state.app.doc_store.total_paragraphs}"


def _build_engine_config() -> dict:
    """构造 DiffEngine 配置（embedding / llm / diff / judge / cache）"""
    return {
        "embedding": _state.app.config.get_section("embedding"),
        "llm": _state.app.config.get_llm_profile("pre_review"),
        "diff": _state.app.config.get_section("pre_review"),
        "judge": _state.app.config.get_section("judge"),
        "cache": {
            "vector_cache_dir": os.path.join(_state.app.cache_dir, "vector_cache")
        },
    }


def _run_version_compare(engine, old_version_filepath: str, new_filepath: str) -> dict:
    """若提供旧版本文档，执行版本对比并返回变更列表（失败返回空结果）

    返回: {
        "changes": [...],         # 实质性变更
        "minor_changes": [...],   # 被过滤的细微变更（跟踪表 / 修订日期等）
    }
    """
    if not old_version_filepath or not os.path.exists(old_version_filepath):
        return {"changes": [], "minor_changes": []}
    log.info(f"检测到旧版本文档，启动版本对比: {old_version_filepath}")

    def _serialize(change) -> dict:
        return {
            "type": change.change_type,
            "category": change.category,
            "section": change.section,
            "location": change.location,
            "old_section": change.old_section,
            "old_location": change.old_location,
            "old_text": change.old_text[:300],
            "new_text": change.new_text[:300],
            "summary": change.summary,
            "similarity": change.similarity,
        }

    version_changes = []
    minor_changes = []
    try:
        version_result = engine.version_compare(old_version_filepath, new_filepath)
        for change in version_result.changes:
            version_changes.append(_serialize(change))
        for change in version_result.minor_changes:
            minor_changes.append(_serialize(change))
        log.info(
            f"版本对比完成: {len(version_changes)} 实质性 + {len(minor_changes)} 细微变更 "
            f"(modified={sum(1 for c in version_changes if c['type']=='modified')}, "
            f"added={sum(1 for c in version_changes if c['type']=='added')}, "
            f"removed={sum(1 for c in version_changes if c['type']=='removed')})"
        )
    except Exception as e:
        log.error(f"版本对比失败: {e}", exc_info=True)
    return {"changes": version_changes, "minor_changes": minor_changes}


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
            with open(cached_result_path, encoding="utf-8") as _f:
                cached = json.load(_f)
            doc_sig = _compute_doc_signature()
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

        engine = DiffEngine(config=_build_engine_config())

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
        parse_cache_dir = os.path.join(_state.app.cache_dir, "parse_cache")
        new_doc = await asyncio.to_thread(_parse, filepath, cache_dir=parse_cache_dir)
        log.info(f"新文档解析完成: {len(new_doc.paragraphs)} 段落")
        # ★ 用 task["filename"]（原始上传名）作为 source_file，
        #   确保前端 /review/paragraphs?file=原名 能匹配到段落
        task["parsed_paragraphs"] = [
            {"text": p.text, "location": p.location, "source_file": task["filename"]}
            for p in new_doc.paragraphs
        ]

        # ====== 增量结果推送准备 ======
        # task["result"] 在 pre_review 过程中被逐步填充：
        #   phase="candidates_ready" → embedding/search 完成，显示 N 个候选
        #   phase="judging"         → LLM 分批判定中，inconsistencies 逐步追加
        #   phase="done"            → 完成，is_safe 等字段就绪
        task["_result_seq"] = 0  # 每次 result 修改时递增，SSE 检测此值决定是否推送

        def _bump_result():
            """原子地递增 _result_seq，让 SSE 推送此次 result 变更"""
            task["_result_seq"] = task.get("_result_seq", 0) + 1

        # 初始化 result 结构（前端可据此渲染"等待中"状态）
        task["result"] = {
            "phase": "embedding",
            "is_safe": True,
            "new_filename": task["filename"],
            "inconsistencies": [],
            "total_candidates": 0,
            "diff_items": 0,
            "rule_filtered": 0,
            "llm_judged": 0,
            "judge_total_batches": 0,
            "judge_current_batch": 0,
            "last_batch_new_count": 0,
            "message": "正在处理...",
        }
        _bump_result()

        def on_candidates(cand_count: int, diff_count: int):
            """候选对检索完成后调用 — 前端立即显示 N 个候选 + 预估批次"""
            # 预估 batch 数量（与 filter_diffs 内部逻辑保持一致）
            bs = engine.config.llm.get("batch_size", 5) or 5
            import math
            est_batches = max(1, math.ceil(diff_count / bs)) if diff_count > 0 else 0
            task["result"]["phase"] = "candidates_ready"
            task["result"]["total_candidates"] = cand_count
            task["result"]["diff_items"] = diff_count
            task["result"]["judge_total_batches"] = est_batches
            task["result"]["message"] = (
                f"发现 {cand_count} 个相似候选对，{diff_count} 处文本差异，"
                f"开始 LLM 判定（约 {est_batches} 批）..."
            )
            _bump_result()
            log.info(f"  增量推送: 候选就绪 {cand_count} 候选, {diff_count} 差异")

        def on_judge_batch(batch_idx: int, total_batches: int, new_dicts: list):
            """每批 LLM 完成后调用 — 增量追加到 inconsistencies"""
            r = task["result"]
            r["phase"] = "judging"
            r["inconsistencies"].extend(new_dicts)
            r["judge_current_batch"] = batch_idx + 1
            r["judge_total_batches"] = total_batches
            r["last_batch_new_count"] = len(new_dicts)
            r["llm_judged"] = r.get("llm_judged", 0) + sum(
                1 for _ in range(len(new_dicts))  # simplify: add new item counts
            )
            cur_count = len(r["inconsistencies"])
            if cur_count > 0:
                r["message"] = f"LLM 判定中... 已发现 {cur_count} 处矛盾（batch {batch_idx + 1}/{total_batches}）"
            else:
                r["message"] = f"LLM 判定中... 暂无矛盾（batch {batch_idx + 1}/{total_batches}）"
            r["is_safe"] = cur_count == 0
            _bump_result()

        result = await asyncio.to_thread(
            engine.pre_review, filepath, on_progress=on_progress, doc_filename=task["filename"],
            on_candidates=on_candidates,
            on_judge_batch=on_judge_batch,
        )

        # 标记知识库是否为空（无任何已有文档可供对比）
        kb_empty = (result.total_candidates == 0 and result.llm_judged == 0
                    and len(result.inconsistencies) == 0)

        # ====== 3. 版本对比（如果存在旧版本文档）======
        old_version_filepath = task.get("old_version_filepath", "")
        version_compare_result = _run_version_compare(engine, old_version_filepath, filepath)

        task["progress"] = 100
        task["current_step"] = "预审核完成"
        task["status"] = "done"
        task["result"] = {
            "phase": "done",
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
            "total_candidates": result.total_candidates,
            "rule_filtered": result.rule_filtered,
            "llm_judged": result.llm_judged,
            "message": "无矛盾，可安全入库"
            if result.is_safe
            else f"发现 {len(result.inconsistencies)} 处矛盾",
            "version_changes": version_compare_result["changes"],
            "minor_changes": version_compare_result["minor_changes"],
            "has_version_changes": len(version_compare_result["changes"]) > 0,
            "has_minor_changes": len(version_compare_result["minor_changes"]) > 0,
            "kb_empty": kb_empty,
            "no_candidates": (not kb_empty and result.total_candidates == 0 and result.llm_judged == 0),
        }
        _state.app.save_review_cache()

        # 写入预审核结果缓存
        try:
            doc_sig = _compute_doc_signature()
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
