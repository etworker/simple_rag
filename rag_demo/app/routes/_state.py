"""路由共享状态 — 由 main.py 注入"""

import json
import logging
import os

from app.services.config_store import ConfigStore
from app.services.doc_store import DocStore

log = logging.getLogger("rag_demo.routes")

# 全局引用（由 main.py 注入）
_doc_store: DocStore | None = None
_config: ConfigStore | None = None
_upload_dir: str = ""
_cache_dir: str = ""

# 预审核任务状态
_review_tasks: dict = {}
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REVIEW_CACHE_PATH = os.path.join(_BASE_DIR, "data", "review_tasks.json")
_REVIEW_RESULT_CACHE = os.path.join(_BASE_DIR, "data", "review_results")
os.makedirs(_REVIEW_RESULT_CACHE, exist_ok=True)

router = None  # 由各路由模块创建


def _save_review_cache():
    """将已完成的 review task 持久化"""
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


def _cleanup_stale_uploads():
    """清理孤儿上传文件（无对应活跃任务的）"""
    import shutil

    if not _upload_dir or not os.path.exists(_upload_dir):
        return
    active_paths = {
        t.get("filepath", "")
        for t in _review_tasks.values()
        if t.get("status") in ("pending", "running")
    }
    removed = 0
    for sub_dir_name in os.listdir(_upload_dir):
        sub_path = os.path.join(_upload_dir, sub_dir_name)
        if not os.path.isdir(sub_path):
            continue
        has_active = False
        for f in os.listdir(sub_path):
            if os.path.join(sub_path, f) in active_paths:
                has_active = True
                break
        if not has_active:
            shutil.rmtree(sub_path, ignore_errors=True)
            removed += 1
    if removed:
        log.info(f"清理孤儿上传目录: {removed} 个")


def init(
    doc_store: DocStore, config: ConfigStore, upload_dir: str, cache_dir: str = ""
):
    """初始化路由依赖"""
    global _doc_store, _config, _upload_dir, _cache_dir
    _doc_store = doc_store
    _config = config
    _upload_dir = upload_dir
    _cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".simple_rag")
    os.makedirs(upload_dir, exist_ok=True)
    _cleanup_stale_uploads()

    global _REVIEW_CACHE_PATH, _REVIEW_RESULT_CACHE
    _REVIEW_CACHE_PATH = os.path.join(_cache_dir, "review_tasks.json")
    _REVIEW_RESULT_CACHE = os.path.join(_cache_dir, "review_results")
    os.makedirs(_REVIEW_RESULT_CACHE, exist_ok=True)
