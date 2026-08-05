"""
多轮对话管理 — 基于 llm_chat 包的薄封装

保持原有接口不变，内部委托给 llm_chat.ChatSession
"""
import logging
from typing import List
from dataclasses import dataclass, field

from llm_chat import ChatSession as _ChatSession
from llm_chat import Message  # re-export for convenience

log = logging.getLogger("rag_demo.chat")


@dataclass
class ChatSession:
    """
    多轮对话会话（兼容原有接口）

    Example:
        session = ChatSession(system_prompt="你是文档助手", llm_config={...})
        answer = session.ask("备份频率是多少？", context="...")
        answer = session.ask("那保留周期呢？")  # 有上文记忆
    """
    system_prompt: str = ""
    llm_config: dict = field(default_factory=dict)
    max_history: int = 20
    session_id: str = ""

    def __post_init__(self):
        # 从 llm_config 构造 llm_chat 的参数
        backend = self.llm_config.get("provider", "bedrock")
        if backend == "bedrock_converse":
            backend = "bedrock"

        kwargs = {
            "model": self.llm_config.get("model", "zai.glm-4.7-flash"),
            "region": self.llm_config.get("region", "us-east-1"),
            "api_key_env": self.llm_config.get("api_key_env", "AWS_BEARER_TOKEN_BEDROCK"),
            "api_key": self.llm_config.get("api_key", ""),
            "max_tokens": self.llm_config.get("max_tokens", 2048),
            "timeout": self.llm_config.get("timeout", 120),
        }
        # OpenAI 特有参数
        if backend == "openai":
            kwargs["base_url"] = self.llm_config.get("base_url", "https://api.openai.com/v1")
            kwargs["endpoint"] = self.llm_config.get("endpoint", "chat")

        self._session = _ChatSession(
            system_prompt=self.system_prompt,
            backend=backend,
            max_history=self.max_history,
            **kwargs,
        )

    def ask(self, question: str, context: str = "") -> str:
        """发送问题并获取回答"""
        return self._session.ask(question, context=context)

    @property
    def messages(self):
        """访问内部对话历史（兼容旧接口）"""
        return self._session.messages

    def reset(self):
        """清空对话历史"""
        self._session.reset()

    def get_history(self) -> List[dict]:
        """获取对话历史"""
        return self._session.get_history()
