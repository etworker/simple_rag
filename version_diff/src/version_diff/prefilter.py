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
_VALUE_WITH_UNIT = re.compile(r"\d+\.?\d*\s*(?:天|日|月|年|分钟|小时|秒|次|%|元|万|人|台|个|项|条|页|周)")

# 日期格式
_DATE_PATTERN = re.compile(
    r"\d{4}[.\-/]\d{1,2}[.\-/]?\d{0,2}|"
    r"\d{4}\s*\u5e74\s*\d{1,2}\s*\u6708|"
    r"[零〇一二三四五六七八九十百千万两]+\s*\u5e74\s*"
    r"[零〇一二三四五六七八九十百千万两]+\s*\u6708(?:\s*[零〇一二三四五六七八九十百千万两]+\s*[日号])?"
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

_DOCUMENT_DATE_CONTEXT_WORDS = ("声明", "检查", "分管领导", "签署", "批准", "生效", "发布")


def _rule_document_date(text_a, text_b, annotated, fragments):
    """规则：声明/批准等文档元数据中的日期变化不属于内容矛盾。"""
    annotations = [a.annotation for a in annotated]
    date_in_text = _DATE_PATTERN.search(text_a) and _DATE_PATTERN.search(text_b)
    same_without_date = _DATE_PATTERN.sub("<DATE>", text_a) == _DATE_PATTERN.sub("<DATE>", text_b)
    if (
        date_in_text
        and same_without_date
        and any(word in text_a + text_b for word in _DOCUMENT_DATE_CONTEXT_WORDS)
    ):
        return PreClassifyResult(category="metadata", confidence=0.97, reason="文档声明/批准等元数据日期变化")
    if (
        annotated
        and "date" in annotations
        and all(a in ("date", "version", "section_ref", "mgmt_info", "ambiguous_number") for a in annotations)
        and any(word in text_a + text_b for word in _DOCUMENT_DATE_CONTEXT_WORDS)
    ):
        return PreClassifyResult(category="metadata", confidence=0.97, reason="文档声明/批准等元数据日期变化")
    return None


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
        results.append(AnnotatedFragment(ftype=ftype, old_text=old_frag, new_text=new_frag, annotation=annotation))

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
        and (_DATE_PATTERN.search(old_clean + new_clean) or _VERSION_PATTERN.search(old_clean + new_clean))
    ):
        return "mgmt_info"

    # 纯数字（无单位）——可能是章节号也可能是数量，标为 ambiguous_number
    if old_clean.replace(".", "").replace("-", "").isdigit() and new_clean.replace(".", "").replace("-", "").isdigit():
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
            category="numbering",
            confidence=0.9,
            reason="所有差异均为章节编号/引用号变化",
        )
    return None


def _rule_mostly_section_ref(text_a, text_b, annotated, fragments):
    """规则2：绝大部分(>80%)是 section_ref/ambiguous_number → numbering"""
    if not annotated:
        return None
    ref_count = sum(1 for a in annotated if a.annotation in ("section_ref", "ambiguous_number"))
    if ref_count >= len(annotated) * 0.8 and len(annotated) >= 2:
        # 进一步验证：去掉所有 X.Y.Z 格式的数字后，剩余文本是否高度相似
        stripped_a = re.sub(r"\d+(?:\.\d+)*(?:\-\d+)?", "", text_a)
        stripped_b = re.sub(r"\d+(?:\.\d+)*(?:\-\d+)?", "", text_b)
        import difflib

        text_sim = difflib.SequenceMatcher(None, stripped_a, stripped_b).ratio()
        if text_sim > 0.85:
            return PreClassifyResult(
                category="numbering",
                confidence=0.85,
                reason=f"差异 {ref_count}/{len(annotated)} 为编号类，去掉数字后文本相似度 {text_sim:.0%}",
            )
    return None


def _rule_mgmt_context(text_a, text_b, annotated, fragments):
    """规则3：段落含大量管理信息关键词且差异均为日期/版本/编号类 → metadata"""
    mgmt_keyword_count = sum(1 for w in _MGMT_CONTEXT_WORDS if w in (text_a + text_b))
    if (
        mgmt_keyword_count >= 3
        and annotated
        and all(a.annotation in ("date", "version", "section_ref", "mgmt_info", "ambiguous_number") for a in annotated)
    ):
        return PreClassifyResult(
            category="metadata",
            confidence=0.9,
            reason=f"段落含{mgmt_keyword_count}个管理信息关键词，差异均为日期/版本/编号类",
        )
    return None


