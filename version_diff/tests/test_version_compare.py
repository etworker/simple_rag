"""
版本对比单元测试 — 验证 version_compare 对同名文档新旧版本的差异识别

测试数据: data/docx/v1/IT运维管理规范.docx vs data/docx/v2/IT运维管理规范.docx
预期差异 17 处:
  - 跨页大表格: 4处修改(防火墙/负载均衡/DNS/数据库) + 1处删除(VPN) + 2处新增(WAF/K8s)
  - 正文: P1响应/P2恢复/磁盘阈值/备份周期频率/变更流程/故障通知/白班人数/培训考核/新章节
"""

import os

import pytest

# 测试数据路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data", "docx")
V1_PATH = os.path.join(_DATA_DIR, "v1", "IT运维管理规范.docx")
V2_PATH = os.path.join(_DATA_DIR, "v2", "IT运维管理规范.docx")


@pytest.fixture(scope="module")
def version_diff_result():
    """执行一次 version_compare，所有测试共享结果"""
    from version_diff import DiffEngine

    engine = DiffEngine(
        config={
            "embedding": {"model": "BAAI/bge-small-zh-v1.5"},
            "diff": {"similarity_threshold": 0.80},
        }
    )
    result = engine.version_compare(V1_PATH, V2_PATH)
    return result


class TestVersionCompareBasic:
    """基本功能验证"""

    def test_files_exist(self):
        assert os.path.exists(V1_PATH), f"V1 文件不存在: {V1_PATH}"
        assert os.path.exists(V2_PATH), f"V2 文件不存在: {V2_PATH}"

    def test_result_type(self, version_diff_result):
        from version_diff.models import VersionDiffResult

        assert isinstance(version_diff_result, VersionDiffResult)

    def test_has_changes(self, version_diff_result):
        assert len(version_diff_result.changes) > 0, "应检测到差异"

    def test_paragraph_counts(self, version_diff_result):
        assert version_diff_result.old_paragraph_count > 0
        assert version_diff_result.new_paragraph_count > 0
        # V2 新增了第六章，段落数应更多
        assert version_diff_result.new_paragraph_count > version_diff_result.old_paragraph_count


class TestVersionCompareChangeTypes:
    """变更类型分布验证"""

    def test_has_modified(self, version_diff_result):
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        assert len(modified) >= 5, f"应有至少5处修改，实际{len(modified)}"

    def test_has_added(self, version_diff_result):
        added = [c for c in version_diff_result.changes if c.change_type == "added"]
        assert len(added) >= 3, f"应有至少3处新增，实际{len(added)}"

    def test_has_removed(self, version_diff_result):
        removed = [c for c in version_diff_result.changes if c.change_type == "removed"]
        assert len(removed) >= 1, f"应有至少1处删除，实际{len(removed)}"


class TestVersionCompareContentAccuracy:
    """具体差异内容验证"""

    def test_p1_response_time_change(self, version_diff_result):
        """P1响应时间 30分钟→15分钟"""
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("30分钟" in c.old_text and "15分钟" in c.new_text for c in modified)
        assert found, "未检测到 P1 响应时间变更 (30→15分钟)"

    def test_p2_recovery_time_change(self, version_diff_result):
        """P2恢复时间 4小时→3小时"""
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("4小时" in c.old_text and "3小时" in c.new_text for c in modified)
        assert found, "未检测到 P2 恢复时间变更 (4→3小时)"

    def test_disk_threshold_change(self, version_diff_result):
        """磁盘阈值 75%→70%"""
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("75%" in c.old_text and "70%" in c.new_text for c in modified)
        assert found, "未检测到磁盘阈值变更 (75%→70%)"

    def test_notification_time_change(self, version_diff_result):
        """故障通知时间 5分钟→3分钟"""
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("5分钟" in c.old_text and "3分钟" in c.new_text for c in modified)
        assert found, "未检测到故障通知时间变更 (5→3分钟)"

    def test_shift_staff_change(self, version_diff_result):
        """白班人数 2人→3人"""
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("2人" in c.old_text and "3人" in c.new_text for c in modified)
        assert found, "未检测到白班人数变更 (2→3人)"

    def test_new_chapter_added(self, version_diff_result):
        """新增第六章信息安全管理"""
        added = [c for c in version_diff_result.changes if c.change_type == "added"]
        found = any("堡垒机" in c.new_text or "访问控制" in c.new_text for c in added)
        assert found, "未检测到第六章信息安全管理的新增内容"

    def test_vpn_gateway_removed(self, version_diff_result):
        """VPN网关被删除"""
        removed = [c for c in version_diff_result.changes if c.change_type == "removed"]
        found = any("VPN" in c.old_text or "ASA 5525" in c.old_text for c in removed)
        assert found, "未检测到 VPN 网关删除"


