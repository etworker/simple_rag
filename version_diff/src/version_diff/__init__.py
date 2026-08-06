"""
versiondiff — 文档差异检测引擎

用法:
    from versiondiff import DiffEngine, Config, DiffResult

    engine = DiffEngine(config={...})
    engine.add("a.pdf")
    engine.add("b.pdf")
    result = engine.pre_review("new.pdf", on_progress=callback)
    print(result.inconsistencies)
"""

from version_diff.config import Config
from version_diff.engine import DiffEngine
from version_diff.judge import JudgeResult
from version_diff.models import DiffResult, Inconsistency

__all__ = ["Config", "DiffEngine", "DiffResult", "Inconsistency", "JudgeResult"]
