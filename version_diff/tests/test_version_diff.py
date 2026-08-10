"""
version_diff 修复验证测试

重点测试:
  - JudgeResult 数据模型和 filter_diffs 返回值
  - VectorStore.compute_config_hash 配置哈希
  - TextDiffItem 结构化字段
  - DiffEngine.check_conflicts 实现
  - 统计字段语义正确性
"""
import pytest
from unittest.mock import patch, MagicMock

from version_diff.judge import JudgeResult, filter_diffs
from version_diff.vectorstore import VectorStore
from version_diff.matcher import TextDiffItem, compute_diff
from version_diff.models import DiffResult, Inconsistency
from doc_parser.models import Paragraph


class TestJudgeResult:
    def test_default(self):
        r = JudgeResult()
        assert r.inconsistent_items == []
        assert r.rule_filtered == 0
        assert r.llm_judged == 0

    def test_with_values(self):
        r = JudgeResult(inconsistent_items=[1, 2], rule_filtered=3, llm_judged=5)
        assert len(r.inconsistent_items) == 2
        assert r.rule_filtered == 3
        assert r.llm_judged == 5


class TestFilterDiffsReturnJudgeResult:
    """filter_diffs 应返回 JudgeResult 而非 list"""

    def test_empty_input(self):
        result = filter_diffs([])
        assert isinstance(result, JudgeResult)
        assert result.inconsistent_items == []
        assert result.rule_filtered == 0
        assert result.llm_judged == 0

    def test_returns_judge_result_type(self):
        """确保返回类型是 JudgeResult，不是 list"""
        para_a = Paragraph(text="每天备份一次", source_file="A.docx")
        para_b = Paragraph(text="每周备份一次", source_file="B.docx")
        item = compute_diff(para_a, para_b, similarity=0.85)

        result = filter_diffs([item], llm_config={}, judge_config={})
        assert isinstance(result, JudgeResult)
        assert isinstance(result.rule_filtered, int)
        assert isinstance(result.llm_judged, int)


class TestConfigHash:
    def test_different_config_different_hash(self):
        h1 = VectorStore.compute_config_hash(
            {"model": "A"}, {"model": "B"}
        )
        h2 = VectorStore.compute_config_hash(
            {"model": "C"}, {"model": "D"}
        )
        assert h1 != h2

    def test_same_config_same_hash(self):
        h1 = VectorStore.compute_config_hash(
            {"model": "A"}, {"model": "B"}
        )
        h2 = VectorStore.compute_config_hash(
            {"model": "A"}, {"model": "B"}
        )
        assert h1 == h2

    def test_empty_config(self):
        h = VectorStore.compute_config_hash({}, {})
        assert len(h) == 8  # md5[:8]

    def test_default_config_hash_matches_old_behavior(self):
        """空配置的哈希应等于 sha256(b'')[:8] = e3b0c442"""
        vs = VectorStore()
        assert vs._config_hash == "e3b0c442"


class TestTextDiffItemStructuredFields:
    """TextDiffItem 应有结构化字段"""

    def test_has_structured_fields(self):
        item = TextDiffItem(
            para_a=Paragraph(text="a"),
            para_b=Paragraph(text="b"),
            similarity=0.9,
        )
        assert hasattr(item, 'llm_point')
        assert hasattr(item, 'llm_doc_a_says')
        assert hasattr(item, 'llm_doc_b_says')
        assert item.llm_point == ''
        assert item.llm_doc_a_says == ''
        assert item.llm_doc_b_says == ''


