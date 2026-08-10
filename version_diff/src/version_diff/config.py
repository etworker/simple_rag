"""配置定义"""

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Config:
    """
    DiffEngine 配置

    所有参数均为 dict/基础类型，便于从 YAML/JSON/环境变量构造。
    默认值支持环境变量覆盖，便于 pip 包场景下由调用者统一配置。

    Example:
        config = Config(
            embedding={"model": "BAAI/bge-small-zh-v1.5"},
            llm={"provider": "bedrock_converse", "model": "zai.glm-4.7-flash"},
            diff={"similarity_threshold": 0.80, "batch_size": 5},
        )
    """

    embedding: dict[str, Any] = field(
        default_factory=lambda: {
            "model": os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
            "device": os.environ.get("SIMPLE_RAG_EMBEDDING_DEVICE", "auto"),
            # dtype: "auto" / "float16" / "bfloat16" / "float32"，仅在 device 非 cpu 时生效
            "dtype": os.environ.get("SIMPLE_RAG_EMBEDDING_DTYPE", ""),
            # 多 GPU 场景下的 GPU 设备 ID
            "gpu_id": 0,
        }
    )

    llm: dict[str, Any] = field(
        default_factory=lambda: {
            "provider": os.environ.get("LLM_BACKEND", "bedrock_converse"),
            "model": os.environ.get("LLM_MODEL", "zai.glm-4.7-flash"),
            "region": os.environ.get("AWS_REGION", "us-east-1"),
            "api_key_env": "AWS_BEARER_TOKEN_BEDROCK",
            "max_tokens": 2048,
            "timeout": 120,
            "max_retries": 3,
            "retry_backoff": 2.0,
        }
    )

    diff: dict[str, Any] = field(
        default_factory=lambda: {
            "similarity_threshold": 0.80,
            "top_k": 3,
            "batch_size": 5,
            # 版本管理「元数据噪声」过滤配置（通用、可覆盖）
            # added/removed 文本剥离下列 patterns 后为空 → 判为纯元数据噪声，归入 minor_changes
            "noise_filter": {
                "enabled": True,
                # 通用版本管理元数据正则（非行业词）：
                # 修订日期戳 / 独立日期 / 版本号·文件号 / 页码跟踪表行
                "patterns": [
                    r"修订日期\s*[：:]\s*\S+",
                    r"(?:^\s*|\b)\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}\s*(?:$|\b)",
                    r"(?:R\d+-\d{2,}|BK-J-\d+|版次\s*[：:]\s*\S+)",
                    r"^(?:\d{1,4}\s+)?(?:R\d{2,3}|N|A|D)\s+\d{4}[-./]\d{1,2}[-./]\d{1,2}",
                ],
            },
        }
    )

    cache: dict[str, Any] = field(
        default_factory=lambda: {
            # 为空则使用 ~/.simple_rag/vector_cache 目录
            "vector_cache_dir": "",
        }
    )

    judge: dict[str, Any] = field(
        default_factory=lambda: {
            # 自定义 prompt 模板（为空则使用内置默认）
            # 模板中需包含 {count} 和 {items} 占位符
            "prompt_template": "",
            # 从外部文件加载 prompt（优先级高于 prompt_template）
            "prompt_file": "",
        }
    )

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """从字典构造配置

        diff 段做浅合并：调用方只传部分键时，缺失键用默认值兜底
        （保证 noise_filter 等默认项在部分配置下仍生效）。
        """
        diff = d.get("diff", {})
        diff_default = {**cls().diff, **diff}
        return cls(
            embedding=d.get("embedding", {}),
            llm=d.get("llm", {}),
            diff=diff_default,
            cache=d.get("cache", {}),
            judge=d.get("judge", {}),
        )
