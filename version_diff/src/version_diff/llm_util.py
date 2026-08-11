"""
LLM 调用 + JSON 数组解析的公共工具

供 judge / engine 等模块复用，避免各处在 ask_once 外层重复书写
「正则提取 [..] → json.loads → 逐条按 index 映射」的样板代码。
"""

import json
import re
import time

from loguru import logger as log


def call_llm_json(
    prompt: str,
    llm_config: dict,
    max_retries: int = 2,
    retry_backoff: float = 1.0,
) -> list | None:
    """
    调用 LLM 并解析其返回的 JSON 数组。

    对「调用异常」和「拿到了返回内容但 JSON 解析失败」两种情况做有限次数重试
    （指数退避）。模型偶尔会把 JSON 包裹在 markdown 或附带多余文本，重试后往往
    能拿到合法 JSON；重试耗尽仍失败才返回 None。

    Args:
        prompt: 完整 prompt 文本
        llm_config: LLM 配置字典（model / region / api_key_env / api_key / ...），
            直接透传给底层 ask_once
        max_retries: 失败后的最大重试次数（不含首次，默认 2 → 最多尝试 3 次）
        retry_backoff: 重试退避基数（秒），第 n 次重试等待 retry_backoff * n

    Returns:
        list[dict] — 解析出的 JSON 数组（可能为空列表）
        None — 重试后仍失败 / 无 JSON 输出（调用方应保守处理，如保留原文）
    """
    last_err: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            from llm_chat import ask_once

            backend = llm_config.get("provider", "bedrock")
            # 别名（如 bedrock_converse → bedrock）由 llm_chat backends 层统一处理
            response = ask_once(
                prompt,
                backend=backend,
                model=llm_config.get("model", ""),
                region=llm_config.get("region", ""),
                api_key_env=llm_config.get("api_key_env", ""),
                api_key=llm_config.get("api_key", ""),
                base_url=llm_config.get("base_url", ""),
                endpoint=llm_config.get("endpoint", "chat"),
                max_tokens=llm_config.get("max_tokens", 0),
                timeout=llm_config.get("timeout", 0),
                max_retries=llm_config.get("max_retries", 0),
                retry_backoff=llm_config.get("retry_backoff", 0),
            )
        except Exception as e:
            last_err = e
            log.warning(f"LLM 调用失败 (第 {attempt + 1} 次尝试): {e}")
            if attempt < max_retries:
                time.sleep(retry_backoff * (attempt + 1))
            continue

        if not response:
            # 空响应重试无意义，直接返回
            return None

        try:
            json_match = re.search(r"\[[\s\S]*\]", response)
            if json_match:
                return json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError) as e:
            last_err = e
            log.warning(f"JSON 解析失败 (第 {attempt + 1} 次尝试): {e}")
            if attempt < max_retries:
                time.sleep(retry_backoff * (attempt + 1))
            continue

    if last_err:
        log.warning(f"call_llm_json 重试 {max_retries} 次后仍失败: {last_err}")
    return None
