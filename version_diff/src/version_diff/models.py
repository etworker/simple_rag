"""结果数据模型"""

from dataclasses import dataclass, field


@dataclass
class VersionChange:
    """一处版本变更"""

    change_type: str  # "modified" | "added" | "removed"
    section: str  # 所在章节（新版）
    location: str  # 位置描述（新版，如"第3页 / §2.2"）
    old_section: str = ""  # 所在章节（旧版）
    old_location: str = ""  # 位置描述（旧版，如"第2页 / §2.1"）
    old_text: str = ""  # 旧版内容（modified/removed 时有值）
    new_text: str = ""  # 新版内容（modified/added 时有值）
    summary: str = ""  # 变更摘要（如"备份频率从每4小时改为每2小时"）
    similarity: float = 0.0  # 段落相似度（modified 时有值）
    category: str = "content"  # "content" | "tracking_table" | "metadata"
    # 表格差异的结构化定位；普通段落变更保持默认空值。
    table_name: str = ""
    row_key: str = ""
    row_index: int = 0
    cell_changes: list[dict] = field(default_factory=list)


@dataclass
class VersionDiffResult:
    """版本对比结果"""

    changes: list[VersionChange] = field(default_factory=list)
    minor_changes: list = field(default_factory=list)  # 被过滤的细微变更（跟踪表/修订日期）
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
    suspects: list[Inconsistency] = field(default_factory=list)  # 疑似不一致（confidence=low）
    total_candidates: int = 0  # 跨文档候选对数
    rule_filtered: int = 0  # 规则预过滤数
    llm_judged: int = 0  # LLM 判断数
    dedup_count: int = 0  # 去重合并的数量

    @property
    def is_safe(self) -> bool:
        """是否可以安全入库（无矛盾）"""
        return len(self.inconsistencies) == 0

    def report(self) -> str:
        """生成 Markdown 报告"""
        if self.is_safe:
            if self.suspects:
                return f"## ✅ 预审核通过\n\n未发现确定性矛盾，但有 {len(self.suspects)} 处疑似不一致需人工复核。"
            return "## ✅ 预审核通过\n\n未发现与已有文档的矛盾。"

        lines = [f"## ⚠️ 发现 {len(self.inconsistencies)} 处文档间不一致"]
        if self.dedup_count:
            lines.append(f"（已自动合并 {self.dedup_count} 处重复）")
        lines.append("")
        for i, inc in enumerate(self.inconsistencies, 1):
            lines.append(f"### 不一致 #{i}: {inc.point}\n")
            lines.append("| | 描述 |")
            lines.append("|---|------|")
            lines.append(f"| **{inc.doc_a_file}** {inc.doc_a_location} | {inc.doc_a_says} |")
            lines.append(f"| **{inc.doc_b_file}** {inc.doc_b_location} | {inc.doc_b_says} |")
            lines.append("")
        if self.suspects:
            lines.append(f"---\n\n### ⚠️ 疑似不一致（{len(self.suspects)} 处，需人工复核）\n")
            for i, inc in enumerate(self.suspects, 1):
                lines.append(f"{i}. **{inc.point}**: {inc.doc_a_says} ↔ {inc.doc_b_says}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """JSON 序列化"""
        return {
            "is_safe": self.is_safe,
            "inconsistency_count": len(self.inconsistencies),
            "suspect_count": len(self.suspects),
            "dedup_count": self.dedup_count,
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
                "dedup_count": self.dedup_count,
            },
        }
