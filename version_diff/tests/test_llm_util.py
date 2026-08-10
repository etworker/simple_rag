"""
call_llm_json 的单元测试：聚焦「失败重试」语义。

通过 mock llm_chat.ask_once，验证：
  1. 连续失败 → 成功：应重试并返回结果（调用次数 = 成功前的失败数 + 1）
  2. 全部失败：应返回 None，且恰好尝试 max_retries + 1 次
  3. 返回内容但非合法 JSON：应触发重试，直到拿到合法 JSON
  4. 空响应：直接返回 None，不重试
"""

import unittest.mock as mock

from version_diff.llm_util import call_llm_json


def _cfg(**overrides):
    cfg = {"provider": "bedrock_converse", "model": "fake-model"}
    cfg.update(overrides)
    return cfg


def test_retry_then_success():
    """第 1 次异常、第 2 次返回非 JSON、第 3 次返回合法 JSON → 重试后成功。"""
    side = [Exception("boom"), "这是乱码没有JSON", '前缀 ["a", "b"] 后缀']
    with mock.patch("llm_chat.ask_once", side_effect=side) as m:
        result = call_llm_json("prompt", _cfg(), max_retries=2, retry_backoff=0.0)

    assert result == ["a", "b"]
    assert m.call_count == 3


def test_retry_exhausted_returns_none():
    """max_retries=2 时最多尝试 3 次，全部异常 → 返回 None。"""
    errs = [Exception("e1"), Exception("e2"), Exception("e3")]
    with mock.patch("llm_chat.ask_once", side_effect=errs) as m:
        result = call_llm_json("prompt", _cfg(), max_retries=2, retry_backoff=0.0)

    assert result is None
    assert m.call_count == 3


def test_json_parse_failure_triggers_retry():
    """拿到了返回内容但解析失败，应重试直至拿到合法 JSON。"""
    side = ["纯文本无JSON", "依旧 [ 残缺", '正确 ["ok"]']
    with mock.patch("llm_chat.ask_once", side_effect=side) as m:
        result = call_llm_json("prompt", _cfg(), max_retries=2, retry_backoff=0.0)

    assert result == ["ok"]
    assert m.call_count == 3


def test_empty_response_no_retry():
    """空响应直接返回 None，不进入重试循环。"""
    with mock.patch("llm_chat.ask_once", return_value="") as m:
        result = call_llm_json("prompt", _cfg(), max_retries=2, retry_backoff=0.0)

    assert result is None
    assert m.call_count == 1


def test_default_backoff_does_not_loop_forever():
    """默认参数下全部失败也应收敛（不无限重试）。"""
    with mock.patch("llm_chat.ask_once", side_effect=Exception("always")) as m:
        result = call_llm_json("prompt", _cfg())

    assert result is None
    # 默认 max_retries=2 → 最多 3 次
    assert m.call_count == 3
