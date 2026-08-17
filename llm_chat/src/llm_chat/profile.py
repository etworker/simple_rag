"""
LLM Profile 共享配置机制。

统一的「命名 LLM 配置」规范，供各模块（rag_server / version_diff / demo）复用，
避免每个模块各自手写 LLM 配置。

约定（与 rag_server 的 config.json 一致）：
    llm_profiles: dict[str, dict]     # 名字 -> 完整 LLM 配置（provider/model/base_url/api_key/...）
    llm_routing:  dict[str, str]      # 用途 -> profile 名

用法：
    profiles = {"glm": {"provider": "openai", "model": "...", "base_url": "..."}}
    routing  = {"version_compare": "glm", "qa": "glm"}
    cfg = resolve_llm_profile(profiles, routing, "version_compare")
    # -> {"provider": "openai", "model": "...", ...}（单 profile 配置）
"""

from __future__ import annotations

from typing import Any


def resolve_llm_profile(
    llm_profiles: dict[str, dict] | None,
    routing: dict[str, str] | None,
    use_case: str,
) -> dict[str, Any]:
    """按用途从 llm_profiles + llm_routing 解析出单个 LLM 配置。

    Args:
        llm_profiles: 命名 LLM 配置集合（名字 -> 完整配置）
        routing: 用途 -> profile 名
        use_case: 用途名（如 "version_compare" / "qa" / "pre_review"）

    Returns:
        单个 profile 的配置字典（深拷贝）。

    Raises:
        KeyError: 无可用 profile 时
    """
    profiles = llm_profiles or {}
    routing_map = routing or {}

    name = routing_map.get(use_case)
    if name and name in profiles:
        return dict(profiles[name])

    # 回退：取第一个 profile
    if profiles:
        return dict(next(iter(profiles.values())))

    raise KeyError(f"无可用 LLM profile（use_case={use_case}）")


def resolve_llm_config(llm: dict[str, Any] | None) -> dict[str, Any]:
    """归一化 LLM 配置为「单个 profile」形式。

    ``llm`` 支持两种形态：
        - 单配置：{"provider": "openai", "model": "..."} —— 原样返回
        - profile 引用：{"profile": "glm", "llm_profiles": {...}, "routing": {...}} —— 解析为单配置

    Args:
        llm: version_diff/demo 等传入的 llm 配置段

    Returns:
        单个 profile 配置字典。
    """
    if not llm:
        return {}
    if "profile" not in llm:
        return dict(llm)

    name = llm["profile"]
    profiles = llm.get("llm_profiles") or {}
    routing = llm.get("routing") or {}

    # 优先按 profile 名取；否则按 use_case（routing 里叫"default"的键）取
    if name in profiles:
        return dict(profiles[name])
    return resolve_llm_profile(profiles, routing, name)