class TestVersionCompareTableChanges:
    """跨页表格差异验证"""

    def test_firewall_model_upgrade(self, version_diff_result):
        """防火墙 PA-850 → PA-3260"""
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("PA-850" in c.old_text and "PA-3260" in c.new_text for c in modified)
        assert found, "未检测到防火墙型号升级 (PA-850→PA-3260)"

    def test_load_balancer_upgrade(self, version_diff_result):
        """负载均衡器 i2600 → i4800"""
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("i2600" in c.old_text and "i4800" in c.new_text for c in modified)
        assert found, "未检测到负载均衡器型号升级 (i2600→i4800)"

    def test_waf_device_added(self, version_diff_result):
        """WAF设备新增"""
        added = [c for c in version_diff_result.changes if c.change_type == "added"]
        found = any("WAF" in c.new_text or "Imperva" in c.new_text for c in added)
        assert found, "未检测到 WAF 设备新增"

    def test_k8s_platform_added(self, version_diff_result):
        """容器平台K8s新增"""
        added = [c for c in version_diff_result.changes if c.change_type == "added"]
        found = any("Kubernetes" in c.new_text or "容器" in c.new_text for c in added)
        assert found, "未检测到容器平台 K8s 新增"

    def test_database_version_upgrade(self, version_diff_result):
        """数据库 Oracle 19c → 21c"""
        modified = [c for c in version_diff_result.changes if c.change_type == "modified"]
        found = any("19c" in c.old_text and "21c" in c.new_text for c in modified)
        assert found, "未检测到数据库版本升级 (Oracle 19c→21c)"


class TestVersionCompareSummary:
    """输出摘要验证"""

    def test_print_all_changes(self, version_diff_result):
        """打印所有检测到的差异（用于人工检视）"""
        changes = version_diff_result.changes
        print(f"\n{'=' * 60}")
        print(f"版本对比结果: {len(changes)} 处差异")
        print(f"  旧版段落数: {version_diff_result.old_paragraph_count}")
        print(f"  新版段落数: {version_diff_result.new_paragraph_count}")
        print(f"  modified: {sum(1 for c in changes if c.change_type == 'modified')}")
        print(f"  added: {sum(1 for c in changes if c.change_type == 'added')}")
        print(f"  removed: {sum(1 for c in changes if c.change_type == 'removed')}")
        print(f"{'=' * 60}")
        for i, c in enumerate(changes, 1):
            icon = {"modified": "✏️", "added": "➕", "removed": "➖"}[c.change_type]
            print(f"\n{icon} #{i} [{c.change_type}] {c.section}")
            print(f"  位置: {c.location}")
            if c.old_text:
                print(f"  旧: {c.old_text[:120]}{'...' if len(c.old_text) > 120 else ''}")
            if c.new_text:
                print(f"  新: {c.new_text[:120]}{'...' if len(c.new_text) > 120 else ''}")
            if c.similarity:
                print(f"  相似度: {c.similarity:.2f}")
        print(f"\n{'=' * 60}")