class TestCheckConflicts:
    """测试 DiffEngine.check_conflicts 实现"""

    def test_empty_input(self):
        from version_diff.engine import DiffEngine
        engine = DiffEngine(config={})
        result = engine.check_conflicts([])
        assert result == []

    def test_single_passage_no_conflict(self):
        from version_diff.engine import DiffEngine
        engine = DiffEngine(config={})
        result = engine.check_conflicts([
            {"text": "每天备份", "source_file": "A.docx", "location": "第1页"}
        ])
        assert result == []

    def test_same_file_no_conflict(self):
        """来自同一文档的段落不应触发冲突检测"""
        from version_diff.engine import DiffEngine
        engine = DiffEngine(config={})
        result = engine.check_conflicts([
            {"text": "每天备份", "source_file": "A.docx", "location": "第1页"},
            {"text": "每周备份", "source_file": "A.docx", "location": "第2页"},
        ])
        assert result == []

    def test_identical_text_no_conflict(self):
        """文本完全相同的段落不触发 diff"""
        from version_diff.engine import DiffEngine
        engine = DiffEngine(config={})
        result = engine.check_conflicts([
            {"text": "每天备份", "source_file": "A.docx", "location": "第1页"},
            {"text": "每天备份", "source_file": "B.docx", "location": "第1页"},
        ])
        assert result == []

    def test_different_files_different_text_calls_judge(self):
        """不同文档的不同文本应调用 LLM 判定（judge_pairs）"""
        from version_diff.engine import DiffEngine

        engine = DiffEngine(config={"llm": {"model": "x", "provider": "bedrock"}})
        with patch("version_diff.conflict.judge_pairs", return_value=[]):
            result = engine.check_conflicts([
                {"text": "每天备份", "source_file": "A.docx", "location": "第1页"},
                {"text": "每周备份", "source_file": "B.docx", "location": "第1页"},
            ])
        # judge_pairs 被调用，返回空不一致
        assert result == []

    def test_conflict_detected(self):
        """检测到冲突时应返回 Inconsistency"""
        from version_diff.engine import DiffEngine

        engine = DiffEngine(config={"llm": {"model": "x", "provider": "bedrock"}})
        with patch(
            "version_diff.conflict.judge_pairs",
            return_value=[
                {
                    "index": 1,
                    "inconsistent": True,
                    "point": "备份频率",
                    "doc_a_says": "每天备份",
                    "doc_b_says": "每周备份",
                }
            ],
        ):
            result = engine.check_conflicts([
                {"text": "每天备份", "source_file": "A.docx", "location": "第1页"},
                {"text": "每周备份", "source_file": "B.docx", "location": "第1页"},
            ])

        assert len(result) == 1
        assert isinstance(result[0], Inconsistency)
        assert result[0].point == "备份频率"
        assert result[0].doc_a_says == "每天备份"
        assert result[0].doc_b_says == "每周备份"


class TestDiffResultStats:
    """测试 DiffResult 统计字段的语义正确性"""

    def test_stats_fields(self):
        result = DiffResult(
            inconsistencies=[],
            total_candidates=10,
            rule_filtered=3,
            llm_judged=7,
        )
        assert result.total_candidates == 10
        assert result.rule_filtered == 3
        assert result.llm_judged == 7

    def test_is_safe_when_no_inconsistencies(self):
        result = DiffResult(inconsistencies=[])
        assert result.is_safe is True

    def test_not_safe_when_has_inconsistencies(self):
        inc = Inconsistency(
            point="test", doc_a_file="a", doc_a_location="", doc_a_says="",
            doc_b_file="b", doc_b_location="", doc_b_says="",
        )
        result = DiffResult(inconsistencies=[inc])
        assert result.is_safe is False

    def test_to_dict_structure(self):
        inc = Inconsistency(
            point="备份频率",
            doc_a_file="A.pdf", doc_a_location="第1页", doc_a_says="每天",
            doc_b_file="B.pdf", doc_b_location="第2页", doc_b_says="每周",
            similarity=0.9,
        )
        result = DiffResult(inconsistencies=[inc], total_candidates=5, rule_filtered=2, llm_judged=3)
        d = result.to_dict()
        assert d['is_safe'] is False
        assert d['inconsistency_count'] == 1
        assert d['stats']['total_candidates'] == 5
        assert d['stats']['rule_filtered'] == 2
        assert d['stats']['llm_judged'] == 3
        assert d['inconsistencies'][0]['point'] == '备份频率'