def _rule_low_diff_punct(text_a, text_b, annotated, fragments):
    """规则4：差异率极低且仅标点/格式 → wording"""
    total_diff = sum(len(f[1]) + len(f[2]) for f in fragments)
    total_text = max(len(text_a), len(text_b), 1)
    if total_diff / total_text < 0.02:
        punct = set('，。；：！？、,.:;!? \t\n（）()【】[]「」""《》<>—─-·•…')
        all_punct = all(all(c in punct for c in (f[1] + f[2]).replace(" ", "")) for f in fragments if f[1] or f[2])
        if all_punct:
            return PreClassifyResult(category="wording", confidence=0.9, reason="差异仅为标点/格式变化")
    return None


def _rule_cover_page(text_a, text_b, annotated, fragments):
    """规则5：封面页/受控页区域差异 → metadata"""
    cover_words_found = sum(1 for w in _COVER_PAGE_WORDS if w in (text_a + text_b))
    if cover_words_found >= 3 and len(text_a) < 600 and len(text_b) < 600:
        return PreClassifyResult(
            category="metadata",
            confidence=0.92,
            reason=f"封面/受控页区域（含{cover_words_found}个封面关键词），差异为管理信息",
        )
    return None


def _rule_valid_page(text_a, text_b, annotated, fragments):
    """规则6：有效页清单区域 → structural"""
    valid_page_found = sum(1 for w in _VALID_PAGE_WORDS if w in (text_a + text_b))
    if valid_page_found >= 2:
        return PreClassifyResult(
            category="structural",
            confidence=0.9,
            reason="有效页清单区域，差异为文档结构管理信息",
        )
    return None


def _rule_revision_table(text_a, text_b, annotated, fragments):
    """规则7：修订记录区域 → metadata"""
    revision_found = sum(1 for w in _REVISION_TABLE_WORDS if w in (text_a + text_b))
    if revision_found >= 2:
        return PreClassifyResult(
            category="metadata",
            confidence=0.92,
            reason="修订记录区域，差异为版本管理信息",
        )
    return None


