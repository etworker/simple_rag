"""
Bedrock Converse API 后端
"""

import json
import urllib.request

from llm_chat.backends.base import BaseHTTPBackend
from llm_chat.defaults import BEDROCK_DEFAULTS
from llm_chat.retry import retry_http


class BedrockBackend(BaseHTTPBackend):
    """
    AWS Bedrock Converse API 后端

    Args:
        model: 模型 ID（如 "zai.glm-4.7-flash"）
        region: AWS 区域
        api_key_env: 环境变量名（存放 Bearer Token）
        api_key: 直接传入的 API Key（优先级低于环境变量）
        max_tokens: 最大生成 token 数
        timeout: 请求超时（秒）
        max_retries: 最大重试次数（429/5xx 自动重试）
        retry_backoff: 重试退避基数（指数退避）
    """

    def __init__(
        self,
        model: str = "",
        region: str = "",
        api_key_env: str = "",
        api_key: str = "",
        max_tokens: int = 0,
        timeout: int = 0,
        max_retries: int = 0,
        retry_backoff: float = 0,
        **kwargs,
    ):
        self.model = model or BEDROCK_DEFAULTS.get("model", "zai.glm-4.7-flash")
        self.region = region or BEDROCK_DEFAULTS["region"]
        self._init_common(BEDROCK_DEFAULTS, api_key_env, api_key, max_tokens, timeout, max_retries, retry_backoff)

    def chat(self, messages: list[dict], system_prompt: str = "") -> str:
        """
        调用 Bedrock Converse API

        Args:
            messages: [{role: "user"|"assistant", content: str}, ...]
            system_prompt: 系统提示词

        Returns:
            LLM 回复文本
        """
        key = self._resolve_key()
        if not key:
            raise RuntimeError("未配置 API Key（检查环境变量或 api_key 参数）")

        # 构建 Bedrock Converse 格式的 messages
        api_messages = []
        for msg in messages:
            api_messages.append({"role": msg["role"], "content": [{"text": msg["content"]}]})

        # Bedrock Converse API 支持顶层 system 字段
        payload = {
            "messages": api_messages,
            "inferenceConfig": {"maxTokens": self.max_tokens},
        }
        if system_prompt:
            payload["system"] = [{"text": system_prompt}]

        url = f"https://bedrock-runtime.{self.region}.amazonaws.com/model/{self.model}/converse"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)

        return self._do_request(req)

    def _do_request(self, req) -> str:
        """发送 HTTP 请求并解析响应（含重试）"""
        return retry_http(
            lambda: self._send(req, self._parse_response),
            max_retries=self.max_retries,
            backoff=self.retry_backoff,
        )

    def _parse_response(self, result) -> str:
        """解析 Bedrock Converse 响应"""
        output = result.get("output", {})
        if output:
            message = output.get("message", {})
            for block in message.get("content", []):
                if "text" in block:
                    return block["text"]

        raise RuntimeError("LLM 返回为空")
