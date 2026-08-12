"""
LLM HTTP 后端公共基类

统一两个 LLM HTTP 后端（OpenAI 兼容 / Bedrock）的：
  - API Key 分级解析（显式传入 > 环境变量）
  - HTTP 错误处理（401 / 429 / 5xx / URLError 四分支）
  - 公共字段初始化（_init_common，消除子类 __init__ 样板）

异常约定：
  - HTTP 错误抛 RuntimeError 时挂 ``status_code`` 属性（真实 HTTP 状态码）
  - 网络错误（URLError）抛 RuntimeError 时挂 ``is_network_error = True``
  这样 retry.py 可直接读属性判断是否重试，无需正则解析 message 字符串。

子类只需实现：
  - _parse_response(result) -> str：把 JSON 响应解析为模型输出文本
  - chat()：构造请求并调用 self._send(req, self._parse_response)
"""

import json
import os
import urllib.error
import urllib.request
from contextlib import suppress


class BaseHTTPBackend:
    """LLM HTTP 后端基类"""

    # 子类应在 __init__ 中赋值以下属性
    api_key_env: str = ""
    api_key: str = ""
    timeout: int = 0
    max_retries: int = 0
    retry_backoff: float = 0.0

    def _init_common(
        self,
        defaults: dict,
        api_key_env: str,
        api_key: str,
        max_tokens: int,
        timeout: int,
        max_retries: int,
        retry_backoff: float,
    ):
        """
        从 defaults 字典初始化公共字段。

        两个后端共享的字段名相同，仅默认值来源不同（BEDROCK_DEFAULTS /
        OPENAI_DEFAULTS），集中在此消除子类 __init__ 的样板重复。
        """
        self.api_key_env = api_key_env or defaults.get("api_key_env", "")
        self.api_key = api_key
        self.max_tokens = max_tokens or defaults.get("max_tokens", 0)
        self.timeout = timeout or defaults.get("timeout", 0)
        self.max_retries = max_retries or defaults.get("max_retries", 3)
        self.retry_backoff = retry_backoff or defaults.get("retry_backoff", 2.0)

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

        Raises:
            RuntimeError: HTTP 错误（挂 status_code）/ 网络错误（挂 is_network_error）
        """
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            with suppress(Exception):
                body = e.read().decode("utf-8")[:300]
            if e.code == 401:
                msg = f"API Key 无效或已过期 (401): {body}"
            elif e.code == 429:
                msg = f"请求频率超限 (429)，请稍后重试: {body}"
            elif e.code >= 500:
                msg = f"服务端错误 ({e.code}): {body}"
            else:
                msg = f"HTTP {e.code}: {body}"
            err = RuntimeError(msg)
            err.status_code = e.code
            raise err from e
        except urllib.error.URLError as e:
            err = RuntimeError(f"网络请求失败: {e.reason}")
            err.is_network_error = True
            raise err from e

        return parse_fn(result)
