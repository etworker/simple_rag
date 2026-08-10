"""
LLM HTTP 后端公共基类

统一两个 LLM HTTP 后端（OpenAI 兼容 / Bedrock）的：
  - API Key 分级解析（显式传入 > 环境变量）
  - HTTP 错误处理（401 / 429 / 5xx / URLError 四分支）

子类只需实现：
  - _parse_response(result) -> str：把 JSON 响应解析为模型输出文本
  - chat()：构造请求并调用 self._send(req, self._parse_response)
"""
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("llm_chat.backends.base")


class BaseHTTPBackend:
    """LLM HTTP 后端基类"""

    # 子类应在 __init__ 中赋值以下属性
    api_key_env: str = ""
    api_key: str = ""
    timeout: int = 0
    max_retries: int = 0
    retry_backoff: float = 0.0

    def _resolve_key(self) -> str:
        """
        分级获取 API Key：显式传入 > 环境变量 > 空

        显式 api_key 优先，避免 self_hosted_glm 这类自带 key 的 profile
        被误用其他后端的 env key（如用 Bedrock token 打自建端点）。
        """
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env, "")

    def _send(self, req, parse_fn) -> str:
        """
        执行单个 HTTP 请求（不含重试），统一处理错误并解析响应。

        Args:
            req: 已构造好的 urllib Request（headers 已含 Authorization）
            parse_fn: (result: dict) -> str，由各后端实现响应解析

        Returns:
            模型输出文本
        """
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

        return parse_fn(result)
