"""结果数据模型"""

from dataclasses import dataclass, field


@dataclass
class VersionChange:
    """一处版本变更"""

    change_type: str  # "modified" | "added" | "removed"
    section: str  # 所在章节（如 "2.2 备份策略"）
    location: str  # 位置描述
    old_text: str  # 旧版内容（modified/removed 时有值）
    new_text: str  # 新版内容（modified/added 时有值）
    summary: str  # 变更摘要（如"备份频率从每4小时改为每2小时"）
    similarity: float = 0.0  # 段落相似度（modified 时有值）


@dataclass
class VersionDiffResult:
    """版本对比结果"""

    changes: list[VersionChange] = field(default_factory=list)
    old_paragraph_count: int = 0
    new_paragraph_count: int = 0


@dataclass
class Inconsistency:
    """一处文档间不一致"""

    point: str  # 矛盾事项（如"备份频率"）
    doc_a_file: str  # A 文档文件名
    doc_a_location: str  # A 文档位置
    doc_a_says: str  # A 的说法
    doc_b_file: str  # B 文档文件名
    doc_b_location: str  # B 文档位置
    doc_b_says: str  # B 的说法
    similarity: float = 0.0  # 段落相似度


@dataclass
class DiffResult:
    """预审核结果"""

    inconsistencies: list[Inconsistency] = field(default_factory=list)
    total_candidates: int = 0  # 跨文档候选对数
    rule_filtered: int = 0  # 规则预过滤数
    llm_judged: int = 0  # LLM 判断数

    @property
    def is_safe(self) -> bool:
        """是否可以安全入库（无矛盾）"""
        return len(self.inconsistencies) == 0

    def report(self) -> str:
        """生成 Markdown 报告"""
        if self.is_safe:
            return "## ✅ 预审核通过\n\n未发现与已有文档的矛盾。"

        lines = [f"## ⚠️ 发现 {len(self.inconsistencies)} 处文档间不一致\n"]
        for i, inc in enumerate(self.inconsistencies, 1):
            lines.append(f"### 不一致 #{i}: {inc.point}\n")
            lines.append("| | 描述 |")
            lines.append("|---|------|")
            lines.append(
                f"| **{inc.doc_a_file}** {inc.doc_a_location} | {inc.doc_a_says} |"
            )
            lines.append(
                f"| **{inc.doc_b_file}** {inc.doc_b_location} | {inc.doc_b_says} |"
            )
            lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """JSON 序列化"""
        return {
            "is_safe": self.is_safe,
            "inconsistency_count": len(self.inconsistencies),
            "inconsistencies": [
                {
                    "point": inc.point,
                    "doc_a": {
                        "file": inc.doc_a_file,
                        "location": inc.doc_a_location,
                        "says": inc.doc_a_says,
                    },
                    "doc_b": {
                        "file": inc.doc_b_file,
                        "location": inc.doc_b_location,
                        "says": inc.doc_b_says,
                    },
                    "similarity": inc.similarity,
                }
                for inc in self.inconsistencies
            ],
            "stats": {
                "total_candidates": self.total_candidates,
                "rule_filtered": self.rule_filtered,
                "llm_judged": self.llm_judged,
            },
        }
