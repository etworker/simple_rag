"""judge.py MVP 修正的单元测试。

覆盖：
  B1. Prompt 配置化（优先级：prompt_file > prompt_template > 内置）
  B2. 输出精简（doc_a_says/doc_b_says 由程序回填）
  B3. Batch size 配置化（默认 5）
  B4. 失败时拆半重试
"""

from types import SimpleNamespace
from unittest.mock import patch

# ============================================================
# B1: Prompt 配置化
# ============================================================


class TestPromptResolution:
    """prompt 优先级验证"""

    def test_prompt_file_priority(self, tmp_path):
        """prompt_file 优先于 prompt_template 和内置默认"""
        from version_diff.judge import _resolve_prompt_template

        # 创建临时 prompt 文件
        pf = tmp_path / "custom_prompt.txt"
        pf.write_text("CUSTOM FILE: {count} {items}", encoding="utf-8")

        config = {
            "prompt_file": str(pf),
            "prompt_template": "INLINE TEMPLATE: {count} {items}",
        }
        result = _resolve_prompt_template(config)
        assert result.startswith("CUSTOM FILE")

    def test_prompt_template_priority(self):
        """无 prompt_file 时使用 prompt_template"""
        from version_diff.judge import _resolve_prompt_template

        config = {
            "prompt_file": "/nonexistent/path.txt",
            "prompt_template": "INLINE: {count} {items}",
        }
        result = _resolve_prompt_template(config)
        assert result == "INLINE: {count} {items}"

    def test_default_fallback(self):
        """都没配置时使用内置默认"""
        from version_diff.judge import CONSISTENCY_JUDGE_PROMPT, _resolve_prompt_template

        config = {}
        result = _resolve_prompt_template(config)
        assert result == CONSISTENCY_JUDGE_PROMPT


# ============================================================
# B2: 输出精简 — doc_a_says / doc_b_says 由程序回填
# ============================================================


class TestProgrammaticBackfill:
    """doc_a_says / doc_b_says 不再依赖 LLM 输出"""

    def test_backfill_from_original_pair(self):
        """确认从原始 pair 中截取回填"""
        from version_diff.judge import _process_batch_results

        # 构造批次中的 item（鸭子类型）
        item = SimpleNamespace(
            para_a=SimpleNamespace(
                text="文档A的完整段落内容：每月进行一次安全巡检，由运维组负责执行。" * 2,
                source_file="safety_manual.pdf",
                location="第3页 / §2.1",
            ),
            para_b=SimpleNamespace(
                text="文档B的完整段落内容：每季度进行一次安全巡检，由安保组负责执行。" * 2,
                source_file="ops_manual.pdf",
                location="第5页 / §3.2",
            ),
        )
        batch = [item]
        # LLM 返回中不包含 doc_a_says / doc_b_says
        llm_results = [
            {"index": 1, "inconsistent": True, "confidence": "high", "point": "巡检频率"}
        ]

        new_items, _suspects = _process_batch_results(batch, llm_results)
        assert len(new_items) == 1
        result_item = new_items[0]
        # 验证程序回填了 A/B 摘要
        assert "文档A" in result_item.llm_doc_a_says
        assert "文档B" in result_item.llm_doc_b_says
        # 验证长度截断（≤80 字符）
        assert len(result_item.llm_doc_a_says) <= 80
        assert len(result_item.llm_doc_b_says) <= 80
        # 验证 reason 包含 point
        assert "巡检频率" in result_item.llm_reason

    def test_low_confidence_goes_to_suspect(self):
        """confidence=low → suspect_items"""
        from version_diff.judge import _process_batch_results

        item = SimpleNamespace(
            para_a=SimpleNamespace(text="A内容", source_file="a.pdf", location=""),
            para_b=SimpleNamespace(text="B内容", source_file="b.pdf", location=""),
        )
        batch = [item]
        llm_results = [
            {"index": 1, "inconsistent": True, "confidence": "low", "point": "审批层级"}
        ]

        new_items, suspects = _process_batch_results(batch, llm_results)
        assert len(new_items) == 0
        assert len(suspects) == 1
        assert suspects[0].__dict__["category"] == "suspect"


