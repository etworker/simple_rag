"""
OpenAI 兼容 API 后端

支持：
  - OpenAI 官方 API
  - AWS Bedrock Mantle Proxy (bedrock-mantle.us-east-1.api.aws/openai/v1)
  - 任何 OpenAI 兼容端点（vLLM、Ollama、LM Studio 等）
"""

import json
import urllib.request

from llm_chat.backends.base import BaseHTTPBackend
from llm_chat.defaults import OPENAI_DEFAULTS
from llm_chat.retry import retry_http


class OpenAIBackend(BaseHTTPBackend):
    """
    OpenAI 兼容 API 后端

    Args:
        model: 模型名称（如 "gpt-4o", "gpt-5.6-luna"）
        base_url: API 基础 URL
        api_key_env: 环境变量名
        api_key: 直接传入的 API Key
        max_tokens: 最大生成 token 数
        timeout: 请求超时（秒）
        endpoint: 使用哪个端点 ("chat" | "responses")
        max_retries: 最大重试次数（429/5xx 自动重试）
        retry_backoff: 重试退避基数
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
        max_retries: int = 0,
        retry_backoff: float = 0,
        **kwargs,
    ):
        self.model = model or OPENAI_DEFAULTS.get("model", "gpt-4o")
        self.base_url = (base_url or OPENAI_DEFAULTS["base_url"]).rstrip("/")
        self.endpoint = endpoint or OPENAI_DEFAULTS["endpoint"]
        self._init_common(
            OPENAI_DEFAULTS, api_key_env, api_key, max_tokens, timeout, max_retries, retry_backoff
        )

    def chat(self, messages: list[dict], system_prompt: str = "") -> str:
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
        """OpenAI Responses API /responses 端点"""
        url = f"{self.base_url}/responses"
        instructions = None
        input_items = []
        for msg in messages:
            if msg["role"] == "system":
                # Responses API 用 instructions 字段承载系统提示，而非 input 里的 system 角色
                instructions = msg["content"]
            else:
                input_items.append({"role": msg["role"], "content": msg["content"]})
        payload = {
            "model": self.model,
            "input": input_items,
            "max_output_tokens": self.max_tokens,
        }
        if instructions:
            payload["instructions"] = instructions
        return self._do_request(url, payload, key)

    def _do_request(self, url: str, payload: dict, key: str) -> str:
        """发送 HTTP 请求并解析响应（含重试）"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)

        return retry_http(
            lambda: self._send(req, self._parse_response),
            max_retries=self.max_retries,
            backoff=self.retry_backoff,
        )

    def _parse_response(self, result) -> str:
        """解析 OpenAI 兼容响应（兼容 chat/completions 与 responses 两种格式）"""
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        if "output" in result:
            for item in result["output"]:
                if item.get("type") == "message":
                    for block in item.get("content", []):
                        if block.get("type") == "output_text":
                            return block["text"]
        raise RuntimeError(f"无法解析 LLM 响应: {json.dumps(result)[:200]}")
