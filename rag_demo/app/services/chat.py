"""
多轮对话管理 — 基于 llm_chat 包的薄封装

保持原有接口不变，内部委托给 llm_chat.ChatSession
"""

from dataclasses import dataclass, field

from llm_chat import ChatSession as _ChatSession
from llm_chat import Message  # noqa: F401 - re-export for tests


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

        # 透传所有 llm_config 参数给 llm_chat，由其内部 defaults 统一管理默认值
        kwargs = {k: v for k, v in self.llm_config.items() if k != "provider"}

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

    def get_history(self) -> list[dict]:
        """获取对话历史"""
        return self._session.get_history()
