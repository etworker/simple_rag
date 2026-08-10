"""
version_diff — 文档差异检测引擎

用法:
    from version_diff import DiffEngine, Config, DiffResult

    engine = DiffEngine(config={...})
    engine.add("a.pdf")
    engine.add("b.pdf")
    result = engine.pre_review("new.pdf", on_progress=callback)
    print(result.inconsistencies)
"""

from version_diff.config import Config
from version_diff.engine import DiffEngine
from version_diff.judge import JudgeResult, judge_pairs
from version_diff.conflict import detect_conflicts
from version_diff.llm_util import call_llm_json
from version_diff.models import DiffResult, Inconsistency, VersionChange, VersionDiffResult

__all__ = ["Config", "DiffEngine", "DiffResult", "Inconsistency", "JudgeResult", "VersionChange", "VersionDiffResult", "judge_pairs", "detect_conflicts", "call_llm_json"]
