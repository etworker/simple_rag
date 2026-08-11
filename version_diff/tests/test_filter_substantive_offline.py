"""
版本对比 LLM 过滤离线测试（不依赖在线 LLM）

锁定 _filter_substantive_changes 在 LLM 不可用时的行为：
- LLM 调用失败（call_llm_json 返回 None）必须保守地全部保留 modified 项
- 保留下来的项必须带 "content" 分类标签（修复前会落入未分类状态）
"""

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from version_diff.engine import DiffEngine  # noqa: E402
from version_diff.models import VersionChange  # noqa: E402


def _make_engine():
    return DiffEngine(config={})


def _substantive_changes():
    """构造几处实质不同的 modified 变更（不会被规则预过滤掉）"""
    return [
        VersionChange(
            change_type="modified",
            section="§2.2 备份策略",
            location="第3页",
            old_text="备份频率为每4小时一次",
            new_text="备份频率改为每2小时一次",
        ),
        VersionChange(
            change_type="modified",
            section="§3.1 告警阈值",
            location="第5页",
            old_text="磁盘使用率超过90%触发告警",
            new_text="磁盘使用率超过80%触发告警",
        ),
    ]


def test_llm_failure_keeps_all_and_tags_content(monkeypatch):
    """LLM 失败时必须全部保留且每项都带 content 标签"""
    import version_diff.llm_util as llm_util

    monkeypatch.setattr(llm_util, "call_llm_json", lambda *a, **k: None)

    engine = _make_engine()
    changes = _substantive_changes()
    keep, minor = engine._filter_substantive_changes(changes)

    assert len(keep) == 2, f"LLM 失败时未全部保留: keep={len(keep)}"
    assert len(minor) == 0, f"不应有 minor: {len(minor)}"
    for c in keep:
        assert c.category == "content", f"保留项未打 content 标签: {c.category!r}"


def test_classify_change_tags_dataclass_and_dict():
    """_classify_change 对 dataclass 实例和 plain dict 都应生效"""
    from version_diff.engine import _classify_change

    vc = VersionChange(change_type="modified", section="s", location="l",
                       old_text="a", new_text="b")
    _classify_change("metadata", vc)
    assert vc.category == "metadata"

    d = {"old_text": "a", "new_text": "b"}
    _classify_change("tracking_table", d)
    assert d["category"] == "tracking_table"
