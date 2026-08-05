"""
OpenAI 兼容 API 后端

支持：
  - OpenAI 官方 API
  - AWS Bedrock Mantle Proxy (bedrock-mantle.us-east-1.api.aws/openai/v1)
  - 任何 OpenAI 兼容端点（vLLM、Ollama、LM Studio 等）
"""
import os
import json
import logging
import urllib.request
import urllib.error
from typing import List

from llm_chat.defaults import OPENAI_DEFAULTS

log = logging.getLogger("llm_chat.openai")


class OpenAIBackend:
    """
    OpenAI 兼容 API 后端

    Args:
        model: 模型名称（如 "gpt-4o", "gpt-5.6-luna"）
        base_url: API 基础 URL（如 "https://api.openai.com/v1"）
        api_key_env: 环境变量名
        api_key: 直接传入的 API Key
        max_tokens: 最大生成 token 数
        timeout: 请求超时（秒）
        endpoint: 使用哪个端点 ("chat" | "responses")
    """

    def __init__(
        self,
        model: str = "",
        base_url: str = "",
        api_key_env: str = "",
        api_key: str = "",
        max_tokens: int = 0,
        timeout: int = 0,
        endpoint: str = "",
        **kwargs,
    ):
        self.model = model or OPENAI_DEFAULTS.get("model", "gpt-4o")
        self.base_url = (base_url or OPENAI_DEFAULTS["base_url"]).rstrip("/")
        self.api_key_env = api_key_env or OPENAI_DEFAULTS["api_key_env"]
        self.api_key = api_key
        self.max_tokens = max_tokens or OPENAI_DEFAULTS["max_tokens"]
        self.timeout = timeout or OPENAI_DEFAULTS["timeout"]
        self.endpoint = endpoint or OPENAI_DEFAULTS["endpoint"]

    def chat(self, messages: List[dict], system_prompt: str = "") -> str:
        """
        调用 OpenAI 兼容 API

        Args:
            messages: [{role: "user"|"assistant", content: str}, ...]
            system_prompt: 系统提示词

        Returns:
            LLM 回复文本
        """
        key = self._resolve_key()
        if not key:
            raise RuntimeError("未配置 API Key（检查环境变量或 api_key 参数）")

        # 构建 OpenAI messages 格式
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        if self.endpoint == "responses":
            return self._call_responses(api_messages, key)
        else:
            return self._call_chat_completions(api_messages, key)

    def _call_chat_completions(self, messages: list, key: str) -> str:
        """标准 /chat/completions 端点"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        return self._do_request(url, payload, key)

    def _call_responses(self, messages: list, key: str) -> str:
        """OpenAI Responses API /responses 端点（Bedrock Mantle proxy 用这个）"""
        url = f"{self.base_url}/responses"
        # Responses API 用 input 而非 messages
        input_items = []
        for msg in messages:
            input_items.append({"role": msg["role"], "content": msg["content"]})
        payload = {
            "model": self.model,
            "input": input_items,
            "max_output_tokens": max(self.max_tokens, 16),
        }
        return self._do_request(url, payload, key)

    def _do_request(self, url: str, payload: dict, key: str) -> str:
        """发送 HTTP 请求并解析响应"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                pass
            if e.code == 401:
                raise RuntimeError(f"API Key 无效或已过期 (401): {body}") from e
            elif e.code == 429:
                raise RuntimeError(f"请求频率超限 (429)，请稍后重试: {body}") from e
            elif e.code >= 500:
                raise RuntimeError(f"服务端错误 ({e.code}): {body}") from e
            else:
                raise RuntimeError(f"HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"网络请求失败: {e.reason}") from e

        # 解析不同格式的响应
        # Chat Completions 格式
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        # Responses API 格式
        if "output" in result:
            for item in result["output"]:
                if item.get("type") == "message":
                    for block in item.get("content", []):
                        if block.get("type") == "output_text":
                            return block["text"]
        # 兜底
        raise RuntimeError(f"无法解析 LLM 响应: {json.dumps(result)[:200]}")

    def _resolve_key(self) -> str:
        """分级获取 API Key"""
        val = os.environ.get(self.api_key_env, "")
        if val:
            return val
        return self.api_key
