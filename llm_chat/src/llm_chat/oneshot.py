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