def _rule_section_plus_hierarchy(text_a, text_b, annotated, fragments):
    """规则8a：章节引用号 + 少量层级名称差异 → numbering"""
    if not annotated or len(annotated) < 2:
        return None
    section_frags = sum(1 for a in annotated if a.annotation in ("section_ref", "ambiguous_number"))
    hier_frags = 0
    for a in annotated:
        if a.annotation == "text_content":
            combined = a.old_text + a.new_text
            if any(kw in combined for kw in ("手册", "管理", "工作", "部")):
                hier_frags += 1
    if (section_frags + hier_frags) >= len(annotated) * 0.8 and section_frags >= 2:
        return PreClassifyResult(
            category="numbering",
            confidence=0.82,
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
            if (pat_a.search(frag.old_text) and pat_b.search(frag.new_text)) or (
                pat_b.search(frag.old_text) and pat_a.search(frag.new_text)
            ):
                is_hier = True
                break
        # 短文本片段（<6字）中仅含手册/部门名的差异也视为层级差异
        if not is_hier and len(frag.old_text.strip()) < 6 and len(frag.new_text.strip()) < 6:
            combined = frag.old_text + frag.new_text
            if any(kw in combined for kw in ("手册", "部", "管理", "工作", "领导", "经理")):
                is_hier = True
        if not is_hier:
            all_hierarchy = False
            break
    if all_hierarchy:
        return PreClassifyResult(
            category="wording",
            confidence=0.85,
            reason="层级名称差异（管理手册↔工作手册/分管领导↔总经理），非实质变更",
        )
    return None


_HEADING_PREFIX = re.compile(r"^\s*(?:(?:第\s*)?\d+(?:\.\d+){0,4}\s*)")
_HEADING_NORMATIVE_WORDS = ("应", "必须", "负责", "适用于", "禁止", "不得", "审批", "申请", "权限")


def _heading_core(text: str) -> str:
    """去掉章节编号并规范空白，返回标题主体。"""
    core = re.sub(r"\s+", "", _HEADING_PREFIX.sub("", text or "")).strip(" ：:、．.")
    return re.sub(r"^(?:相关|支持性)", "", core)


def _looks_like_heading(para, text: str) -> bool:
    """识别纯标题/结构标签，不把带规范性内容的短段落误过滤。"""
    block_type = getattr(para, "block_type", "")
    core = _heading_core(text)
    return bool(
        core
        and (block_type == "heading" or bool(_HEADING_PREFIX.match(text or "")))
        and len(core) <= 40
        and not any(word in core for word in _HEADING_NORMATIVE_WORDS)
    )


def _rule_same_structural_heading(text_a, text_b, annotated, fragments):
    """规则：两边只是同名章节/结构标题，章节编号不同不构成矛盾。"""
    if _looks_like_heading(None, text_a) and _looks_like_heading(None, text_b):
        if _heading_core(text_a) == _heading_core(text_b):
            return PreClassifyResult(category="structural", confidence=0.98, reason="同名章节/结构标题，仅编号或层级不同")
    return None


_EQUIVALENT_SCOPE_PHRASES = (
    ("所有员工", "全体员工"),
    ("本规定适用于", "适用于"),
)


def _normalize_safe_equivalents(text: str) -> str:
    """只归一化已确认的制度措辞等价词，不泛化员工/用户等角色范围。"""
    value = re.sub(r"\s+", "", text or "")
    for source, target in _EQUIVALENT_SCOPE_PHRASES:
        value = value.replace(source, target)
    return value


def _rule_normalized_same_content(text_a, text_b, annotated, fragments):
    """规则：仅有标点、空白或解析尾符差异时，不是内容矛盾。"""
    normalize = lambda text: re.sub(r"[\\s，。；：、,.;:（）()【】\\[\\]《》]", "", text)
    if text_a != text_b and normalize(text_a) == normalize(text_b):
        return PreClassifyResult(category="wording", confidence=0.99, reason="正文内容相同，仅标点/格式不同")
    return None


def _rule_safe_equivalent_wording(text_a, text_b, annotated, fragments):
    """规则：严格等价措辞且其余内容完全一致时，不送 LLM。"""
    if text_a != text_b and _normalize_safe_equivalents(text_a) == _normalize_safe_equivalents(text_b):
        return PreClassifyResult(category="wording", confidence=0.98, reason="已确认的同义范围措辞，规范要求未改变")
    return None


_OBJECT_MARKERS = (
    ("email", ("企业邮箱", "电子邮箱", "电子邮件", "邮箱", "邮件")),
    ("storage", ("移动存储设备", "移动存储", "U盘", "USB")),
    ("office_computer", ("办公电脑", "办公计算机")),
    ("vpn", ("VPN",)),
    ("video_conference", ("视频会议系统", "视频会议")),
    ("network", ("办公网络", "有线网络", "互联网", "IP地址")),
)


def _primary_object(text: str) -> str:
    """提取明确的 IT 控制对象；只用于两边对象明确不同的保守过滤。"""
    for name, markers in _OBJECT_MARKERS:
        if any(marker in text for marker in markers):
            return name
    return ""


def _rule_explicitly_different_objects(text_a, text_b, annotated, fragments):
    """规则：明确指向不同系统/设备的段落不是同一事项。"""
    object_a = _primary_object(text_a)
    object_b = _primary_object(text_b)
    if object_a and object_b and object_a != object_b:
        return PreClassifyResult(category="scope", confidence=0.96, reason=f"明确不同控制对象：{object_a} vs {object_b}")
    return None


_ACTION_MARKERS = ("立项", "论证", "监督", "审核", "跟进", "沟通", "保密", "保护", "解释", "培训", "考核", "运行维护", "管理", "维护", "安装", "维修", "防病毒", "保管", "使用")


def _rule_complementary_responsibilities(text_a, text_b, annotated, fragments):
    """规则：同样出现负责，但动作集合完全不同，优先视为职责分工。"""
    if "负责" not in text_a or "负责" not in text_b:
        return None
    actions_a = {word for word in _ACTION_MARKERS if word in text_a}
    actions_b = {word for word in _ACTION_MARKERS if word in text_b}
    if actions_a and actions_b:
        shared_specific = (actions_a & actions_b) - {"管理", "使用"}
        distinct_a = actions_a - {"管理", "使用"}
        distinct_b = actions_b - {"管理", "使用"}
        if not shared_specific and not distinct_a.intersection(distinct_b):
            return PreClassifyResult(category="scope", confidence=0.91, reason="职责动作不重叠，属于不同角色/流程分工")
    return None


def _rule_asymmetric_missing_requirement(text_a, text_b, annotated, fragments):
    """规则：一方新增禁止要求、另一方仅未提及，不构成矛盾。"""
    prohibitions = ("禁止", "不得", "严禁", "不允许")
    permissions = ("允许", "可以", "可自行", "自由", "无需", "必须", "应当", "采用", "设置成", "填写", "使用", "访问", "协助", "需告知")
    has_a = any(word in text_a for word in prohibitions)
    has_b = any(word in text_b for word in prohibitions)
    if has_a == has_b:
        return None
    if "自动获取" in text_a and "自动获取" in text_b:
        return PreClassifyResult(category="scope", confidence=0.9, reason="两边要求相同，单边仅增加禁止私自填写限制")
    other_text = text_b if has_a else text_a
    if any(word in other_text for word in permissions):
        # 若另一边明确给出相反许可/要求，保留给 LLM 判断。
        normalized_a = re.sub(r"配置(?:成|为)|采用|的方式|均|\s+", "", text_a)
        normalized_b = re.sub(r"配置(?:成|为)|采用|的方式|均|\s+", "", text_b)
        prohibition_a = re.sub(r"(?:禁止|不得|严禁|不允许)[^，。；;]*", "", normalized_a)
        prohibition_b = re.sub(r"(?:禁止|不得|严禁|不允许)[^，。；;]*", "", normalized_b)
        if (
            (has_a and prohibition_a and prohibition_a in normalized_b)
            or (has_b and prohibition_b and prohibition_b in normalized_a)
        ):
            return PreClassifyResult(category="scope", confidence=0.9, reason="一方仅新增限制，另一方未给出相反许可")
        return None
    return PreClassifyResult(category="scope", confidence=0.9, reason="一方未提及新增限制，不能据此认定冲突")


def _rule_scope_subset(text_a, text_b, annotated, fragments):
    """规则：适用范围短句是另一段的完整子集/概括，不构成矛盾。"""
    normalized_a = re.sub(r"\s+", "", text_a)
    normalized_b = re.sub(r"\s+", "", text_b)
    if len(normalized_a) >= 8 and len(normalized_b) >= 8 and (
        normalized_a in normalized_b or normalized_b in normalized_a
    ):
        if any(word in text_a + text_b for word in ("适用于", "适用范围", "定义")):
            return PreClassifyResult(category="scope", confidence=0.94, reason="适用范围/定义为概括与细化关系")
    return None


def _rule_document_declaration(text_a, text_b, annotated, fragments):
    """规则：各文档声明执行各自手册，不是两个控制要求互斥。"""
    if all(word in text_a and word in text_b for word in ("严格执行", "本手册规定", "《")):
        return PreClassifyResult(category="reference", confidence=0.95, reason="声明页分别引用各自手册")
    return None


def _rule_definition_subtype(text_a, text_b, annotated, fragments):
    """规则：专门定义与上位概念定义可以同时成立。"""
    if "内网信息系统" in text_a and "信息系统" in text_b and "：" in text_a + text_b:
        return PreClassifyResult(category="scope", confidence=0.94, reason="专门概念与上位概念定义关系")
    if "内网信息系统" in text_b and "信息系统" in text_a and "：" in text_a + text_b:
        return PreClassifyResult(category="scope", confidence=0.94, reason="专门概念与上位概念定义关系")
    return None


def _rule_placeholder_or_reference_heading(text_a, text_b, annotated, fragments):
    """规则：目录/占位引用与正文段落错配不是内容矛盾。"""
    placeholder = re.compile(r"^\s*\d+(?:\.\d+)*x+", re.IGNORECASE)
    if placeholder.match(text_a) or placeholder.match(text_b):
        return PreClassifyResult(category="structural", confidence=0.97, reason="章节占位符/目录引用噪声")
    return None


def _rule_reference_list(text_a, text_b, annotated, fragments):
    """规则：支持性文件章节中的纯引用清单差异不是正文矛盾。"""
    reference_only = re.compile(r"^(?:\s*《[^》]+》\s*(?:[（(][^）)]*[）)])?\s*[、,，;；]?)+$")
    if reference_only.match(text_a) and reference_only.match(text_b):
        return PreClassifyResult(category="reference", confidence=0.96, reason="纯支持性文件/引用清单差异")
    return None


# 规则优先级：靠前的规则先判定
_PRECLASSIFY_RULES = [
    _rule_same_structural_heading,
    _rule_normalized_same_content,
    _rule_safe_equivalent_wording,
    _rule_document_date,
    _rule_document_declaration,
    _rule_explicitly_different_objects,
    _rule_complementary_responsibilities,
    _rule_asymmetric_missing_requirement,
    _rule_scope_subset,
    _rule_definition_subtype,
    _rule_placeholder_or_reference_heading,
    _rule_reference_list,
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
