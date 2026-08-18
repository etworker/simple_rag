"""
预审核后台任务执行器

从 review.py 拆分出来的 _run_pre_review + _load_existing_docs，
减轻路由文件体积（530 → ~340 行）。
"""

import asyncio
import copy
import hashlib
import json
import os
import time

from loguru import logger as log

from app.routes import _state
from app.services.utils import compute_sha256

_REVIEW_CACHE_VERSION = 2


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
    """计算知识库内容与全部有效审核输入的稳定签名。"""
    documents = sorted(
        ({"doc_id": d.doc_id, "file_hash": d.file_hash} for d in _state.app.doc_store.list_documents()),
        key=lambda item: item["doc_id"],
    )
    llm = _state.app.config.get_llm_profile("pre_review")
    llm_without_secrets = {
        key: value
        for key, value in llm.items()
        if key not in {"api_key", "api_key_env", "access_key", "secret_key", "token"}
    }
    parse_qa = _state.app.config.get_section("parse_qa")
    parse_qa_llm_without_secrets = {}
    if parse_qa.get("enabled", False):
        parse_qa_llm = _state.app.config.get_llm_profile(parse_qa.get("llm_profile", "pre_review"))
        parse_qa_llm_without_secrets = {
            key: value
            for key, value in parse_qa_llm.items()
            if key not in {"api_key", "api_key_env", "access_key", "secret_key", "token"}
        }
    judge = _state.app.config.get_section("judge")
    prompt_file = judge.get("prompt_file", "")
    if prompt_file:
        try:
            with open(prompt_file, "rb") as prompt_stream:
                judge["prompt_file_sha256"] = hashlib.sha256(prompt_stream.read()).hexdigest()
        except OSError:
            judge["prompt_file_sha256"] = "missing"
    payload = {
        "cache_version": _REVIEW_CACHE_VERSION,
        "documents": documents,
        "embedding": _state.app.config.get_section("embedding"),
        "pre_review": _state.app.config.get_section("pre_review"),
        "parse_cleanup": _state.app.config.get_section("parse_cleanup"),
        "parse_qa": parse_qa,
        "parse_qa_llm": parse_qa_llm_without_secrets,
        "judge": judge,
        "llm": llm_without_secrets,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_engine_config() -> dict:
    """构造 DiffEngine 配置（embedding / llm / diff / judge / cache）

    解析后端：pre_review.parse_backend（默认 auto，按文档特征选择；
    docling_device 控制 Docling 推理设备）。embedding 段注入 parse_config（带 extract 包装，
    与 doc_parser.get_extract_config 契约一致）供 engine/新文档解析使用。
    """
    embedding = _state.app.config.get_section("embedding")
    pre_review = _state.app.config.get_section("pre_review")
    parse_config = build_parse_config(pre_review)
    embedding = {**embedding, "parse_config": parse_config}
    return {
        "embedding": embedding,
        "llm": _state.app.config.get_llm_profile("pre_review"),
        "diff": pre_review,
        "judge": _state.app.config.get_section("judge"),
        "cache": {
            "vector_cache_dir": os.path.join(_state.app.cache_dir, "vector_cache"),
            "parse_cache_dir": os.path.join(_state.app.cache_dir, "parse_cache"),
        },
    }


def build_parse_config(pre_review: dict) -> dict:
    """根据 pre_review 段构造 doc_parser 解析配置（带 extract 包装）。

    Example:
        {"extract": {"backend": "docling", "docling_device": "cuda"}}
    """
    parse_backend = pre_review.get("parse_backend", "auto")
    extract = {"backend": parse_backend}
    if parse_backend == "docling":
        extract["docling_device"] = pre_review.get("docling_device", "auto")
        # 推理 batch（0=docling 默认 4；T4 可调 16-32 提升 GPU 利用率）
        batch = pre_review.get("docling_batch_size", 0)
        if batch:
            extract["docling_batch_size"] = int(batch)
        # 显式带上后处理开关（进缓存签名，逻辑变更时缓存自动失效）
        extract["docling_merge_split_paras"] = True
        extract["docling_strip_header_prefix"] = True
    return {"extract": extract}


def _run_version_compare(engine, old_version_filepath: str, new_filepath: str, on_progress=None) -> dict:
    """执行版本对比；失败时抛出异常，禁止把未完成误报为无差异。"""
    if not old_version_filepath or not os.path.exists(old_version_filepath):
        raise FileNotFoundError(f"旧版本文件不存在: {old_version_filepath}")
    log.info(f"检测到旧版本文档，启动版本对比: {old_version_filepath}")

    def _serialize(change) -> dict:
        return {
            "type": change.change_type,
            "category": change.category,
            "section": change.section,
            "location": change.location,
            "old_section": change.old_section,
            "old_location": change.old_location,
            "old_text": change.old_text,
            "new_text": change.new_text,
            "summary": change.summary,
            "similarity": change.similarity,
        }

    version_result = engine.version_compare(old_version_filepath, new_filepath, on_progress=on_progress)
    version_changes = [_serialize(change) for change in version_result.changes]
    minor_changes = [_serialize(change) for change in version_result.minor_changes]
    log.info(
        f"版本对比完成: {len(version_changes)} 实质性 + {len(minor_changes)} 细微变更 "
        f"(modified={sum(1 for c in version_changes if c['type'] == 'modified')}, "
        f"added={sum(1 for c in version_changes if c['type'] == 'added')}, "
        f"removed={sum(1 for c in version_changes if c['type'] == 'removed')})"
    )
    return {"changes": version_changes, "minor_changes": minor_changes}


def _serialize_inconsistency(inc) -> dict:
    """把 Inconsistency 转 dict（供前端展示）"""
    return {
        "point": getattr(inc, "point", ""),
        "doc_a_file": getattr(inc, "doc_a_file", ""),
        "doc_a_location": getattr(inc, "doc_a_location", ""),
        "doc_a_says": getattr(inc, "doc_a_says", ""),
        "doc_b_file": getattr(inc, "doc_b_file", ""),
        "doc_b_location": getattr(inc, "doc_b_location", ""),
        "doc_b_says": getattr(inc, "doc_b_says", ""),
        "similarity": getattr(inc, "similarity", 0.0),
    }


def _wait_if_paused(task, timeout: float = 0.3):
    """若任务已暂停则阻塞等待（线程内轮询 task 状态）。返回 False 表示已取消。"""
    import time as _time

    while task["status"] == "paused":
        if task["status"] == "cancelled":
            return False
        _time.sleep(timeout)
    return task["status"] != "cancelled"


def _run_multi_compare(
    engine,
    new_filepath: str,
    doc_list: list,
    task: dict,
    on_progress=None,
    version_threshold: float = 0.90,
) -> list:
    """与库内每个文档逐一比较，返回 compare_groups 列表。

    对每个已有文档 doc_meta：
      1. 快速相似度评估（document_similarity）
      2. >= version_threshold → 版本差异对比（version_compare）
      3. 否则 → 矛盾检测（单文档库 pre_review）
    每组结果追加到 compare_groups；调用方每收到一组即推送（渐进式）。
    循环中检查 task 状态（暂停/续跑/取消）。

    Args:
        doc_list: list[DocMeta]（库内已有文档，已按 add 顺序）
        task: review task dict（status 字段控制暂停/取消）
        on_progress: 进度回调
        version_threshold: 判定"疑似版本"的相似度阈值

    Returns:
        list[dict] — compare_groups（每项含 doc 信息 + 比较结果）
    """

    groups = []
    total = len(doc_list)
    new_filename = task.get("filename", "")

    # 1. 快速相似度排序（渐进式披露：最接近的先对比）
    scored = []
    scoring_errors = {}
    for idx, doc_meta in enumerate(doc_list):
        if task["status"] == "cancelled":
            break
        try:
            sim = engine.document_similarity(new_filepath, doc_meta.filepath)
        except Exception as e:
            log.error(f"相似度评估失败 {doc_meta.filename}: {e}", exc_info=True)
            scoring_errors[doc_meta.doc_id] = str(e)[:200]
            sim = 0.0
        scored.append((sim, doc_meta))
        if on_progress:
            on_progress("scoring", 0.1 + 0.1 * (idx + 1) / max(1, total), f"评估与 {doc_meta.filename} 的相似度...")
    scored.sort(key=lambda x: x[0], reverse=True)  # 相似度从高到低

    # 2. 逐个比较
    for i, (sim, doc_meta) in enumerate(scored):
        if not _wait_if_paused(task):
            break
        if task["status"] == "cancelled":
            break

        is_version = sim >= version_threshold
        kind = "version_diff" if is_version else "conflict"
        group = {
            "doc_id": doc_meta.doc_id,
            "doc_filename": doc_meta.filename,
            "label": doc_meta.label,
            "file_hash": doc_meta.file_hash,
            "similarity": round(sim, 3),
            "compare_type": kind,
            "version_changes": [],
            "minor_changes": [],
            "inconsistencies": [],
            "status": "running",
        }
        groups.append(group)

        if doc_meta.doc_id in scoring_errors:
            group["status"] = "error"
            group["error"] = f"文档相似度评估失败: {scoring_errors[doc_meta.doc_id]}"
            if on_progress:
                on_progress(
                    "group_done",
                    0.2 + 0.7 * (i + 1) / max(1, total),
                    f"跳过 {doc_meta.filename}：相似度评估失败",
                )
            continue

        step_label = "版本差异对比" if is_version else "内容一致性检查"
        if on_progress:
            on_progress(
                "comparing",
                0.2 + 0.7 * (i + 1) / max(1, total),
                f"[{i + 1}/{total}] {step_label} vs {doc_meta.filename}...",
            )
        log.info(f"[{i + 1}/{total}] 比较 {new_filename} vs {doc_meta.filename}（sim={sim:.3f}, {kind}）")

        try:
            if is_version:
                vr = _run_version_compare(engine, doc_meta.filepath, new_filepath, on_progress=on_progress)
                group["version_changes"] = vr["changes"]
                group["minor_changes"] = vr["minor_changes"]
            else:
                # 单文档库：新建引擎只含 doc_meta，pre_review 只对比它
                from version_diff import DiffEngine

                sub_engine = DiffEngine(config=_build_engine_config())
                sub_engine.add(doc_meta.filepath)
                result = sub_engine.pre_review(new_filepath, doc_filename=new_filename)
                group["inconsistencies"] = [_serialize_inconsistency(inc) for inc in result.inconsistencies]
            group["status"] = "done"
        except Exception as e:
            log.error(f"比较失败 {doc_meta.filename}: {e}", exc_info=True)
            group["status"] = "error"
            group["error"] = str(e)[:200]

        # 完成一组：调用方推送
        if on_progress:
            on_progress(
                "group_done",
                0.2 + 0.7 * (i + 1) / max(1, total),
                f"完成 {doc_meta.filename}（{len(group['version_changes'])} 变更 / "
                f"{len(group['inconsistencies'])} 矛盾）",
            )

    return groups


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
            cache_valid = (
                cached.get("cache_version") == _REVIEW_CACHE_VERSION
                and cache_doc_sig == doc_sig
                and not cached.get("result", {}).get("incomplete", False)
                and not cached.get("result", {}).get("parse_qa", {}).get("incomplete", False)
            )
            if not cache_valid:
                log.info(f"预审核缓存已过期（文档、配置或算法变化），重新执行: {task['filename']}")
            else:
                log.info(f"📦 命中预审核结果缓存: {task['filename']} (SHA256={file_md5[-8:].upper()})")
                task["status"] = "done"
                task["progress"] = 100
                task["current_step"] = "预审核完成（使用缓存）"
                task["result"] = copy.deepcopy(cached.get("result"))
                task["result"]["new_filename"] = task["filename"]
                task["parsed_paragraphs"] = copy.deepcopy(cached.get("parsed_paragraphs", []))
                for paragraph in task["parsed_paragraphs"]:
                    paragraph["source_file"] = task["filename"]
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
    parse_qa_cfg = _state.app.config.get_section("parse_qa") if _state.app and _state.app.config else {}
    parse_qa_enabled = bool(parse_qa_cfg.get("enabled", False))
    # 步骤列表在加载已有文档后按需调整（空知识库时跳过检索/差异/判定）
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
            # 新步骤开始：前一步标记完成（elapsed）
            if task["completed_steps"]:
                task["completed_steps"][-1]["elapsed"] = round(now - step_start_time, 1)
            step_start_time = now
            task["completed_steps"].append({"id": step, "message": msg, "started_at": now, "pct": int(pct * 100)})
        else:
            # 同步骤进度更新：刷新 pct/消息（供前端进度条）。
            # pct 只升不降（不同调用方可能以更低 pct 重申同一阶段，如 engine 内部 parsing 0.0）
            for _s in task["completed_steps"]:
                if _s["id"] == step:
                    _s["pct"] = max(_s.get("pct", 0), int(pct * 100))
                    _s["message"] = msg
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

        # 版本更新场景：同名文档新版本上传，只需版本对比，无需跨文档矛盾检测
        old_version_filepath = task.get("old_version_filepath", "")
        is_version_update = bool(old_version_filepath and os.path.exists(old_version_filepath))

        if not is_version_update:
            on_progress("loading", 0.1, "加载已有文档到引擎...")
            log.info(f"开始加载已有文档 ({_state.app.doc_store.total_documents} 篇)...")
            await asyncio.to_thread(load_existing_docs, engine)
            log.info("已有文档加载完成")

        # 知识库为空时：精简步骤列表，跳过检索/差异/判定，直接到汇总
        kb_empty = _state.app.doc_store.total_documents == 0
        if kb_empty or is_version_update:
            if is_version_update:
                # 步骤与 engine.version_compare 内部 on_progress 一致
                # （parsing/embedding/diffing/filtering/done），保证前端列表与实际执行同步
                all_steps = [
                    {"id": "parsing", "label": "解析文档"},
                    {"id": "embedding", "label": "计算语义向量"},
                    {"id": "diffing", "label": "版本差异对比"},
                    {"id": "filtering", "label": "过滤非实质性差异"},
                    {"id": "done", "label": "汇总结果"},
                ]
                log.info("检测到版本更新，跳过跨文档矛盾检测，直接执行版本对比")
            else:
                all_steps = [
                    {"id": "model", "label": "加载向量模型"},
                    {"id": "loading", "label": "加载已有文档"},
                    {"id": "parsing", "label": "解析文档"},
                    {"id": "embedding", "label": "计算语义向量"},
                    {"id": "done", "label": "汇总结果"},
                ]
                log.info("知识库为空，跳过跨文档检索/差异/判定步骤")
        if parse_qa_enabled:
            parsing_step = next((idx for idx, step in enumerate(all_steps) if step["id"] == "parsing"), None)
            if parsing_step is not None:
                all_steps.insert(parsing_step + 1, {"id": "parse_qa", "label": "解析质量检查"})
        task["all_steps"] = all_steps

        from app.services.parse_cache import cached_parse as _parse

        log.info(f"开始解析新文档: {task['filename']}")
        parse_cache_dir = os.path.join(_state.app.cache_dir, "parse_cache")
        # 解析配置与 version_compare 一致（pre_review.parse_backend，默认 auto）
        parse_config = build_parse_config(_state.app.config.get_section("pre_review"))
        # 进度推送：解析开始/完成都通知，避免左侧进度滞后于日志（解析期间 active）
        on_progress("parsing", 0.15, f"解析文档（{parse_config['extract'].get('backend', 'auto')}）...")
        new_doc = await asyncio.to_thread(_parse, filepath, config=parse_config, cache_dir=parse_cache_dir)

        # LLM 辅助页眉/页脚清洗（可选，默认关闭）：剥离段首页眉残留
        # （如表格区域内绕过坐标剔除的页眉行）。不写回 parse_cache，
        # 每次解析后重跑（幂等）；配置 parse_cleanup.enabled=true 启用。
        cleanup_cfg = _state.app.config.get_section("parse_cleanup") if _state.app and _state.app.config else {}
        if cleanup_cfg.get("enabled", False):
            from app.services.llm_cleanup import clean_headers

            new_doc = await asyncio.to_thread(
                clean_headers, new_doc, {**cleanup_cfg, "config_path": _state.app.config._config_path}
            )

        if parse_qa_enabled:
            on_progress("parse_qa", 0.26, "检查解析结果质量...")
            try:
                from app.services.parse_qa import review_document_parse_quality

                llm_profile = parse_qa_cfg.get("llm_profile", "pre_review")
                llm_config = _state.app.config.get_llm_profile(llm_profile)
                qa_report = await asyncio.to_thread(
                    review_document_parse_quality,
                    new_doc,
                    llm_config,
                    parse_qa_cfg,
                )
            except Exception as exc:
                # QA 是旁路能力：配置或 LLM 异常不能阻断原有预审核，保留规则报告并标记不完整。
                from doc_parser.qa import inspect_document

                log.warning("解析质量检查失败，保留规则报告并标记 incomplete: {}", exc)
                qa_report = inspect_document(new_doc, parse_qa_cfg)
                qa_report.incomplete = True
            task["parse_qa"] = qa_report.to_dict(include_markdown=False)
            on_progress("parse_qa", 0.30, f"解析质量检查完成: {qa_report.status}")

        on_progress("parsing", 0.30, f"解析完成: {len(new_doc.paragraphs)} 段")
        log.info(f"新文档解析完成: {len(new_doc.paragraphs)} 段落")
        # ★ 用 task["filename"]（原始上传名）作为 source_file，
        #   确保前端 /review/paragraphs?file=原名 能匹配到段落
        task["parsed_paragraphs"] = [
            {"text": p.text, "location": p.location, "source_file": task["filename"]} for p in new_doc.paragraphs
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

        # ====== 统一流程：与库内每个文档逐一比较（渐进式披露）======
        # 结果结构 compare_groups：每组对应"新文档 vs 库内某文档"，
        # 相似度 >= 阈值 → 版本差异对比；否则 → 内容一致性检查。
        # 每完成一组即推送（前端逐步显示 fold），支持暂停/续跑/取消。
        task["result"] = {
            "phase": "scoring",
            "is_safe": True,
            "new_filename": task["filename"],
            "compare_groups": [],
            "compare_total": 0,
            "compare_done": 0,
            "message": "正在评估与库内文档的相似度...",
        }
        _bump_result()

        # 库内已有文档列表（含同名旧版，作为一组参与比较）
        doc_list = _state.app.doc_store.list_documents()
        if old_version_filepath and os.path.exists(old_version_filepath):
            has_old = any(d.filepath == old_version_filepath for d in doc_list)
            if not has_old:
                from app.services.doc_store import DocMeta

                old_meta = DocMeta(
                    filename=task.get("old_doc_filename", "旧版本"),
                    filepath=old_version_filepath,
                    doc_id=task.get("old_doc_filename", "旧版本"),
                )
                doc_list = [old_meta, *doc_list]

        task["result"]["compare_total"] = len(doc_list)
        _bump_result()

        def _on_group_progress(step, percent, message):
            """逐文档比较进度回调：更新 result 并推送（渐进式）"""
            r = task["result"]
            r["phase"] = step if step in ("scoring", "comparing", "group_done") else r.get("phase", "comparing")
            r["progress"] = round(percent * 100)
            r["message"] = message
            if step == "group_done":
                r["compare_done"] = r.get("compare_done", 0) + 1
            task["progress"] = max(task.get("progress", 0), round(percent * 100))
            on_progress(step, percent, message)
            _bump_result()
            log.info(f"  增量推送: {message}")

        _thr = float(
            _state.app.config.get_section("pre_review").get("version_similarity_threshold", 0.90)
            if _state.app and _state.app.config
            else 0.90
        )
        compare_groups = await asyncio.to_thread(
            _run_multi_compare,
            engine,
            filepath,
            doc_list,
            task,
            on_progress=_on_group_progress,
            version_threshold=_thr,
        )

        n_version = sum(1 for g in compare_groups if g["compare_type"] == "version_diff")
        n_conflict = sum(1 for g in compare_groups if g["compare_type"] == "conflict")
        n_issue = sum(
            1 for g in compare_groups if len(g.get("version_changes", [])) > 0 or len(g.get("inconsistencies", [])) > 0
        )
        n_error = sum(1 for g in compare_groups if g.get("status") == "error")
        cancelled = task.get("status") == "cancelled"
        incomplete = n_error > 0
        task["progress"] = 100 if not cancelled else task.get("progress", 0)
        if cancelled:
            task["current_step"] = "已取消"
            task["status"] = "cancelled"
            message = "已取消"
        elif incomplete:
            task["current_step"] = f"预审核未完成：{n_error} 组比较失败"
            task["status"] = "error"
            message = f"有 {n_error} 组比较失败，不能判定为安全，请修复后重跑"
        else:
            task["current_step"] = "预审核完成"
            task["status"] = "done"
            message = "无矛盾，可安全入库" if n_issue == 0 else f"发现 {n_issue} 组存在差异/矛盾"

        task["result"] = {
            "phase": "error" if incomplete else "done",
            "is_safe": not incomplete and n_issue == 0,
            "incomplete": incomplete,
            "new_filename": task["filename"],
            "compare_groups": compare_groups,
            "compare_total": len(compare_groups),
            "compare_done": sum(1 for g in compare_groups if g.get("status") == "done"),
            "n_version_groups": n_version,
            "n_conflict_groups": n_conflict,
            "n_issue_groups": n_issue,
            "n_error_groups": n_error,
            "message": message,
            "kb_empty": kb_empty,
            "cancelled": cancelled,
            "parse_qa": task.get("parse_qa"),
        }
        _state.app.save_review_cache()

        # 只缓存完整成功的审核结果；错误或取消结果必须重新计算。
        if not cancelled and not incomplete:
            try:
                doc_sig = _compute_doc_signature()
                cache_data = {
                    "cache_version": _REVIEW_CACHE_VERSION,
                    "result": task["result"],
                    "parsed_paragraphs": task.get("parsed_paragraphs", []),
                    "filename": task["filename"],
                    "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "doc_signature": doc_sig,
                }
                tmp_cache_path = f"{cached_result_path}.tmp"
                with open(tmp_cache_path, "w", encoding="utf-8") as _f:
                    json.dump(cache_data, _f, ensure_ascii=False)
                os.replace(tmp_cache_path, cached_result_path)
                log.info(f"已缓存预审核结果: {task['filename']} (SHA256={file_md5[-8:].upper()})")
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
