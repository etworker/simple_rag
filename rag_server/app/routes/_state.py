"""路由共享状态 — 由 main.py 通过 init() 注入到 AppState 单例"""

import json
import os

from loguru import logger as log


class AppState:
    """
    应用全局共享状态

    替代原来散落在模块级的 _doc_store / _config / _review_tasks 等全局变量，
    将所有运行时依赖集中在一个显式对象中，便于追踪和测试。
    """

    def __init__(self, doc_store, config, upload_dir: str, cache_dir: str = ""):
        self.doc_store = doc_store
        self.config = config
        self.upload_dir = upload_dir
        self.cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".simple_rag")
        os.makedirs(upload_dir, exist_ok=True)

        # 预审核任务状态
        self.review_tasks: dict = {}
        self.confirmed_or_rejected: set = set()
        self.qa_engine = None  # 由 init_qa() 设置

        # 预审核缓存路径
        self.review_cache_path = os.path.join(self.cache_dir, "review_tasks.json")
        self.review_result_cache = os.path.join(self.cache_dir, "review_results")
        os.makedirs(self.review_result_cache, exist_ok=True)
        self._load_review_cache()

    def save_review_cache(self):
        """将已完成的 review task 持久化（含段落、版本对比信息与确认状态）"""
        try:
            os.makedirs(os.path.dirname(self.review_cache_path), exist_ok=True)
            to_save = {}
            for tid, task in self.review_tasks.items():
                # 保存 done / confirmed / rejected 状态的 task
                status = task.get("status")
                if status in ("done", "confirmed", "rejected") and task.get("result"):
                    to_save[tid] = {
                        "status": status,
                        "filename": task["filename"],
                        "filepath": task["filepath"],
                        "file_hash": task.get("file_hash", ""),
                        "result": task["result"],
                        "parsed_paragraphs": task.get("parsed_paragraphs", []),
                        "old_version_filepath": task.get("old_version_filepath", ""),
                        "old_doc_filename": task.get("old_doc_filename", ""),
                    }
            # 同时持久化 confirmed_or_rejected 集合，重启后恢复
            cache = {"tasks": to_save, "confirmed_or_rejected": list(self.confirmed_or_rejected)}
            with open(self.review_cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_review_cache(self):
        """启动时恢复已完成的 review task 及确认状态"""
        if os.path.exists(self.review_cache_path):
            try:
                with open(self.review_cache_path, encoding="utf-8") as f:
                    cache = json.load(f)
                # 兼容旧格式（直接是 task dict）和新格式（{tasks, confirmed_or_rejected}）
                if isinstance(cache, dict) and "tasks" in cache:
                    tasks = cache["tasks"]
                    self.confirmed_or_rejected = set(cache.get("confirmed_or_rejected", []))
                else:
                    tasks = cache
                for tid, task in tasks.items():
                    self.review_tasks[tid] = task
                log.info(f"恢复 {len(tasks)} 个审核任务缓存")
            except Exception:
                pass


# 全局单例（由 main.py 通过 init() 设置）
app: AppState | None = None


def init(doc_store, config, upload_dir: str, cache_dir: str = ""):
    """初始化路由共享状态"""
    global app
    app = AppState(doc_store, config, upload_dir, cache_dir)


def init_qa(qa_engine):
    """设置 QA 引擎（单独步骤，避免循环导入）"""
    app.qa_engine = qa_engine
