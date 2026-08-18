"""跨页段落合并 (merge_cross_page_paragraphs) 单元测试。

覆盖计划 A4 中的场景：
  1. 普通段落跨页
  2. 条款跨页
  3. 章节标题后接正文
  4. 页眉页脚干扰（不误合并）
  5. 表格结束后接正文（不误合并）
  6. 不同章节不合并
  7. 合并后长度上限生效
"""

from doc_parser._merge import merge_cross_page_paragraphs
from doc_parser.models import Paragraph


def _p(text, page=1, page_end=0, chapter="", chapter_title="", source_file="test.pdf", order=0, block_type="paragraph"):
    """辅助构造 Paragraph"""
    return Paragraph(
        text=text, page=page, page_end=page_end,
        chapter=chapter, chapter_title=chapter_title,
        source_file=source_file, index=0, order=order,
        block_type=block_type,
    )


class TestBasicMerge:
    """基础跨页合并"""

    def test_incomplete_sentence_merges(self):
        """普通段落跨页：上一页以未完成句结尾"""
        paras = [
            _p("本公司致力于建设安全生产管理体系，确保所有人员了解并遵守安全防护策", page=3),
            _p("略，阻止任何可能导致事故的行为发生。", page=4),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 1
        assert "安全防护策略" in result[0].text
        assert result[0].page == 3
        assert result[0].page_end == 4

    def test_clause_cross_page(self):
        """条款跨页：编号条款的描述跨页"""
        paras = [
            _p("5.1.3 巡检人员应按照规定路线进行巡检，发现异常情况应及时记录并上报", page=7),
            _p("相关负责人，不得隐瞒或延误。", page=8),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 1
        assert "5.1.3" in result[0].text
        assert "不得隐瞒" in result[0].text

    def test_complete_sentence_no_merge(self):
        """上一段以完整句号结尾 → 不合并"""
        paras = [
            _p("本条款适用于所有在岗人员。", page=5),
            _p("下一条规定了考核标准。", page=6),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 2


class TestNoMerge:
    """不应合并的场景"""

    def test_different_chapter_no_merge(self):
        """不同章节 → 不合并"""
        paras = [
            _p("上一章的最后一段内容", page=10, chapter="3", chapter_title="安全管理"),
            _p("新章节的第一段内容", page=11, chapter="4", chapter_title="应急管理"),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 2

    def test_heading_block_no_merge(self):
        """章节标题后面的正文 → 不合并"""
        paras = [
            _p("3.1 日常维护", page=5, chapter="3.1", chapter_title="日常维护", block_type="heading"),
            _p("日常维护应包含以下内容", page=5),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 2

    def test_new_list_item_no_merge(self):
        """下一块是新列表项 → 不合并"""
        paras = [
            _p("以下是具体要求", page=3),
            _p("1. 每日进行设备检查", page=4),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 2

    def test_new_heading_no_merge(self):
        """下一块是新章节标题格式 → 不合并"""
        paras = [
            _p("上述规定自发布之日起执行", page=9),
            _p("4.2 应急预案管理", page=10),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 2

    def test_table_block_no_merge(self):
        """表格块 → 不与正文合并"""
        paras = [
            _p("以下为检查要求", page=5, block_type="paragraph"),
            _p("（表格内容）", page=6, block_type="table"),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 2

    def test_same_page_no_merge(self):
        """同一页内已分好段的 → 不应再合并"""
        paras = [
            _p("第一段内容说明了背景", page=3),
            _p("第二段内容说明了目的", page=3),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 2

    def test_different_file_no_merge(self):
        """不同文件 → 不合并"""
        paras = [
            _p("文件A的最后一段", page=10, source_file="a.pdf"),
            _p("文件B的第一段", page=1, source_file="b.pdf"),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 2


class TestMaxLength:
    """合并长度上限"""

    def test_exceeds_max_length_no_merge(self):
        """合并后超过上限 → 不合并"""
        long_text = "这是一段很长的文字" * 60  # ~540 chars
        paras = [
            _p(long_text, page=3),
            _p("续页内容同样很长" * 60, page=4),
        ]
        result = merge_cross_page_paragraphs(paras, max_merged_length=600)
        assert len(result) == 2


class TestCJKJoin:
    """CJK 拼接规则"""

    def test_cjk_no_space(self):
        """中文←→中文：直接相连"""
        paras = [
            _p("安全防护策", page=1),
            _p("略的具体内容", page=2),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert "策略" in result[0].text
        assert " " not in result[0].text[:10]  # 中文之间无空格

    def test_mixed_with_space(self):
        """中英混排：保留空格"""
        paras = [
            _p("使用 Windows", page=1),
            _p("Server 系统进行部署。", page=2),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert "Windows Server" in result[0].text


class TestMultipleMerges:
    """连续多页合并"""

    def test_three_pages_merge(self):
        """三页连续段落合并"""
        paras = [
            _p("第一部分内容在本页开始，描述了安全管理体系的基本框架和适用范", page=5),
            _p("围，以及相关的法律法规依据。在此基础上，各部门应根据实际情况制定具体实施方", page=6),
            _p("案，确保体系有效运行。", page=7),
        ]
        result = merge_cross_page_paragraphs(paras)
        assert len(result) == 1
        assert result[0].page == 5
        assert result[0].page_end == 7


class TestReindex:
    """合并后重新编号"""

    def test_reindexed(self):
        """合并后 index 连续"""
        paras = [
            _p("第一段完整内容。", page=1),
            _p("第二段开始在这里，内容跨", page=2),
            _p("页继续。", page=3),
            _p("第三段完整内容。", page=4),
        ]
        result = merge_cross_page_paragraphs(paras)
        for i, p in enumerate(result, 1):
            assert p.index == i
