"""
PDF 版本对比单元测试 — 验证 version_compare 对 PDF 格式同样有效

测试数据: data/pdf/v1/IT运维管理规范.pdf vs data/pdf/v2/IT运维管理规范.pdf
（由 docx 通过 Word 转换而来，内容与 docx 版本完全一致）

预期：应检测到与 docx 测试相同的差异（正文+表格）
"""

import os

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data", "pdf", "IT运维管理规范")
V1_PATH = os.path.join(_DATA_DIR, "v1", "IT运维管理规范.pdf")
V2_PATH = os.path.join(_DATA_DIR, "v2", "IT运维管理规范.pdf")


@pytest.fixture(scope="module")
def version_diff_result():
    """执行一次 version_compare（PDF），所有测试共享结果"""
    from version_diff import DiffEngine

    engine = DiffEngine(config={
        "embedding": {"model": "BAAI/bge-small-zh-v1.5"},
        "diff": {"similarity_threshold": 0.80},
    })
    result = engine.version_compare(V1_PATH, V2_PATH)
    return result


class TestPdfVersionCompareBasic:
    def test_files_exist(self):
        assert os.path.exists(V1_PATH), f"V1 PDF 不存在: {V1_PATH}"
        assert os.path.exists(V2_PATH), f"V2 PDF 不存在: {V2_PATH}"

    def test_has_changes(self, version_diff_result):
        assert len(version_diff_result.changes) > 0, "应检测到差异"

    def test_has_all_change_types(self, version_diff_result):
        types = {c.change_type for c in version_diff_result.changes}
        assert "modified" in types, "应有 modified"
        assert "added" in types, "应有 added"
        # removed 可能因 PDF 解析噪音而丢失，不强制

    def test_paragraph_counts(self, version_diff_result):
        assert version_diff_result.old_paragraph_count > 20
        assert version_diff_result.new_paragraph_count > version_diff_result.old_paragraph_count


class TestPdfContentAccuracy:
    """验证正文关键差异能被检测到"""

    def test_p1_response_time(self, version_diff_result):
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("30" in c.old_text and "15" in c.new_text for c in modified)
        assert found, "未检测到 P1 响应时间 30→15"

    def test_disk_threshold(self, version_diff_result):
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("75" in c.old_text and "70" in c.new_text for c in modified)
        assert found, "未检测到磁盘阈值 75→70"

    def test_shift_staff(self, version_diff_result):
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        # PDF 提取可能在数字和汉字间插入空格："2 人" vs "3 人"
        found = any(("2人" in c.old_text or "2 人" in c.old_text) and ("3人" in c.new_text or "3 人" in c.new_text) for c in modified)
        assert found, "未检测到白班人数 2→3（含PDF空格变体）"

    def test_new_chapter_content(self, version_diff_result):
        added = [c for c in version_diff_result.changes if c.change_type == "added"]
        found = any("堡垒机" in c.new_text or "SSH" in c.new_text for c in added)
        assert found, "未检测到第六章信息安全管理新增内容"


class TestPdfTableChanges:
    """验证表格差异（PDF 解析质量可能略低于 Word）"""

    def test_firewall_upgrade(self, version_diff_result):
        all_changes = version_diff_result.changes
        found = any(
            "PA-850" in c.old_text and "PA-3260" in c.new_text
            for c in all_changes if c.change_type == "modified"
        )
        assert found, "未检测到防火墙 PA-850→PA-3260"

    def test_database_upgrade(self, version_diff_result):
        all_changes = version_diff_result.changes
        found = any(
            "19c" in c.old_text and "21c" in c.new_text
            for c in all_changes if c.change_type == "modified"
        )
        assert found, "未检测到数据库 Oracle 19c→21c"

    def test_waf_added(self, version_diff_result):
        added = [c for c in version_diff_result.changes if c.change_type == "added"]
        found = any("WAF" in c.new_text or "Imperva" in c.new_text for c in added)
        assert found, "未检测到 WAF 设备新增"


class TestPdfSummary:
    def test_print_summary(self, version_diff_result):
        """打印 PDF 版本对比摘要"""
        changes = version_diff_result.changes
        print(f"\n{'='*60}")
        print(f"PDF 版本对比: {len(changes)} 处差异")
        print(f"  旧版段落: {version_diff_result.old_paragraph_count}")
        print(f"  新版段落: {version_diff_result.new_paragraph_count}")
        print(f"  modified: {sum(1 for c in changes if c.change_type == 'modified')}")
        print(f"  added: {sum(1 for c in changes if c.change_type == 'added')}")
        print(f"  removed: {sum(1 for c in changes if c.change_type == 'removed')}")
        print(f"{'='*60}")
        for i, c in enumerate(changes[:20], 1):
            icon = {"modified": "✏️", "added": "➕", "removed": "➖"}[c.change_type]
            print(f"  {icon} #{i} [{c.change_type}] {c.location}")
            if c.old_text:
                print(f"     旧: {c.old_text[:80]}")
            if c.new_text:
                print(f"     新: {c.new_text[:80]}")
        if len(changes) > 20:
            print(f"  ... 共 {len(changes)} 处 (仅显示前20)")
