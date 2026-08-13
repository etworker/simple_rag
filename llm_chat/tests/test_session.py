"""
ChatSession 单元测试

测试策略：
  - Mock 后端，不实际调用 LLM
  - 验证会话管理、历史截断、context 拼接等核心逻辑
"""

from unittest.mock import MagicMock, patch

import pytest

from llm_chat import ChatSession


class FakeBackend:
    """测试用假后端"""

    def __init__(self, **kwargs):
        self.calls = []

    def chat(self, messages, system_prompt=""):
        self.calls.append({"messages": messages, "system_prompt": system_prompt})
        # 返回固定回答
        return f"回答: 收到 {len(messages)} 条消息"


@pytest.fixture
def session():
    """创建使用假后端的 session"""
    with patch("llm_chat.session.get_backend", return_value=FakeBackend()):
        s = ChatSession(system_prompt="你是测试助手", backend="bedrock", model="test")
    return s


class TestChatSession:
    """ChatSession 核心功能测试"""

    def test_basic_ask(self, session):
        """基本问答"""
        answer = session.ask("你好")
        assert "回答" in answer
        assert len(session.messages) == 2  # 1 user + 1 assistant

    def test_multi_turn(self, session):
        """多轮对话历史累积"""
        session.ask("第一个问题")
        session.ask("第二个问题")
        session.ask("第三个问题")
        assert len(session.messages) == 6  # 3 轮 × 2

    def test_context_injection(self, session):
        """context 参数注入到用户消息"""
        session.ask("什么是RAG？", context="RAG = 检索增强生成")
        user_msg = session.messages[0].content
        assert "参考资料" in user_msg
        assert "RAG = 检索增强生成" in user_msg
        assert "什么是RAG？" in user_msg

    def test_no_context(self, session):
        """不传 context 时只有问题"""
        session.ask("你好吗")
        assert session.messages[0].content == "你好吗"

    def test_history_truncation(self):
        """历史截断"""
        with patch("llm_chat.session.get_backend", return_value=FakeBackend()):
            s = ChatSession(system_prompt="", backend="bedrock", model="t", max_history=3)

        # 发送 5 轮（每轮 2 条），应截断为最近 3 轮（6 条）
        for i in range(5):
            s.ask(f"问题{i}")

        assert len(s.messages) == 6  # max_history=3 → 保留 6 条
        assert s.messages[0].content == "问题2"  # 前两轮被截断

    def test_reset(self, session):
        """重置清空历史"""
        session.ask("问题")
        assert len(session.messages) == 2
        session.reset()
        assert len(session.messages) == 0

    def test_get_history(self, session):
        """获取历史格式"""
        session.ask("你好")
        history = session.get_history()
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_backend_receives_correct_messages(self, session):
        """后端收到正确的消息格式"""
        backend = session._backend
        session.ask("测试问题")
        call = backend.calls[0]
        assert call["system_prompt"] == "你是测试助手"
        assert call["messages"][0]["role"] == "user"
        assert call["messages"][0]["content"] == "测试问题"

    def test_backend_error_handling(self):
        """后端异常时应向上抛出，由上层转换为可控的 HTTP 错误（而非返回 [错误] 文本当答案）"""

        class ErrorBackend:
            def __init__(self, **kw):
                pass

            def chat(self, messages, system_prompt=""):
                raise ConnectionError("网络超时")

        with patch("llm_chat.session.get_backend", return_value=ErrorBackend()):
            s = ChatSession(system_prompt="", backend="bedrock", model="t")

        with pytest.raises(ConnectionError):
            s.ask("会失败的问题")


