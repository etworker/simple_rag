"""
CrossNoiseFilter（跨文档版式噪声过滤）单测。

验证内置可配置噪声过滤：目录/记录清单/页码占位等被过滤，实质内容保留，
超参数（patterns/min_length/dir_entry_max_length/enabled）可配置。
"""

from version_diff import CrossNoiseFilter
from version_diff.models import VersionChange


def _c(change_type, old="", new=""):
    return VersionChange(change_type=change_type, section="", location="", old_text=old, new_text=new)


class TestIsNoise:
    def test_empty_is_noise(self):
        assert CrossNoiseFilter().is_noise("")

    def test_short_is_noise(self):
        assert CrossNoiseFilter().is_noise("abc")

    def test_real_content_not_noise(self):
        nf = CrossNoiseFilter()
        assert not nf.is_noise("0.6.1.2 编制依据：根据《信息技术管理手册》制定本手册，明确管理职责。")

    def test_dir_entry_with_page_is_noise(self):
        nf = CrossNoiseFilter()
        assert nf.is_noise("1 组织机构及职责 35 页")

    def test_dir_entry_with_number_is_noise(self):
        nf = CrossNoiseFilter()
        assert nf.is_noise("1.1 信息技术部职责 1.1-1")

    def test_record_list_is_noise(self):
        nf = CrossNoiseFilter()
        assert nf.is_noise("0.1 手册记录清单")

    def test_revision_note_is_noise(self):
        nf = CrossNoiseFilter()
        assert nf.is_noise("B 根据公司下发的各类规范性或程序性文件，明确信息技术管理的范围")


class TestConfigurable:
    def test_disabled_keeps_all(self):
        nf = CrossNoiseFilter({"enabled": False})
        real, noise = nf.filter_changes([_c("added", new="1 组织机构及职责 35 页")])
        assert len(real) == 1
        assert noise == []

    def test_custom_pattern(self):
        nf = CrossNoiseFilter({"patterns": [r"^审批人\s*[：:]\s*\S+$"]})
        assert nf.is_noise("审批人：张三")  # 自定义模式生效
        # 目录条目启发式独立于 patterns，仍生效
        assert nf.is_noise("1 组织机构及职责 35 页")
        # 自定义模式不匹配的普通内容 → 非噪声
        assert not nf.is_noise("0.6.1.2 编制依据：根据手册制定本制度，明确职责分工。")

    def test_min_length_configurable(self):
        nf = CrossNoiseFilter({"min_length": 20})
        # "1 组织机构及职责 35 页" 长度 > 20 但无 min_length 触发，仍靠模式
        assert not nf.is_noise("这是一个足够长的实质内容句子，超过二十字。")
        nf2 = CrossNoiseFilter({"min_length": 20, "patterns": []})
        assert nf2.is_noise("短文本")  # 短于 min_length


class TestFilterChanges:
    def test_added_real_kept(self):
        nf = CrossNoiseFilter()
        changes = [_c("added", new="0.6.1.2 编制依据：根据《信息技术管理手册》制定本手册。")]
        real, noise = nf.filter_changes(changes)
        assert len(real) == 1
        assert noise == []

    def test_added_noise_moved(self):
        nf = CrossNoiseFilter()
        changes = [_c("added", new="1 组织机构及职责 35 页")]
        real, noise = nf.filter_changes(changes)
        assert real == []
        assert len(noise) == 1

    def test_removed_noise_moved(self):
        nf = CrossNoiseFilter()
        changes = [_c("removed", old="3.1 3.1-1 安全目标管理程序")]
        real, noise = nf.filter_changes(changes)
        assert real == []
        assert len(noise) == 1

    def test_modified_kept_if_any_real(self):
        nf = CrossNoiseFilter()
        changes = [_c("modified", old="旧版实质内容条款。", new="新版实质内容条款。")]
        real, noise = nf.filter_changes(changes)
        assert len(real) == 1
        assert noise == []

    def test_mixed_preserves_order(self):
        nf = CrossNoiseFilter()
        changes = [
            _c("added", new="1 目录条目 35 页"),
            _c("added", new="0.6.1.2 编制依据：根据手册制定本制度，明确职责分工。"),
            _c("removed", old="2 日常管理 38 页"),
        ]
        real, noise = nf.filter_changes(changes)
        assert len(real) == 1
        assert real[0].new_text.startswith("0.6.1.2")
        assert len(noise) == 2
