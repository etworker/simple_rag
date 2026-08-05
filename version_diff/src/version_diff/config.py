"""配置定义"""
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable


@dataclass
class Config:
    """
    DiffEngine 配置

    所有参数均为 dict/基础类型，便于从 YAML/JSON/环境变量构造。

    Example:
        config = Config(
            embedding={"model": "BAAI/bge-base-zh-v1.5"},
            llm={"provider": "bedrock_converse", "model": "zai.glm-4.7-flash"},
            diff={"similarity_threshold": 0.80, "batch_size": 5},
        )
    """
    embedding: Dict[str, Any] = field(default_factory=lambda: {
        "model": "BAAI/bge-base-zh-v1.5",
    })

    llm: Dict[str, Any] = field(default_factory=lambda: {
        "provider": "bedrock_converse",
        "model": "zai.glm-4.7-flash",
        "region": "us-east-1",
        "api_key_env": "AWS_BEARER_TOKEN_BEDROCK",
        "max_tokens": 2048,
        "timeout": 120,
    })

    diff: Dict[str, Any] = field(default_factory=lambda: {
        "similarity_threshold": 0.80,
        "top_k": 3,
        "batch_size": 5,
        "max_workers": 4,
    })

    cache: Dict[str, Any] = field(default_factory=lambda: {
        # 为空则使用包内默认 .vector_cache 目录
        "vector_cache_dir": "",
    })

    judge: Dict[str, Any] = field(default_factory=lambda: {
        # 自定义 prompt 模板（为空则使用内置默认）
        # 模板中需包含 {count} 和 {items} 占位符
        "prompt_template": "",
        # 从外部文件加载 prompt（优先级高于 prompt_template）
        "prompt_file": "",
    })

    progress_callback: Optional[Callable] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """从字典构造配置"""
        return cls(
            embedding=d.get("embedding", {}),
            llm=d.get("llm", {}),
            diff=d.get("diff", {}),
            cache=d.get("cache", {}),
            judge=d.get("judge", {}),
        )
