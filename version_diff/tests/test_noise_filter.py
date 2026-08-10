"""
可配置元数据噪声过滤测试（纯单元测试，离线）

验证：_filter_substantive_changes 对 added/removed 类型也能按配置的
noise_filter.patterns 剥离「纯元数据」段落（修订日期戳/版本号/页码跟踪行等），
剥离后为空 → 归入 minor_changes；否则保留为 content。

不依赖 LLM：仅构造含 added/removed 的 changes（无 modified），不会触发在线判断。
"""

import re

import pytest

from version_diff import DiffEngine
from version_diff.engine import _strip_configured_noise
from version_diff.models import VersionChange

# 与 config.py 默认 noise_filter.patterns 保持一致（通用版本管理元数据）
_DEFAULT_PATTERNS = [
    r"修订日期\s*[：:]\s*\S+",
    r"(?:^\s*|\b)\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}\s*(?:$|\b)",
    r"(?:R\d+-\d{2,}|BK-J-\d+|版次\s*[：:]\s*\S+)",
    r"^(?:\d{1,4}\s+)?(?:R\d{2,3}|N|A|D)\s+\d{4}[-./]\d{1,2}[-./]\d{1,2}",
]


def _make_engine(enabled=True, patterns=None):
    return DiffEngine(config={
        "diff": {
            "similarity_threshold": 0.80,
            "top_k": 3,
            "batch_size": 5,
            "noise_filter": {
                "enabled": enabled,
                "patterns": patterns if patterns is not None else _DEFAULT_PATTERNS,
            },
        },
    })


def _change(change_type, old="", new=""):
    return VersionChange(
        change_type=change_type,
        section="测试",
        location="第1页",
        old_text=old,
        new_text=new,
    )


class TestStripConfiguredNoise:
    def test_strips_revision_date(self):
        assert _strip_configured_noise("修订日期：2026-05-08", _DEFAULT_PATTERNS) == ""

    def test_keeps_substantive_content(self):
        out = _strip_configured_noise(
            "核心网络设备、网络带宽的业务处理能力应满足业务高峰期的需求。", _DEFAULT_PATTERNS
        )
        assert "业务高峰期" in out

    def test_strips_date_with_surrounding_text(self):
        # 修订日期混在句中的情况：应只剥掉日期，保留正文
        out = _strip_configured_noise(
            "核心网络设备的业务能力 修订日期：2026-05-08 需求", _DEFAULT_PATTERNS
        )
        assert "业务能力" in out
        assert "2026" not in out

    def test_invalid_pattern_ignored(self):
        out = _strip_configured_noise("修订日期：2026-05-08", ["[", 123, "修订日期\\s*[：:]\\s*\\S+"])
        assert out == ""


class TestFilterAddedRemovedNoise:
    def test_added_pure_revision_date_moved_to_minor(self):
        engine = _make_engine()
        changes = [_change("added", new="修订日期：2026-05-08")]
        keep, minor = engine._filter_substantive_changes(changes)
        assert keep == []
        assert len(minor) == 1
        assert minor[0].category == "metadata"

    def test_removed_pure_revision_date_moved_to_minor(self):
        engine = _make_engine()
        changes = [_change("removed", old="修订日期：2021-06-15")]
        keep, minor = engine._filter_substantive_changes(changes)
        assert keep == []
        assert len(minor) == 1
        assert minor[0].category == "metadata"

    def test_removed_standalone_date_moved_to_minor(self):
        engine = _make_engine()
        changes = [_change("removed", old="2026-05-08")]
        keep, minor = engine._filter_substantive_changes(changes)
        assert keep == []
        assert len(minor) == 1

    def test_removed_version_stamp_moved_to_minor(self):
        engine = _make_engine()
        changes = [_change("removed", old="版次：R5-21")]
        keep, minor = engine._filter_substantive_changes(changes)
        assert keep == []
        assert len(minor) == 1

    def test_substantive_content_kept_even_with_revision_date(self):
        # 段落含实质内容 + 修订日期 → 保留为 content
        engine = _make_engine()
        changes = [
            _change(
                "added",
                new="核心网络设备、网络带宽的业务处理能力应满足业务高峰期的需求。 修订日期：2026-05-08",
            )
        ]
        keep, minor = engine._filter_substantive_changes(changes)
        assert len(keep) == 1
        assert keep[0].category == "content"
        assert minor == []

    def test_disabled_noise_filter_keeps_pure_metadata(self):
        # 关闭过滤后，纯修订日期也保留
        engine = _make_engine(enabled=False)
        changes = [_change("added", new="修订日期：2026-05-08")]
        keep, minor = engine._filter_substantive_changes(changes)
        assert len(keep) == 1
        assert minor == []

    def test_configured_custom_pattern(self):
        # 用户自定义 pattern：把「审批人」戳当作元数据
        engine = _make_engine(patterns=[r"审批人\s*[：:]\s*\S+"])
        changes = [_change("added", new="审批人：张三")]
        keep, minor = engine._filter_substantive_changes(changes)
        assert keep == []
        assert len(minor) == 1

    def test_mixed_preserves_content_keeps_order(self):
        # 混合：纯元数据进 minor，实质内容保留，顺序稳定
        engine = _make_engine()
        changes = [
            _change("added", new="修订日期：2026-05-08"),
            _change("added", new="备份频率为每 2 小时执行一次。"),
            _change("removed", old="2026-04-14"),
        ]
        keep, minor = engine._filter_substantive_changes(changes)
        assert len(keep) == 1
        assert keep[0].new_text == "备份频率为每 2 小时执行一次。"
        assert len(minor) == 2


class TestDiffPartialConfig:
    """from_dict 对 diff 段浅合并：部分配置下默认 noise_filter 仍生效"""

    def test_default_noise_filter_applies_with_partial_diff(self):
        # 调用方只传 diff 的部分键（无 noise_filter），默认 noise_filter 仍生效
        engine = DiffEngine(config={"diff": {"similarity_threshold": 0.80, "batch_size": 5}})
        assert engine.config.diff.get("noise_filter", {}).get("enabled") is True
        changes = [_change("removed", old="修订日期：2021-06-15")]
        keep, minor = engine._filter_substantive_changes(changes)
        assert keep == []
        assert len(minor) == 1

    def test_override_noise_filter_disabled(self):
        # 调用方显式传 noise_filter.enabled=False 覆盖默认
        engine = DiffEngine(
            config={"diff": {"noise_filter": {"enabled": False}}}
        )
        assert engine.config.diff["noise_filter"]["enabled"] is False
        changes = [_change("removed", old="修订日期：2021-06-15")]
        keep, minor = engine._filter_substantive_changes(changes)
        assert len(keep) == 1
        assert minor == []

    def test_diff_defaults_kept_when_partial(self):
        # 部分配置不丢默认的 similarity_threshold 等
        engine = DiffEngine(config={"diff": {"batch_size": 3}})
        assert engine.config.diff["similarity_threshold"] == 0.80
        assert engine.config.diff["top_k"] == 3
        assert engine.config.diff["batch_size"] == 3
