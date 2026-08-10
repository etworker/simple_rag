"""
版本对比 LLM 实质性变更过滤测试

验证：version_compare + _filter_substantive_changes 能正确过滤噪音，
只保留客观事实变更，过滤掉编号重排/等义改写/元数据差异。

需要 LLM 在线（使用 config.json 中的 self_hosted_glm 配置）
"""

import json
import os

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data", "docx")
V1_PATH = os.path.join(_DATA_DIR, "v1", "IT运维管理规范.docx")
V2_PATH = os.path.join(_DATA_DIR, "v2", "IT运维管理规范.docx")

# 从 config.json 读取 LLM 配置
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "rag_demo", "config.json")


def _get_llm_config():
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    profiles = config.get("llm_profiles", {})
    routing = config.get("llm_routing", {})
    profile_name = routing.get("pre_review", "")
    return profiles.get(profile_name, {})


@pytest.fixture(scope="module")
def filtered_result():
    """带 LLM 过滤的版本对比"""
    from version_diff import DiffEngine

    llm_config = _get_llm_config()
    if not llm_config.get("model"):
        pytest.skip("未配置 LLM，跳过需要在线 LLM 的测试")

    engine = DiffEngine(config={
        "embedding": {"model": "BAAI/bge-small-zh-v1.5"},
        "diff": {"similarity_threshold": 0.80, "batch_size": 10},
        "llm": llm_config,
    })
    result = engine.version_compare(V1_PATH, V2_PATH)
    return result


class TestFilterReducesNoise:
    """验证过滤确实减少了差异数量"""

    def test_total_changes_reduced(self, filtered_result):
        """过滤后差异数应少于原始 35 处"""
        # 原始无过滤时约 35 处（含编号变化、版本号等噪音）
        # 过滤后应保留约 15-25 处实质性变更
        total = len(filtered_result.changes)
        print(f"\n过滤后差异数: {total}")
        assert total < 35, f"过滤后仍有 {total} 处，未有效减少"
        assert total >= 10, f"过滤过于激进，只剩 {total} 处"

    def test_no_version_metadata(self, filtered_result):
        """版本号/日期变化应被过滤"""
        for c in filtered_result.changes:
            # V1.0→V2.0、2025-06-01→2026-03-01 这类不应出现
            if c.change_type == "modified":
                is_metadata = (
                    ("V1.0" in c.old_text and "V2.0" in c.new_text) or
                    ("2025-06-01" in c.old_text and "2026-03-01" in c.new_text)
                )
                assert not is_metadata, f"版本元数据未被过滤: {c.old_text[:50]} → {c.new_text[:50]}"


class TestFilterKeepsSubstantive:
    """验证实质性变更被正确保留"""

    def test_keeps_numeric_changes(self, filtered_result):
        """数值变更应保留（P1时间、磁盘阈值等）"""
        modified = [c for c in filtered_result.changes if c.change_type == "modified"]
        # 至少应保留：P1时间、磁盘阈值、备份频率、白班人数、培训学时
        numeric_found = sum(1 for c in modified if any(
            kw in c.old_text or kw in c.new_text
            for kw in ["分钟", "小时", "%", "学时", "人在岗"]
        ))
        assert numeric_found >= 3, f"数值变更保留太少: {numeric_found}"

    def test_keeps_device_changes(self, filtered_result):
        """设备型号变更应保留"""
        modified = [c for c in filtered_result.changes if c.change_type == "modified"]
        device_found = any(
            "PA-850" in c.old_text or "i2600" in c.old_text or "19c" in c.old_text
            for c in modified
        )
        assert device_found, "设备型号变更被错误过滤"

    def test_keeps_added_content(self, filtered_result):
        """新增内容应全部保留"""
        added = [c for c in filtered_result.changes if c.change_type == "added"]
        assert len(added) >= 3, f"新增内容保留太少: {len(added)}"

    def test_keeps_removed_content(self, filtered_result):
        """删除内容应保留"""
        removed = [c for c in filtered_result.changes if c.change_type == "removed"]
        assert len(removed) >= 1, f"删除内容被过滤: {len(removed)}"


class TestFilterSummary:
    """LLM 生成的摘要验证"""

    def test_has_summaries(self, filtered_result):
        """保留的 modified 项应有 LLM 生成的摘要"""
        modified = [c for c in filtered_result.changes if c.change_type == "modified"]
        with_summary = [c for c in modified if c.summary]
        print(f"\n有摘要的 modified: {len(with_summary)}/{len(modified)}")
        for c in with_summary[:5]:
            print(f"  - {c.summary}")
        # 至少一半有摘要
        assert len(with_summary) >= len(modified) * 0.5, "摘要生成不足"

    def test_print_final_result(self, filtered_result):
        """打印最终过滤结果"""
        changes = filtered_result.changes
        print(f"\n{'='*60}")
        print(f"版本对比最终结果（LLM 过滤后）: {len(changes)} 处实质性变更")
        print(f"  modified: {sum(1 for c in changes if c.change_type == 'modified')}")
        print(f"  added: {sum(1 for c in changes if c.change_type == 'added')}")
        print(f"  removed: {sum(1 for c in changes if c.change_type == 'removed')}")
        print(f"{'='*60}")
        for i, c in enumerate(changes, 1):
            icon = {"modified": "✏️", "added": "➕", "removed": "➖"}[c.change_type]
            summary = f" — {c.summary}" if c.summary else ""
            print(f"  {icon} #{i} [{c.change_type}] {c.location}{summary}")
            if c.old_text:
                print(f"     旧: {c.old_text[:100]}")
            if c.new_text:
                print(f"     新: {c.new_text[:100]}")
        print(f"{'='*60}")
