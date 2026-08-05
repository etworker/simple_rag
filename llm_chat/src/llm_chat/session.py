"""
ChatSession — 多轮对话管理（后端无关）

职责：
  - 维护多轮对话历史
  - 自动截断历史（防止 token 溢出）
  - 将请求分发到具体后端（Bedrock / OpenAI）
"""
import logging
from typing import List, Optional
from dataclasses import dataclass, field

from llm_chat.backends import get_backend
from llm_chat.defaults import DEFAULT_BACKEND, DEFAULT_MODELS, SESSION_DEFAULTS

log = logging.getLogger("llm_chat")


@dataclass
class Message:
    """一条对话消息"""
    role: str       # "user" | "assistant"
    content: str


class ChatSession:
    """
    多轮对话会话

    Args:
        system_prompt: 系统提示词
        backend: 后端类型 ("bedrock" | "openai")
        model: 模型名称
        max_history: 最大保留对话轮数
        **kwargs: 传给后端的额外参数（region, base_url, api_key_env, api_key, max_tokens, timeout）

    Example:
        session = ChatSession(system_prompt="你是助手", backend="bedrock", model="zai.glm-4.7-flash")
        answer = session.ask("什么是RAG？")
        answer = session.ask("能举个例子吗？")  # 自动携带上文
    """

    def __init__(
        self,
        system_prompt: str = "",
        backend: str = "",
        model: str = "",
        max_history: int = 0,
        **kwargs,
    ):
        backend = backend or DEFAULT_BACKEND
        model = model or DEFAULT_MODELS.get(backend, "")
        self.system_prompt = system_prompt
        self.model = model
        self.max_history = max_history or SESSION_DEFAULTS["max_history"]
        self.messages: List[Message] = []
        self._backend = get_backend(backend, model=model, **kwargs)

    def ask(self, question: str, context: str = "") -> str:
        """
        发送问题并获取回答

        Args:
            question: 用户问题
            context: 可选的参考资料文本（RAG 检索结果）

        Returns:
            LLM 回答文本
        """
        # 构建用户消息
        if context:
            user_content = f"参考资料：\n{context}\n\n用户问题：{question}"
        else:
            user_content = question

        self.messages.append(Message(role="user", content=user_content))

        # 构建完整对话序列
        conversation = self._build_conversation()

        # 调用后端
        try:
            answer = self._backend.chat(conversation, system_prompt=self.system_prompt)
        except RuntimeError:
            raise  # API Key 缺失等严重错误，让上层处理
        except Exception as e:
            log.error(f"LLM 调用失败: {e}")
            answer = f"[错误] LLM 调用失败: {e}"

        # 记录助手回复
        self.messages.append(Message(role="assistant", content=answer))

        # 截断历史
        self._truncate()

        return answer

    def reset(self):
        """清空对话历史"""
        self.messages = []

    def get_history(self) -> List[dict]:
        """获取对话历史"""
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def _build_conversation(self) -> List[dict]:
        """构建发送给后端的对话列表 [{role, content}, ...]"""
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def _truncate(self):
        """截断历史（保留最近 max_history 轮）"""
        max_msgs = self.max_history * 2
        if len(self.messages) > max_msgs:
            self.messages = self.messages[-max_msgs:]
