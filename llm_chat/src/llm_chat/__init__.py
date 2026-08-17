"""
llm-chat — 轻量多后端 LLM 多轮对话管理

用法:
    from llm_chat import ChatSession

    # Bedrock Converse
    session = ChatSession(
        system_prompt="你是文档助手",
        backend="bedrock",
        model="zai.glm-4.7-flash",
        region="us-east-1",
        api_key_env="AWS_BEARER_TOKEN_BEDROCK",
    )

    # OpenAI 兼容
    session = ChatSession(
        system_prompt="你是文档助手",
        backend="openai",
        model="gpt-5.6-luna",
        base_url="https://bedrock-mantle.us-east-1.api.aws/openai/v1",
        api_key_env="OPENAI_API_KEY",
    )

    answer = session.ask("备份频率是多少？", context="...")
    answer = session.ask("那保留周期呢？")  # 自动带上对话历史
"""

from loguru import logger

logger.disable("llm_chat")  # 库默认安静：由应用入口(rag_server)或 examples 的 configure_logger() 开启

from llm_chat.defaults import (
    BEDROCK_DEFAULTS,
    DEFAULT_BACKEND,
    DEFAULT_MODELS,
    OPENAI_DEFAULTS,
    SESSION_DEFAULTS,
)
from llm_chat.oneshot import ask_once, ask_once_with_config
from llm_chat.profile import resolve_llm_config, resolve_llm_profile
from llm_chat.session import ChatSession, Message

__all__ = [
    "BEDROCK_DEFAULTS",
    "DEFAULT_BACKEND",
    "DEFAULT_MODELS",
    "OPENAI_DEFAULTS",
    "SESSION_DEFAULTS",
    "ChatSession",
    "Message",
    "ask_once",
    "ask_once_with_config",
    "resolve_llm_config",
    "resolve_llm_profile",
]
