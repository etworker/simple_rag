"""
单次 LLM 调用（无状态，不保留历史）

适用于：分类、判断、摘要生成等一次性任务
"""

from llm_chat.backends import get_backend
from llm_chat.defaults import DEFAULT_BACKEND, DEFAULT_MODELS


def ask_once(
    prompt: str,
    system_prompt: str = "",
    backend: str = "",
    model: str = "",
    **kwargs,
) -> str:
    """
    单次 LLM 调用

    Args:
        prompt: 用户 prompt
        system_prompt: 系统提示词（可选）
        backend: 后端类型 ("bedrock" | "openai")
        model: 模型名称
        **kwargs: 后端参数（region, base_url, api_key_env, max_tokens, timeout）

    Returns:
        LLM 回复文本

    Example:
        from llm_chat import ask_once

        result = ask_once(
            "这两段话是否矛盾？A说每周备份，B说每月备份。",
            model="zai.glm-4.7-flash",
        )
    """
    backend = backend or DEFAULT_BACKEND
    model = model or DEFAULT_MODELS.get(backend, "")
    be = get_backend(backend, model=model, **kwargs)
    messages = [{"role": "user", "content": prompt}]
    return be.chat(messages, system_prompt=system_prompt)


def ask_once_with_config(llm_config: dict, prompt: str, system_prompt: str = "") -> str:
    """
    从 llm_config 字典解析参数并单次调用 LLM

    llm_config 的 "provider" 字段映射为后端名，"model" 单独取出，
    其余字段（region / base_url / api_key_env / api_key / max_tokens / timeout /
    endpoint / max_retries / retry_backoff / ...）透传给后端，由后端 defaults
    统一管理默认值。

    消除调用方手写字段透传的样板代码（如 version_diff.llm_util.call_llm_json）。

    Args:
        llm_config: LLM 配置字典（已解析的单个 profile，含 provider/model/...）
        prompt: 用户 prompt
        system_prompt: 系统提示词（可选）

    Returns:
        LLM 回复文本

    Example:
        from llm_chat import ask_once_with_config

        result = ask_once_with_config(
            {"provider": "openai", "model": "gpt-4o", "api_key": "sk-..."},
            "这两段话是否矛盾？",
        )
    """
    # 委托 ask_once（函数内 import，使 mock.patch("llm_chat.ask_once") 仍能拦截，
    # 同时复用 ask_once 的后端构造与默认值逻辑，零重复）
    from llm_chat import ask_once

    cfg = dict(llm_config)
    backend = cfg.pop("provider", "") or DEFAULT_BACKEND
    model = cfg.pop("model", "") or DEFAULT_MODELS.get(backend, "")
    return ask_once(prompt, system_prompt=system_prompt, backend=backend, model=model, **cfg)
