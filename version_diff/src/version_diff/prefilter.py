"""
规则预分类器 + 差异片段标注器

两个职责：
1. 对明显的非实质差异直接分类（减少 LLM 调用）
2. 对需要 LLM 判断的差异，标注每个 diff 片段的结构性质
   （让 LLM 知道 "2.3→4.5" 是章节引用号还是业务数值）

通用设计原则：
- 基于文本结构模式，不引用任何特定文档内容
- 宁可漏判（交 LLM），不可误判（错误过滤实质差异）
"""

import re
from dataclasses import dataclass


@dataclass
class PreClassifyResult:
    """预分类结果"""

    category: str | None  # None = 不确定，交给 LLM
    confidence: float
    reason: str


@dataclass
class AnnotatedFragment:
    """标注后的差异片段"""

    ftype: str  # replace/delete/insert
    old_text: str
    new_text: str
    annotation: str  # 标注：section_ref / numeric_value / date / text_content / mixed


# ============================================================
# 差异片段标注（给 LLM 提供结构信息）
# ============================================================

# 章节引用号模式（如 2.3, 4.1.2, 0.7-1, §3.2）
_SECTION_REF = re.compile(
    r"^[\d]+(?:[.\-]\d+){0,3}$"  # 纯数字+点/横杠组合：1, 1.2, 1.2.3, 0.4-1
)

# 带单位的业务数值（如 30天, 15分钟, 99.5%, 200万元）
_VALUE_WITH_UNIT = re.compile(
    r"\d+\.?\d*\s*(?:天|日|月|年|分钟|小时|秒|次|%|元|万|人|台|个|项|条|页|周)"
)

# 日期格式
_DATE_PATTERN = re.compile(
    r"\d{4}[.\-/]\d{1,2}[.\-/]?\d{0,2}|"
    r"\d{4}\s*\u5e74\s*\d{1,2}\s*\u6708"
)

# 版本号格式
_VERSION_PATTERN = re.compile(
    r"^[RVv]?\d+[.\-]\d+$|"  # R3-1, V2.0, v1.1
    r"^\u7b2c?\d+\u7248$"  # 第3版
)

# 管理信息上下文关键词
_MGMT_CONTEXT_WORDS = {
    "修订",
    "版次",
    "版本",
    "生效",
    "批准",
    "发布",
    "页码",
    "页号",
    "受控",
    "编号",
    "手册编号",
    "文件编号",
    "下载时间",
    "打印",
}

# 封面/有效页/修订记录特征
_COVER_PAGE_WORDS = {
    "受控状态",
    "版次",
    "手册编号",
    "文件编号",
    "发布日期",
    "下载时间",
    "打印",
    "有效",
    "修订日期",
    "修订次数",
    "批准人",
}

# 有效页清单特征
_VALID_PAGE_WORDS = {"有效页", "状态符号", "修订次数", "页号", "页码"}

# 修订记录表特征
_REVISION_TABLE_WORDS = {"修订版次", "修订日期", "修订原因", "修订人", "修订记录"}

# 层级名称差异模式（consistency模式下，这些差异是天然存在的）
_HIERARCHY_PATTERNS = [
    (re.compile(r"管理手册|管理"), re.compile(r"工作手册|部工作|作业指导")),
    (re.compile(r"分管领导|副总"), re.compile(r"总经理|部门经理|部长")),
]

# 章节引用上下文（出现这些词时旁边的数字更可能是章节号）
_SECTION_CONTEXT_WORDS = {
    "见",
    "参见",
    "详见",
    "参照",
    "按照",
    "根据",
    "章",
    "节",
    "条",
    "款",
    "手册",
    "附录",
    "程序",
}


def annotate_fragments(diff_item) -> list[AnnotatedFragment]:
    """
    对差异片段进行结构标注

    返回标注后的片段列表，用于：
    1. 规则预分类的判断依据
    2. 传给 LLM 时的上下文提示（让 LLM 知道数字的性质）
    """
    results = []
    text_a = diff_item.para_a.text
    text_b = diff_item.para_b.text
    combined_context = text_a + " " + text_b

    for ftype, old_frag, new_frag in diff_item.diff_fragments:
        annotation = _classify_fragment(old_frag, new_frag, combined_context)
        results.append(
            AnnotatedFragment(
                ftype=ftype, old_text=old_frag, new_text=new_frag, annotation=annotation
            )
        )

    return results


