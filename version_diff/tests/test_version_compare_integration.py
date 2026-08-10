"""
version_compare 的离线集成测试（真实 embedding，LLM 过滤路径被 mock 接管）。

目的：在不依赖外部 LLM 网络的前提下，验证端到端流水线确实能
  解析 PDF → 语义配对 → 文本/表格 diff → 调用 LLM 过滤实质性变更
并产出非空、结构正确的 VersionDiffResult。

LLM 调用被替换为确定性 mock：按 prompt 中的「--- 第 N 处 ---」数量，
返回「全部保留（keep=True）」的判定，从而验证 _filter_substantive_changes
确实把批量结果映射回了变更对象（c.summary 被写入）。

数据依赖：data/pdf/IT运维管理规范/v1 与 v2 两个同名异版 PDF。
缺失时自动 skip（CI 可选装数据）。
"""

import os
import pathlib
import unittest.mock as mock

import pytest

from version_diff import DiffEngine

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OLD_PDF = REPO_ROOT / "data" / "pdf" / "IT运维管理规范" / "v1" / "IT运维管理规范.pdf"
NEW_PDF = REPO_ROOT / "data" / "pdf" / "IT运维管理规范" / "v2" / "IT运维管理规范.pdf"


def _mock_filter(prompt, llm_config, max_retries=2, retry_backoff=1.0):
    """确定性 mock：本批 modified 条数 = prompt 中 '--- 第 ' 的出现次数。"""
    count = prompt.count("--- 第 ")
    return [
        {"index": i + 1, "keep": True, "summary": "（离线 mock：保留）"}
        for i in range(count)
    ]


@pytest.fixture
def engine():
    cfg = {
        "embedding": {"model": "BAAI/bge-base-zh-v1.5", "device": "cpu"},
        "llm": {
            "provider": "bedrock_converse",
            "model": "zai.glm-4.7-flash",
            "max_tokens": 2048,
            "timeout": 120,
        },
        "diff": {"similarity_threshold": 0.80, "top_k": 3, "batch_size": 5},
    }
    return DiffEngine(config=cfg)


@pytest.mark.skipif(
    not (OLD_PDF.exists() and NEW_PDF.exists()),
    reason="缺少 data/pdf/IT运维管理规范 v1/v2 测试数据",
)
def test_version_compare_pipeline(engine):
    """真实 embedding + mock LLM 过滤，应产出非空且结构正确的差异结果。"""
    with mock.patch(
        "version_diff.engine.call_llm_json", side_effect=_mock_filter
    ) as m:
        result = engine.version_compare(str(OLD_PDF), str(NEW_PDF))

    # LLM 过滤路径确实被调用
    assert m.call_count > 0, "LLM 过滤路径未被调用"

    # 解析与配对正常
    assert result.old_paragraph_count > 0
    assert result.new_paragraph_count > 0
    assert len(result.changes) > 0, "未识别到任何版本差异"

    # 至少存在 modified 变更，且其 summary 由 mock LLM 写入
    modified = [c for c in result.changes if c.change_type == "modified"]
    assert len(modified) > 0, "未识别到修改类变更"
    assert any(c.summary for c in modified), "LLM 过滤结果未映射回变更对象"

    # 三类变更标签合法
    valid_types = {"added", "removed", "modified"}
    assert all(c.change_type in valid_types for c in result.changes)
