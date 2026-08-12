"""
llm_chat 后端修复验证测试

重点测试:
  - BedrockBackend 使用标准 system 字段（不再伪装为对话对）
  - OpenAIBackend HTTP 错误处理（401/429/500/网络错误）
  - BedrockBackend HTTP 错误处理
"""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from llm_chat.backends.bedrock import BedrockBackend
from llm_chat.backends.openai import OpenAIBackend


class TestBedrockSystemField:
    """测试 Bedrock 后端使用标准 system 字段"""

    def test_system_prompt_in_system_field(self, monkeypatch):
        """system_prompt 应出现在 payload 的 system 字段，而非伪装为对话"""
        monkeypatch.setenv("TEST_KEY", "secret")
        backend = BedrockBackend(api_key_env="TEST_KEY", model="test-model")

        captured_payload = {}

        class FakeResp:
            def read(self):
                return json.dumps({"output": {"message": {"content": [{"text": "OK"}]}}}).encode()

        class FakeUlopen:
            def __init__(self, req, timeout=None):
                captured_payload["data"] = json.loads(req.data.decode())
                captured_payload["headers"] = req.headers

            def __enter__(self):
                return FakeResp()

            def __exit__(self, *a):
                pass

        with patch("urllib.request.urlopen", FakeUlopen):
            result = backend.chat([{"role": "user", "content": "你好"}], system_prompt="你是助手")

        assert result == "OK"
        # system_prompt 应在 system 字段
        assert "system" in captured_payload["data"]
        assert captured_payload["data"]["system"] == [{"text": "你是助手"}]
        # messages 中不应包含伪装的 system 对话
        messages = captured_payload["data"]["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        # 不应出现 "明白" 这类假回复
        for msg in messages:
            for block in msg["content"]:
                assert "明白" not in block.get("text", "")

    def test_no_system_prompt_omits_field(self, monkeypatch):
        """无 system_prompt 时不应有 system 字段"""
        monkeypatch.setenv("TEST_KEY", "secret")
        backend = BedrockBackend(api_key_env="TEST_KEY", model="test-model")

        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps({"output": {"message": {"content": [{"text": "OK"}]}}}).encode()

        class FakeUlopen:
            def __init__(self, req, timeout=None):
                captured["data"] = json.loads(req.data.decode())

            def __enter__(self):
                return FakeResp()

            def __exit__(self, *a):
                pass

        with patch("urllib.request.urlopen", FakeUlopen):
            backend.chat([{"role": "user", "content": "hi"}])

        assert "system" not in captured["data"]


class TestOpenAIErrorHandling:
    """测试 OpenAI 后端的 HTTP 错误处理"""

    def test_http_401_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-test")
        backend = OpenAIBackend(api_key_env="TEST_KEY", model="gpt-4o")

        error = urllib.error.HTTPError(
            url="http://test",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )

        with (
            patch("urllib.request.urlopen", side_effect=error),
            pytest.raises(RuntimeError, match="API Key 无效或已过期"),
        ):
            backend.chat([{"role": "user", "content": "hi"}])

    def test_http_429_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-test")
        backend = OpenAIBackend(api_key_env="TEST_KEY", model="gpt-4o")

        error = urllib.error.HTTPError(
            url="http://test",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=MagicMock(),
        )
        error.read = MagicMock(return_value=b'{"error": "rate limit"}')

        with patch("urllib.request.urlopen", side_effect=error), pytest.raises(RuntimeError, match="请求频率超限"):
            backend.chat([{"role": "user", "content": "hi"}])

    def test_http_500_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-test")
        backend = OpenAIBackend(api_key_env="TEST_KEY", model="gpt-4o")

        error = urllib.error.HTTPError(
            url="http://test",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=MagicMock(),
        )
        error.read = MagicMock(return_value=b'{"error": "server"}')

        with patch("urllib.request.urlopen", side_effect=error), pytest.raises(RuntimeError, match="服务端错误"):
            backend.chat([{"role": "user", "content": "hi"}])

    def test_url_error_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-test")
        backend = OpenAIBackend(api_key_env="TEST_KEY", model="gpt-4o")

        error = urllib.error.URLError(OSError("Connection refused"))

        with patch("urllib.request.urlopen", side_effect=error), pytest.raises(RuntimeError, match="网络请求失败"):
            backend.chat([{"role": "user", "content": "hi"}])


class TestBedrockErrorHandling:
    """测试 Bedrock 后端的 HTTP 错误处理"""

    def test_http_401_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "secret")
        backend = BedrockBackend(api_key_env="TEST_KEY", model="test-model")

        error = urllib.error.HTTPError(
            url="http://test",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=MagicMock(),
        )
        error.read = MagicMock(return_value=b"{}")

        with (
            patch("urllib.request.urlopen", side_effect=error),
            pytest.raises(RuntimeError, match="API Key 无效或已过期"),
        ):
            backend.chat([{"role": "user", "content": "hi"}])

    def test_http_429_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "secret")
        backend = BedrockBackend(api_key_env="TEST_KEY", model="test-model")

        error = urllib.error.HTTPError(
            url="http://test",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=MagicMock(),
        )
        error.read = MagicMock(return_value=b"{}")

        with patch("urllib.request.urlopen", side_effect=error), pytest.raises(RuntimeError, match="请求频率超限"):
            backend.chat([{"role": "user", "content": "hi"}])

    def test_url_error_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "secret")
        backend = BedrockBackend(api_key_env="TEST_KEY", model="test-model")

        error = urllib.error.URLError(OSError("timeout"))

        with patch("urllib.request.urlopen", side_effect=error), pytest.raises(RuntimeError, match="网络请求失败"):
            backend.chat([{"role": "user", "content": "hi"}])


class TestOpenAIResponsesInstructions:
    """测试 Responses API 端点把 system_prompt 放入 instructions 字段"""

    def test_system_prompt_goes_to_instructions(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-test")
        backend = OpenAIBackend(api_key_env="TEST_KEY", model="gpt-4o", endpoint="responses")

        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps(
                    {
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "OK"}],
                            }
                        ]
                    }
                ).encode()

        class FakeUlopen:
            def __init__(self, req, timeout=None):
                captured["data"] = json.loads(req.data.decode())

            def __enter__(self):
                return FakeResp()

            def __exit__(self, *a):
                pass

        with patch("urllib.request.urlopen", FakeUlopen):
            result = backend.chat(
                [{"role": "user", "content": "你好"}],
                system_prompt="你是助手",
            )

        assert result == "OK"
        # system_prompt 应进入 instructions（Responses API 标准字段）
        assert captured["data"].get("instructions") == "你是助手"
        # input 中不应出现 system 角色（否则该端点会拒绝）
        for item in captured["data"]["input"]:
            assert item["role"] != "system"

    def test_no_system_prompt_omits_instructions(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-test")
        backend = OpenAIBackend(api_key_env="TEST_KEY", model="gpt-4o", endpoint="responses")

        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps(
                    {
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "OK"}],
                            }
                        ]
                    }
                ).encode()

        class FakeUlopen:
            def __init__(self, req, timeout=None):
                captured["data"] = json.loads(req.data.decode())

            def __enter__(self):
                return FakeResp()

            def __exit__(self, *a):
                pass

        with patch("urllib.request.urlopen", FakeUlopen):
            backend.chat([{"role": "user", "content": "hi"}])

        assert "instructions" not in captured["data"]