def _classify_fragment(old_text: str, new_text: str, context: str) -> str:
    """
    判断一个差异片段的性质

    Returns: section_ref / numeric_value / date / version / mgmt_info / text_content / mixed
    """
    old_clean = old_text.strip()
    new_clean = new_text.strip()

    # 两边都是章节引用号格式
    if _is_section_ref(old_clean) and _is_section_ref(new_clean):
        # 进一步看上下文是否有引用提示词
        if _has_section_context(context, old_clean, new_clean):
            return "section_ref"
        # 即使没有提示词，纯数字.数字格式大概率是章节号
        return "section_ref"

    # 一边是章节号格式（另一边为空=插入/删除的情况）
    if (old_clean and _is_section_ref(old_clean) and not new_clean) or (
        new_clean and _is_section_ref(new_clean) and not old_clean
    ):
        return "section_ref"

    # 版本号
    if _VERSION_PATTERN.match(old_clean) or _VERSION_PATTERN.match(new_clean):
        return "version"

    # 日期
    if _DATE_PATTERN.search(old_clean) or _DATE_PATTERN.search(new_clean):
        return "date"

    # 带单位的业务数值
    if _VALUE_WITH_UNIT.search(old_clean) or _VALUE_WITH_UNIT.search(new_clean):
        return "numeric_value"

    # 管理信息上下文（且差异内容较短且含日期/版本格式）
    if (
        any(w in context for w in _MGMT_CONTEXT_WORDS)
        and len(old_clean) + len(new_clean) < 30
        and (
            _DATE_PATTERN.search(old_clean + new_clean)
            or _VERSION_PATTERN.search(old_clean + new_clean)
        )
    ):
        return "mgmt_info"

    # 纯数字（无单位）——可能是章节号也可能是数量，标为 ambiguous_number
    if (
        old_clean.replace(".", "").replace("-", "").isdigit()
        and new_clean.replace(".", "").replace("-", "").isdigit()
    ):
        return "ambiguous_number"

    # 默认：文本内容差异
    return "text_content"


def _is_section_ref(text: str) -> bool:
    """判断是否为章节引用号格式"""
    if not text:
        return False
    return bool(_SECTION_REF.match(text))


def _has_section_context(context: str, old_num: str, new_num: str) -> bool:
    """检查上下文中是否有章节引用提示"""
    # 在上下文中找这些数字附近是否有"见""章""条"等词
    for word in _SECTION_CONTEXT_WORDS:
        if word in context:
            return True
    # 检查数字是否在段落开头作为编号（如 "2.1.3 适用范围"）
    if re.search(rf"^{re.escape(old_num)}\s+\S", context, re.MULTILINE):
        return True
    return bool(re.search(rf"^{re.escape(new_num)}\s+\S", context, re.MULTILINE))


# ============================================================
# 预分类规则（规则表驱动）
# ============================================================

# 每条规则接收 (text_a, text_b, annotated, fragments)，
# 命中则返回 PreClassifyResult，否则返回 None（继续下一条规则）。
# 列表顺序即判定优先级：靠前的规则先判定。


def _rule_all_section_ref(text_a, text_b, annotated, fragments):
    """规则1：所有差异片段都是 section_ref → numbering"""
    if annotated and all(a.annotation == "section_ref" for a in annotated):
        return PreClassifyResult(
            category="numbering", confidence=0.9,
            reason="所有差异均为章节编号/引用号变化",
        )
    return None


def _rule_mostly_section_ref(text_a, text_b, annotated, fragments):
    """规则2：绝大部分(>80%)是 section_ref/ambiguous_number → numbering"""
    if not annotated:
        return None
    ref_count = sum(
        1 for a in annotated if a.annotation in ("section_ref", "ambiguous_number")
    )
    if ref_count >= len(annotated) * 0.8 and len(annotated) >= 2:
        # 进一步验证：去掉所有 X.Y.Z 格式的数字后，剩余文本是否高度相似
        stripped_a = re.sub(r"\d+(?:\.\d+)*(?:\-\d+)?", "", text_a)
        stripped_b = re.sub(r"\d+(?:\.\d+)*(?:\-\d+)?", "", text_b)
        import difflib

        text_sim = difflib.SequenceMatcher(None, stripped_a, stripped_b).ratio()
        if text_sim > 0.85:
            return PreClassifyResult(
                category="numbering", confidence=0.85,
                reason=f"差异 {ref_count}/{len(annotated)} 为编号类，去掉数字后文本相似度 {text_sim:.0%}",
            )
    return None


def _rule_mgmt_context(text_a, text_b, annotated, fragments):
    """规则3：段落含大量管理信息关键词且差异均为日期/版本/编号类 → metadata"""
    mgmt_keyword_count = sum(1 for w in _MGMT_CONTEXT_WORDS if w in (text_a + text_b))
    if mgmt_keyword_count >= 3 and annotated and all(
        a.annotation in ("date", "version", "section_ref", "mgmt_info", "ambiguous_number")
        for a in annotated
    ):
        return PreClassifyResult(
            category="metadata", confidence=0.9,
            reason=f"段落含{mgmt_keyword_count}个管理信息关键词，差异均为日期/版本/编号类",
        )
    return None


def _rule_low_diff_punct(text_a, text_b, annotated, fragments):
    """规则4：差异率极低且仅标点/格式 → wording"""
    total_diff = sum(len(f[1]) + len(f[2]) for f in fragments)
    total_text = max(len(text_a), len(text_b), 1)
    if total_diff / total_text < 0.02:
        punct = set('，。；：！？、,.:;!? \t\n（）()【】[]「」""《》<>—─-·•…')
        all_punct = all(
            all(c in punct for c in (f[1] + f[2]).replace(" ", ""))
            for f in fragments if f[1] or f[2]
        )
        if all_punct:
            return PreClassifyResult(
                category="wording", confidence=0.9, reason="差异仅为标点/格式变化"
            )
    return None


