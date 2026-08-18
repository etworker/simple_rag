"""配置定义"""

import os
from dataclasses import dataclass, field
from typing import Any

from llm_chat.defaults import (
    BEDROCK_API_KEY_ENV,
    DEFAULT_LLM_MODEL,
    ENV_AWS_REGION,
    ENV_LLM_BACKEND,
    ENV_LLM_MODEL,
)
from loguru import logger as log

from version_diff.paths import (
    DEFAULT_EMBEDDING_MODEL,
    ENV_EMBEDDING_DEVICE,
    ENV_EMBEDDING_DTYPE,
    ENV_EMBEDDING_MODEL,
)


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
            "model": os.environ.get(ENV_EMBEDDING_MODEL, DEFAULT_EMBEDDING_MODEL),
            "device": os.environ.get(ENV_EMBEDDING_DEVICE, "auto"),
            # dtype: "auto" / "float16" / "bfloat16" / "float32"，仅在 device 非 cpu 时生效
            "dtype": os.environ.get(ENV_EMBEDDING_DTYPE, ""),
            # 多 GPU 场景下的 GPU 设备 ID
            "gpu_id": 0,
        }
    )

    llm: dict[str, Any] = field(
        default_factory=lambda: {
            "provider": os.environ.get(ENV_LLM_BACKEND, "bedrock_converse"),
            "model": os.environ.get(ENV_LLM_MODEL, DEFAULT_LLM_MODEL),
            "region": os.environ.get(ENV_AWS_REGION, "us-east-1"),
            "api_key_env": BEDROCK_API_KEY_ENV,
            "max_tokens": 2048,
            "timeout": 120,
            "max_retries": 3,
            "retry_backoff": 2.0,
        }
    )

    diff: dict[str, Any] = field(
        default_factory=lambda: {
            "similarity_threshold": 0.85,
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
            # 跨文档（不同级别/体例）内容差异的「版式噪声」过滤（内置、可配置）。
            # 目录/记录清单/页码占位等体例差异易被误判为内容差异，调用方可覆盖超参数。
            "cross_noise_filter": {
                "enabled": True,
                "patterns": [],  # 为空则用内置通用模式（目录条目/记录清单/页码占位等）
                "min_length": 6,
                "dir_entry_max_length": 40,
            },
            # 跟踪表过滤配置（修订记录表/有效页清单等自动更新的行）
            # 可覆盖 hints / row_patterns / version_stamp_patterns / summary_template
            "tracking_table": {
                # 跟踪表提示词正则（location 匹配）
                # "hints": r"有效页清单|修订记录表|发放清单|修改记录",
                # 跟踪表行匹配正则列表
                # "row_patterns": [...],
                # 版本戳正则列表（strip_revision_noise 用）
                # "version_stamp_patterns": [...],
                # 跟踪表行 summary 模板
                # "summary_template": "[页码跟踪] 跟踪表行自动更新",
            },
        }
    )

    cache: dict[str, Any] = field(
        default_factory=lambda: {
            # 为空则使用 ~/.simple_rag/vector_cache 目录
            "vector_cache_dir": "",
            # LLM 单条判断缓存目录；为空表示不启用持久化判断缓存
            "judge_cache_dir": "",
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

        llm 段支持两种形态（经 llm_chat.resolve_llm_config 归一化）：
            - 单配置：{"provider": "openai", "model": "..."}
            - profile 引用：{"profile": "glm", "llm_profiles": {...}, "routing": {...}}
              从 llm_profiles 共享配置中解析出单个 profile，实现各模块共用一套 LLM profile。
        """
        diff = d.get("diff", {})
        diff_default = {**cls().diff, **diff}
        llm = d.get("llm", {})
        try:
            from llm_chat import resolve_llm_config

            llm = resolve_llm_config(llm)
        except Exception as e:
            log.warning(f"LLM 配置解析失败，保留原始值: {e}")
        return cls(
            embedding=d.get("embedding", {}),
            llm=llm,
            diff=diff_default,
            cache=d.get("cache", {}),
            judge=d.get("judge", {}),
        )
