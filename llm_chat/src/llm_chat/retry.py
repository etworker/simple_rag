"""
网络请求重试工具

对 429（限流）和 5xx（服务端错误）自动重试，指数退避。
401/403 等认证错误不重试，直接抛出。
"""

import re
import time

from loguru import logger as log

# 从异常信息中提取 HTTP 状态码，例如 "服务端错误 (503): ..." 或 "HTTP 503: ..."
_STATUS_RE = re.compile(r"\((\d{3})\)|\b(\d{3})\b")


def _extract_status_code(msg: str) -> int | None:
    """从 RuntimeError 信息中提取 HTTP 状态码（若有）"""
    m = _STATUS_RE.search(msg)
    if not m:
        return None
    code = int(m.group(1) or m.group(2))
    # 仅当数字落在合法 HTTP 状态码区间时才认作状态码，
    # 避免把正文里出现的 3 位数误判为状态码
    if 100 <= code <= 599:
        return code
    return None


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
            # 429（限流）或 5xx（服务端错误）才重试；
            # 从异常信息提取真实状态码，避免把正文里的 "500" 等数字误判为重试条件
            status = _extract_status_code(msg)
            should_retry = status == 429 or (status is not None and 500 <= status < 600)
            if not should_retry and "网络请求失败" in msg:
                should_retry = True
            if not should_retry or attempt == max_retries:
                raise
            wait = backoff**attempt
            log.warning(f"请求失败 (attempt {attempt + 1}/{max_retries + 1}), {wait:.1f}s 后重试: {msg[:100]}")
            time.sleep(wait)
    raise last_err