# ============================================================
# B3: Batch size 配置化（默认 5）
# ============================================================


class TestBatchSizeConfig:
    """batch_size 配置"""

    def test_default_batch_size_is_5(self):
        """未显式配置 batch_size 时默认为 5"""
        from version_diff.judge import _calculate_batch_size

        # 不传 batch_size 或传 0
        llm_config = {}
        result = _calculate_batch_size(llm_config, [])
        assert result == 5

    def test_explicit_batch_size(self):
        """显式配置 batch_size 生效"""
        from version_diff.judge import _calculate_batch_size

        llm_config = {"batch_size": 10}
        result = _calculate_batch_size(llm_config, [])
        assert result == 10


# ============================================================
# B4: 失败时拆半重试
# ============================================================


class TestSplitHalfRetry:
    """批次失败时拆半重试"""

    def test_successful_first_try(self):
        """首次成功 → 直接返回"""
        from version_diff.judge import _judge_batch_with_split_retry

        items = [SimpleNamespace(para_a=SimpleNamespace(text="A", source_file="a", location=""),
                                  para_b=SimpleNamespace(text="B", source_file="b", location=""))] * 4

        with patch("version_diff.judge._judge_batch") as mock_judge:
            mock_judge.return_value = [
                {"index": i, "inconsistent": False} for i in range(1, 5)
            ]
            results, has_failure = _judge_batch_with_split_retry(items, {}, "")
            assert results is not None
            assert has_failure is False
            assert mock_judge.call_count == 1

    def test_split_on_failure(self):
        """首次失败 → 拆半重试"""
        from version_diff.judge import _judge_batch_with_split_retry

        items = [SimpleNamespace(para_a=SimpleNamespace(text="A", source_file="a", location=""),
                                  para_b=SimpleNamespace(text="B", source_file="b", location=""))] * 6

        call_count = [0]
        def mock_judge(batch_items, config, template):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # 首次失败
            # 拆半后成功
            return [{"index": i, "inconsistent": False} for i in range(1, len(batch_items) + 1)]

        with patch("version_diff.judge._judge_batch", side_effect=mock_judge):
            results, has_failure = _judge_batch_with_split_retry(items, {}, "")
            assert results is not None
            assert len(results) == 6
            assert has_failure is False
            assert call_count[0] == 3  # 1 原始 + 2 拆半

    def test_split_to_single_items(self):
        """批次失败后应继续拆到单条，单条失败仍保留失败标记"""
        from version_diff.judge import _judge_batch_with_split_retry

        items = [SimpleNamespace(para_a=SimpleNamespace(text=str(i), source_file="a", location=""),
                                  para_b=SimpleNamespace(text="B", source_file="b", location="")) for i in range(2)]
        with patch("version_diff.judge._judge_batch", return_value=None) as mock_judge:
            results, has_failure = _judge_batch_with_split_retry(items, {}, "")
            assert results is None
            assert has_failure is True
            assert mock_judge.call_count == 3  # 原批次 + 两个单条

    def test_successful_results_are_reused_from_cache(self, tmp_path):
        """完整结果应按单条写缓存，后续相同输入不再调用 LLM"""
        from version_diff.judge import _judge_batch_with_cache

        items = [SimpleNamespace(
            para_a=SimpleNamespace(text="A", source_file="a", location="1"),
            para_b=SimpleNamespace(text="B", source_file="b", location="2"),
        )]
        response = [{"index": 1, "inconsistent": False, "point": ""}]
        with patch("version_diff.judge._judge_batch", return_value=response) as first:
            results, failed = _judge_batch_with_cache(items, {}, "{count} {items}", str(tmp_path))
            assert results == response
            assert failed is False
            assert first.call_count == 1

        with patch("version_diff.judge._judge_batch", side_effect=AssertionError("cache miss")) as second:
            results, failed = _judge_batch_with_cache(items, {}, "{count} {items}", str(tmp_path))
            assert results == response
            assert failed is False
            assert second.call_count == 0


# ============================================================
# Prompt 内容验证
# ============================================================