class TestBackendRegistry:
    """后端注册测试"""

    def test_unknown_backend_raises(self):
        """未知后端报错"""
        from llm_chat.backends import get_backend

        with pytest.raises(ValueError, match="未知后端"):
            get_backend("不存在的后端")

    def test_bedrock_backend_instantiation(self):
        """Bedrock 后端可正常实例化"""
        from llm_chat.backends import get_backend

        backend = get_backend("bedrock", model="test-model", region="us-west-2")
        assert backend.model == "test-model"
        assert backend.region == "us-west-2"

    def test_openai_backend_instantiation(self):
        """OpenAI 后端可正常实例化"""
        from llm_chat.backends import get_backend

        backend = get_backend("openai", model="gpt-4o", base_url="http://localhost:8080/v1")
        assert backend.model == "gpt-4o"
        assert "localhost" in backend.base_url

    def test_bedrock_alias(self):
        """bedrock_converse 是 bedrock 的别名"""
        from llm_chat.backends import get_backend

        backend = get_backend("bedrock_converse", model="x")
        assert backend.__class__.__name__ == "BedrockBackend"


class TestBedrockBackend:
    """Bedrock 后端单元测试"""

    def test_resolve_key_from_env(self, monkeypatch):
        """从环境变量获取 key"""
        from llm_chat.backends.bedrock import BedrockBackend

        monkeypatch.setenv("TEST_KEY_ENV", "my-secret-key")
        backend = BedrockBackend(api_key_env="TEST_KEY_ENV")
        assert backend._resolve_key() == "my-secret-key"

    def test_resolve_key_fallback(self, monkeypatch):
        """环境变量为空时 fallback 到直传"""
        from llm_chat.backends.bedrock import BedrockBackend

        monkeypatch.delenv("NONEXISTENT_ENV", raising=False)
        backend = BedrockBackend(api_key_env="NONEXISTENT_ENV", api_key="direct-key")
        assert backend._resolve_key() == "direct-key"

    def test_no_key_raises(self, monkeypatch):
        """无 key 时报错"""
        from llm_chat.backends.bedrock import BedrockBackend

        monkeypatch.delenv("NONEXISTENT", raising=False)
        backend = BedrockBackend(api_key_env="NONEXISTENT", api_key="")
        with pytest.raises(RuntimeError, match="未配置"):
            backend.chat([{"role": "user", "content": "hi"}])


class TestOpenAIBackend:
    """OpenAI 后端单元测试"""

    def test_resolve_key_from_env(self, monkeypatch):
        """从环境变量获取 key"""
        from llm_chat.backends.openai import OpenAIBackend

        monkeypatch.setenv("MY_OPENAI_KEY", "sk-test123")
        backend = OpenAIBackend(api_key_env="MY_OPENAI_KEY")
        assert backend._resolve_key() == "sk-test123"

    def test_endpoint_selection(self):
        """endpoint 参数正确存储"""
        from llm_chat.backends.openai import OpenAIBackend

        b1 = OpenAIBackend(endpoint="chat")
        b2 = OpenAIBackend(endpoint="responses")
        assert b1.endpoint == "chat"
        assert b2.endpoint == "responses"

    def test_base_url_strip_trailing_slash(self):
        """base_url 去尾部斜杠"""
        from llm_chat.backends.openai import OpenAIBackend

        backend = OpenAIBackend(base_url="http://localhost/v1/")
        assert backend.base_url == "http://localhost/v1"


class TestAskOnce:
    """ask_once 单次调用测试"""

    def test_ask_once_basic(self):
        """基本调用（mock 后端）"""
        from unittest.mock import patch

        from llm_chat import ask_once

        mock_backend = MagicMock()
        mock_backend.chat.return_value = "这是回答"

        with patch("llm_chat.oneshot.get_backend", return_value=mock_backend):
            result = ask_once("你好", backend="bedrock", model="test")

        assert result == "这是回答"
        mock_backend.chat.assert_called_once()
        call_args = mock_backend.chat.call_args
        assert call_args[0][0] == [{"role": "user", "content": "你好"}]

    def test_ask_once_with_system_prompt(self):
        """带 system_prompt"""
        from unittest.mock import patch

        from llm_chat import ask_once

        mock_backend = MagicMock()
        mock_backend.chat.return_value = "ok"

        with patch("llm_chat.oneshot.get_backend", return_value=mock_backend):
            ask_once("问题", system_prompt="你是分类器", backend="openai", model="x")

        call_args = mock_backend.chat.call_args
        assert call_args[1]["system_prompt"] == "你是分类器"
