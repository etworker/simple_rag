"""
LLM Profile 共享配置机制单测。

验证：llm_profiles + llm_routing 解析、回退、Config.llm 从 profile 解析。
"""

import pytest

from llm_chat import resolve_llm_config, resolve_llm_profile

PROFILES = {
    "glm": {"provider": "openai", "model": "GLM", "base_url": "http://x/v1", "api_key": "dummy"},
    "bedrock": {"provider": "bedrock", "model": "zai.glm-4.7-flash", "region": "us-east-1"},
}
ROUTING = {"version_compare": "glm", "qa": "bedrock"}


class TestResolveLlmProfile:
    def test_by_use_case(self):
        cfg = resolve_llm_profile(PROFILES, ROUTING, "version_compare")
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "GLM"

    def test_fallback_to_first(self):
        # 未知用途 → 回退到第一个 profile
        cfg = resolve_llm_profile(PROFILES, ROUTING, "unknown_use")
        assert cfg["provider"] == "openai"

    def test_no_profile_raises(self):
        with pytest.raises(KeyError):
            resolve_llm_profile(None, None, "x")


class TestResolveLlmConfig:
    def test_direct_config_passthrough(self):
        cfg = {"provider": "openai", "model": "m"}
        assert resolve_llm_config(cfg) == {"provider": "openai", "model": "m"}

    def test_empty(self):
        assert resolve_llm_config(None) == {}
        assert resolve_llm_config({}) == {}

    def test_profile_reference(self):
        llm = {"profile": "glm", "llm_profiles": PROFILES, "routing": ROUTING}
        cfg = resolve_llm_config(llm)
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "GLM"

    def test_profile_via_routing(self):
        # profile 名不在 profiles，则按 routing[use_case] 回退解析
        llm = {"profile": "qa", "llm_profiles": PROFILES, "routing": ROUTING}
        cfg = resolve_llm_config(llm)
        assert cfg["provider"] == "bedrock"
