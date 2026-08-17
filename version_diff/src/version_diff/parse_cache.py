"""文档解析缓存（version_diff 层）。

以 文件 SHA256 + 解析配置签名 为 key 缓存 doc_parser 解析结果（Document JSON），
避免 version_compare 每次重新解析（docling 等慢后端百页分钟级）。

缓存文件：<cache_dir>/{sha256}_{config_sig}.json
  - sha256：文件内容哈希（内容变 → 失效）
  - config_sig：解析配置签名（切换解析后端 pymupdf/docling/... → 失效）
"""

import hashlib
import json
import os

from doc_parser import Document
from doc_parser import parse as _raw_parse
from loguru import logger as log

_DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".simple_rag", "parse_cache")


def compute_sha256(filepath: str) -> str:
    """文件 SHA256。"""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _config_sig(config) -> str:
    """解析配置签名（排序后哈希前 8 位）。"""
    return hashlib.sha256(json.dumps(config or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]


def cached_parse(filepath: str, config: dict | None = None, cache_dir: str | None = None) -> Document:
    """带缓存的文档解析（配置变化自动失效）。"""
    base_dir = cache_dir or _DEFAULT_CACHE_DIR
    os.makedirs(base_dir, exist_ok=True)
    file_hash = compute_sha256(filepath)
    cache_path = os.path.join(base_dir, f"{file_hash}_{_config_sig(config)}.json")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                doc = Document.from_dict(json.load(f))
            log.info(
                f"📦 命中解析缓存: {doc.filename} ({len(doc.paragraphs)} 段, "
                f"后端缓存 {os.path.basename(cache_path)[-16:]}"
            )
            return doc
        except Exception as e:
            log.warning(f"解析缓存加载失败，重新解析: {e}")

    doc = _raw_parse(filepath, config=config)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(doc.to_dict(), f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"解析缓存写入失败: {e}")
    return doc
