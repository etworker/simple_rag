"""
doc_parser 元数据行剥离测试（离线纯单测）

验证：_split_stream 会把独立成行的版本管理元数据行（修订日期/发布日期/独立日期/版次）
从正文段落流中剥离，避免其被并入相邻正文造成"修订日期：xxxx 被拼进句子中间"。
"""

from doc_parser.parser import (
    _split_stream,
    DEFAULT_CONFIG,
)


def _split(text):
    """用默认配置分段，返回段落文本列表"""
    paras = _split_stream(text, DEFAULT_CONFIG)
    return [t for t, _, _ in paras]


class TestMetadataLineStripped:
    def test_revision_date_line_not_merged_into_paragraph(self):
        text = (
            "5.1.5.6 核心网络设备、网络带宽的业务处理能力应满足业务高峰期的\n"
            "修订日期：2026-05-08\n"
            "需求。\n"
        )
        paras = _split(text)
        # 修订日期行不应混入任何正文段落
        joined = " ".join(paras)
        assert "修订日期" not in joined
        assert "2026-05-08" not in joined
        # 正文内容仍在
        assert any("业务高峰期" in p for p in paras)

    def test_standalone_date_line_stripped(self):
        text = "a 办公防火墙连通性测试；\n2026-05-08\nb 服务器防火墙连通性测试；\n"
        paras = _split(text)
        joined = " ".join(paras)
        assert "2026-05-08" not in joined
        assert "办公防火墙" in joined
        assert "服务器防火墙" in joined

    def test_version_stamp_line_stripped(self):
        text = "版次：R5-22\n5.1.5.7 口令条款内容。\n"
        paras = _split(text)
        joined = " ".join(paras)
        assert "版次" not in joined
        assert "口令条款" in joined

    def test_normal_content_not_stripped(self):
        # 普通正文行不应被误剥
        text = "核心网络设备的业务处理能力应满足业务高峰期的需求。\n5.1.5.7 口令条款。\n"
        paras = _split(text)
        joined = " ".join(paras)
        assert "业务高峰期" in joined
        assert "口令条款" in joined

    def test_empty_lines_still_split(self):
        # 空行分段仍生效
        text = "第一段内容，以句号结尾。\n\n第二段内容，以句号结尾。\n"
        paras = _split(text)
        assert len(paras) >= 2

    def test_custom_noise_line_pattern(self):
        # 用户自定义元数据行模式
        cfg = dict(DEFAULT_CONFIG)
        cfg["noise_line_patterns"] = [r"^审批人\s*[：:]\s*\S+$"]
        text = "正文第一行。\n审批人：张三\n"
        paras = _split_stream(text, cfg)
        joined = " ".join(t for t, _, _ in paras)
        assert "审批人" not in joined
