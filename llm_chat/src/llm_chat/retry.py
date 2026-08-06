"""
网络请求重试工具

对 429（限流）和 5xx（服务端错误）自动重试，指数退避。
401/403 等认证错误不重试，直接抛出。
"""

import logging
import time

log = logging.getLogger("llm_chat.retry")


def retry_http(fn, max_retries: int = 3, backoff: float = 2.0):
    """
    对 HTTP 请求函数做重试封装

    Args:
        fn: 无参数可调用对象，返回响应或抛出 RuntimeError
        max_retries: 最大重试次数（不含首次）
        backoff: 退避基数（第 n 次重试等 backoff^n 秒）

    Returns:
        fn() 的返回值

    Raises:
        最后一次重试仍失败时抛出异常
    """
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except RuntimeError as e:
            last_err = e
            msg = str(e)
            # 429 或 5xx 才重试
            should_retry = (
                "429" in msg
                or "500" in msg
                or "502" in msg
                or "503" in msg
                or "504" in msg
                or "网络请求失败" in msg
            )
            if not should_retry or attempt == max_retries:
                raise
            wait = backoff**attempt
            log.warning(
                f"请求失败 (attempt {attempt + 1}/{max_retries + 1}), {wait:.1f}s 后重试: {msg[:100]}"
            )
            time.sleep(wait)
    raise last_err