def _rule_cover_page(text_a, text_b, annotated, fragments):
    """规则5：封面页/受控页区域差异 → metadata"""
    cover_words_found = sum(1 for w in _COVER_PAGE_WORDS if w in (text_a + text_b))
    if cover_words_found >= 3 and len(text_a) < 600 and len(text_b) < 600:
        return PreClassifyResult(
            category="metadata", confidence=0.92,
            reason=f"封面/受控页区域（含{cover_words_found}个封面关键词），差异为管理信息",
        )
    return None


def _rule_valid_page(text_a, text_b, annotated, fragments):
    """规则6：有效页清单区域 → structural"""
    valid_page_found = sum(1 for w in _VALID_PAGE_WORDS if w in (text_a + text_b))
    if valid_page_found >= 2:
        return PreClassifyResult(
            category="structural", confidence=0.9,
            reason="有效页清单区域，差异为文档结构管理信息",
        )
    return None


def _rule_revision_table(text_a, text_b, annotated, fragments):
    """规则7：修订记录区域 → metadata"""
    revision_found = sum(1 for w in _REVISION_TABLE_WORDS if w in (text_a + text_b))
    if revision_found >= 2:
        return PreClassifyResult(
            category="metadata", confidence=0.92,
            reason="修订记录区域，差异为版本管理信息",
        )
    return None


def _rule_section_plus_hierarchy(text_a, text_b, annotated, fragments):
    """规则8a：章节引用号 + 少量层级名称差异 → numbering"""
    if not annotated or len(annotated) < 2:
        return None
    section_frags = sum(
        1 for a in annotated if a.annotation in ("section_ref", "ambiguous_number")
    )
    hier_frags = 0
    for a in annotated:
        if a.annotation == "text_content":
            combined = a.old_text + a.new_text
            if any(kw in combined for kw in ("手册", "管理", "工作", "部")):
                hier_frags += 1
    if (section_frags + hier_frags) >= len(annotated) * 0.8 and section_frags >= 2:
        return PreClassifyResult(
            category="numbering", confidence=0.82,
            reason=f"章节引用号{section_frags}处 + 层级名称差异{hier_frags}处，非实质变更",
        )
    return None


def _rule_hierarchy_names(text_a, text_b, annotated, fragments):
    """规则8：层级名称差异（管理手册↔工作手册等） → wording"""
    if not annotated:
        return None
    text_frags = [a for a in annotated if a.annotation == "text_content"]
    if not text_frags or len(text_frags) > 5:
        return None
    all_hierarchy = True
    for frag in text_frags:
        is_hier = False
        for pat_a, pat_b in _HIERARCHY_PATTERNS:
            if (
                pat_a.search(frag.old_text) and pat_b.search(frag.new_text)
            ) or (pat_b.search(frag.old_text) and pat_a.search(frag.new_text)):
                is_hier = True
                break
        # 短文本片段（<6字）中仅含手册/部门名的差异也视为层级差异
        if (
            not is_hier
            and len(frag.old_text.strip()) < 6
            and len(frag.new_text.strip()) < 6
        ):
            combined = frag.old_text + frag.new_text
            if any(kw in combined for kw in ("手册", "部", "管理", "工作", "领导", "经理")):
                is_hier = True
        if not is_hier:
            all_hierarchy = False
            break
    if all_hierarchy:
        return PreClassifyResult(
            category="wording", confidence=0.85,
            reason="层级名称差异（管理手册↔工作手册/分管领导↔总经理），非实质变更",
        )
    return None


# 规则优先级：靠前的规则先判定
_PRECLASSIFY_RULES = [
    _rule_all_section_ref,
    _rule_mostly_section_ref,
    _rule_mgmt_context,
    _rule_low_diff_punct,
    _rule_cover_page,
    _rule_valid_page,
    _rule_revision_table,
    _rule_section_plus_hierarchy,
    _rule_hierarchy_names,
]


def pre_classify(diff_item) -> PreClassifyResult:
    """
    基于规则的快速预分类（规则表驱动）

    只在高置信度场景下给出判断，否则返回 None（交 LLM）
    """
    fragments = diff_item.diff_fragments
    text_a = diff_item.para_a.text
    text_b = diff_item.para_b.text
    annotated = annotate_fragments(diff_item)

    for rule in _PRECLASSIFY_RULES:
        result = rule(text_a, text_b, annotated, fragments)
        if result is not None:
            return result

    # 不确定，交 LLM
    return PreClassifyResult(category=None, confidence=0, reason="")


# ============================================================
# 差异描述增强（给 LLM 提供标注后的 context）
# ============================================================


# ============================================================
# 批量预分类
# ============================================================


def batch_pre_classify(diff_items):
    """
    批量预分类

    Returns:
        (classified: list[(item, category, reason)],
         uncertain: list[item])
    """
    classified = []
    uncertain = []

    for item in diff_items:
        result = pre_classify(item)
        if result.category:
            classified.append((item, result.category, result.reason))
        else:
            uncertain.append(item)

    return classified, uncertain
