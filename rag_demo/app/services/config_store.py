"""
配置管理 — 运行时可动态修改

支持:
  - LLM 模型/参数配置
  - Embedding 模型配置
  - 检索参数（top_k, threshold）
  - Prompt 模板
  - 预审核参数
"""

import json
import logging
import os
from copy import deepcopy
from typing import Any

log = logging.getLogger("rag_demo.config")

# 默认缓存根目录
_DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".simple_rag")

# 默认配置
DEFAULT_CONFIG = {
    "cache": {
        "base_dir": _DEFAULT_CACHE_DIR,
        "upload_dir": "",  # 空=默认 ~/.simple_rag/uploads/
    },
    "embedding": {
        "model": "BAAI/bge-base-zh-v1.5",
        "cache_dir": "",
    },
    "llm": {
        "provider": "bedrock_converse",
        "model": "zai.glm-4.7-flash",
        "region": "us-east-1",
        "api_key_env": "AWS_BEARER_TOKEN_BEDROCK",
        "max_tokens": 2048,
        "timeout": 120,
        "max_retries": 3,
        "retry_backoff": 2.0,
    },
    "retrieval": {
        "top_k": 5,
        "similarity_threshold": 0.5,
    },
    "pre_review": {
        "similarity_threshold": 0.80,
        "batch_size": 5,
    },
    "conflict_detection": {
        "min_score": 0.7,
        "min_similarity": 0.5,
        "max_similarity": 0.95,
    },
    "prompts": {
        "system": (
            "你是一个企业文档问答助手。根据提供的参考资料回答用户问题。\n"
            "要求：\n"
            "1. 仅基于参考资料回答，不要编造信息\n"
            "2. 回答末尾标注来源（完整文档文件名+位置），文档文件名必须完整保留，不得省略任何前缀或后缀\n"
            "3. 如果参考资料中有矛盾描述，必须醒目指出\n"
            "4. 如果参考资料不足以回答，明确告知用户\n"
            "5. 对于关于知识库本身的元数据问题（如共几篇文档），根据参考资料的来源信息如实回答"
        ),
        "context_template": "[来源: {source} {location}]\n{text}\n",
        "conflict_warning": "\n⚠️ 注意：以下文档对此问题的描述存在矛盾：\n{conflicts}\n请以最新版本或上级文档为准。",
    },
    "chat": {
        "max_history": 20,
    },
}


class ConfigStore:
    """运行时配置存储"""

    def __init__(self, config_path: str = None):
        self._config = deepcopy(DEFAULT_CONFIG)
        self._config_path = config_path
        if config_path and os.path.exists(config_path):
            self.load(config_path)

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值，支持点号分隔的 key

        Example:
            config.get("llm.model")  → "zai.glm-4.7-flash"
            config.get("retrieval.top_k")  → 5
        """
        keys = key.split(".")
        val = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def set(self, key: str, value: Any):
        """设置配置值"""
        keys = key.split(".")
        d = self._config
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        log.info(f"配置更新: {key} = {value}")

    def get_section(self, section: str) -> dict:
        """获取整个配置段"""
        return deepcopy(self._config.get(section, {}))

    def to_dict(self) -> dict:
        """导出完整配置"""
        return deepcopy(self._config)

    def update(self, updates: dict):
        """批量更新配置"""
        self._deep_merge(self._config, updates)

    def save(self, path: str = None):
        """保存配置到文件"""
        path = path or self._config_path
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            log.info(f"配置已保存: {path}")

    def load(self, path: str):
        """从文件加载配置"""
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self._deep_merge(self._config, loaded)
        self._config_path = path
        log.info(f"配置已加载: {path}")

    @staticmethod
    def _deep_merge(base: dict, override: dict):
        """深度合并字典"""
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                ConfigStore._deep_merge(base[k], v)
            else:
                base[k] = v
