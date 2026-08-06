"""
后端注册与发现
"""

from llm_chat.backends.bedrock import BedrockBackend
from llm_chat.backends.openai import OpenAIBackend

_BACKENDS = {
    "bedrock": BedrockBackend,
    "bedrock_converse": BedrockBackend,
    "openai": OpenAIBackend,
}


def get_backend(name: str, **kwargs):
    """
    获取后端实例

    Args:
        name: 后端名称 ("bedrock" | "openai")
        **kwargs: 后端配置参数

    Returns:
        Backend 实例（实现了 .chat() 方法）
    """
    cls = _BACKENDS.get(name)
    if cls is None:
        raise ValueError(f"未知后端: {name}，可选: {list(_BACKENDS.keys())}")
    return cls(**kwargs)