class TestPromptContent:
    """验证 prompt 文件不再要求 doc_a_says / doc_b_says"""

    def test_no_doc_ab_says_in_prompt(self):
        """默认 prompt 不包含 doc_a_says / doc_b_says 的输出要求"""
        from version_diff.judge import CONSISTENCY_JUDGE_PROMPT

        # prompt 中不应有对模型输出 doc_a_says 的要求
        assert "doc_a_says" not in CONSISTENCY_JUDGE_PROMPT
        assert "doc_b_says" not in CONSISTENCY_JUDGE_PROMPT

    def test_prompt_has_placeholders(self):
        """prompt 模板包含必要的占位符"""
        from version_diff.judge import CONSISTENCY_JUDGE_PROMPT

        assert "{count}" in CONSISTENCY_JUDGE_PROMPT
        assert "{items}" in CONSISTENCY_JUDGE_PROMPT


# ============================================================
# 同一事项边界与安全降级
# ============================================================


def test_same_heading_is_structural_not_a_conflict():
    """同名章节标题只改变编号时，不应进入 LLM 矛盾结果。"""
    from doc_parser.models import Paragraph
    from version_diff.matcher import compute_diff
    from version_diff.prefilter import pre_classify

    item = compute_diff(
        Paragraph(text="4.1.3 术语与定义", block_type="heading"),
        Paragraph(text="2.4.3 术语与定义", block_type="heading"),
        similarity=0.97,
    )
    result = pre_classify(item)
    assert result.category == "structural"


def test_equivalent_employee_scope_is_wording_only():
    """公司所有员工/公司全体员工不应被判为实质矛盾。"""
    from doc_parser.models import Paragraph
    from version_diff.matcher import compute_diff
    from version_diff.prefilter import pre_classify

    item = compute_diff(
        Paragraph(text="本规定适用于公司所有员工"),
        Paragraph(text="适用于公司全体员工"),
        similarity=0.92,
    )
    result = pre_classify(item)
    assert result.category == "wording"


def test_missing_confidence_is_suspect_not_confirmed():
    """LLM 对 true 结果未给 confidence 时，不能默认升级为确认矛盾。"""
    from version_diff.judge import _process_batch_results

    item = SimpleNamespace(
        para_a=SimpleNamespace(text="A", source_file="a", location="1"),
        para_b=SimpleNamespace(text="B", source_file="b", location="2"),
    )
    confirmed, suspects = _process_batch_results(
        [item], [{"index": 1, "inconsistent": True, "point": "事项"}]
    )
    assert confirmed == []
    assert suspects == [item]


def test_chinese_document_date_is_metadata():
    """声明页中文日期变化属于文档元数据，不应送 LLM。"""
    from doc_parser.models import Paragraph
    from version_diff.matcher import compute_diff
    from version_diff.prefilter import pre_classify

    item = compute_diff(
        Paragraph(text="奥凯航空有限公司随时准备接受局方的检查。分管领导：二零二四年四月三十日"),
        Paragraph(text="奥凯航空有限公司随时准备接受局方的检查。分管领导：二零二三年七月十八日"),
        similarity=0.99,
    )
    result = pre_classify(item)
    assert result.category == "metadata"


def test_reference_lists_are_not_content_conflicts():
    """支持性文件纯引用清单不同，不应直接判正文矛盾。"""
    from doc_parser.models import Paragraph
    from version_diff.matcher import compute_diff
    from version_diff.prefilter import pre_classify

    item = compute_diff(
        Paragraph(text="《网络与信息安全管理手册》《奥凯航空OA系统文件审批管理办法》"),
        Paragraph(text="《民航网络与信息安全管理暂行办法》（MD-PE-2013-01）"),
        similarity=0.86,
    )
    result = pre_classify(item)
    assert result.category == "reference"


def test_explicitly_different_it_objects_are_not_paired_as_conflicts():
    """企业邮箱与办公电脑是不同控制对象，不应进入矛盾判断。"""
    from doc_parser.models import Paragraph
    from version_diff.matcher import compute_diff
    from version_diff.prefilter import pre_classify

    item = compute_diff(
        Paragraph(text="信息技术部负责企业邮箱开通、变更和维护"),
        Paragraph(text="信息技术部负责办公电脑的安装、维修和防病毒管理"),
        similarity=0.84,
    )
    result = pre_classify(item)
    assert result.category == "scope"
