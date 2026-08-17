"""向后兼容：re-export llm_chat 的 ChatSession 和 Message

qa_engine 已直接使用 llm_chat.ChatSession，此模块仅为兼容已有 import。
"""

from llm_chat import ChatSession, Message

__all__ = ["ChatSession", "Message"]
