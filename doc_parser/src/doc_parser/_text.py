"""文本分段与章节识别（PDF / DOCX 共享）"""

import os
import re

from doc_parser.models import Paragraph

# 以下常量为内置默认值，均可通过 cfg 同名键覆盖，
# 便于客户定制化场景通过 web 配置注入。

# 列表项特征：以动作动词开头（后面通常跟长描述）
_DEFAULT_LIST_VERB_PREFIXES = (
    "负责",
    "建立",
    "整合",
    "加强",
    "完成",
    "管理与",
    "参与",
    "组织",
    "规划",
    "做好",
    "开展",
    "制定",
    "依据",
    "按照",
    "定期",
    "管理维护",
    "采集",
    "编制",
    "审批",
    "评估",
)

# 列表项结尾标点（真实章节标题不会以这些结尾）
_DEFAULT_LIST_END_PUNCT = "；，。、；,.；"

# 单纯数字编号的正则（用于在 _detect_chapter 中识别并施加更严格的过滤）
_SINGLE_NUMBER_RE = re.compile(r"^\d+\s+(.+)")

# 数字编号后紧跟的量词/统计单位前缀：
# 正文统计数据（"3 万人次"/"6 月份"/"28. 55 万小时"/"10 万架次"）以数字开头，
# 但紧跟量词单位，不是章节标题；而真实章节标题（"2 职责分工"/"3 网络管理"）
# 数字后紧跟的是名词性内容。
# 命中这些前缀 → 视为统计数字正文，非章节标题。
_NUM_UNIT_PREFIXES = (
    "万人次", "人月", "人年", "万小时", "小时", "万架次", "架次", "万公里", "公里",
    "万米", "万吨", "吨", "亿元", "万元", "千元", "元", "万方", "立方米", "平米",
    "平方米", "平方公里", "个月", "月份", "月", "年度", "季度", "天", "日", "号",
    "人次", "次", "个", "名", "家", "岁", "分", "秒", "‰", "%", "％", "‰",
)

# 中文编号正则（用于判断是否为中文数字编号模式，施加标题终止符截断）
_CHINESE_NUM_RE = re.compile(r"^（?[一二三四五六七八九十]+[、）)]")

# 标题终止符：标题后紧跟正文时，标题通常以这些符号结尾
# 遇到这些符号且后面还有内容时，只取终止符前的部分作为标题
_DEFAULT_HEADING_TERMINATORS = ("．", "。", "：", ":")


# 句末终止符集合（用于行内标题粘连检测）
_DEFAULT_SENTENCE_END_CHARS = "。；！？.;!？"


def normalize_number_spacing(text, cfg=None):
    """修复 PDF 文本层数字断字。

    部分 PDF 将数字按字符拆成独立 text run（字距与词距无区分），
    文本后端拼接后出现 "28. 55 万小时"、"0. 18%"、"2632. 3 万人次"。
    本函数按确定性模式把小数点后的空格去掉：
      - "28. 55"   → "28.55"   （小数，点前有数字）
      - "0. 18%"   → "0.18%"
      - "2632. 3"  → "2632.3"
    """
    if not text:
        return text
    out = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", text)
    # "N N. 万/亿..."（如 "57 10. 万架次" → "57.10 万架次"、"26 63. 万吨" → "26.63 万吨"）
    out = re.sub(r"(\d+)\s+(\d+)\.(?=\s*(?:万|亿|％|%|吨|米|公里))", r"\1.\2", out)
    return out


def _try_split_inline_title(line, cfg):
    """检测行内标题粘连，返回 (body, title) 或 None。

    当一行文本中句末终止符之后紧跟章节标题模式时，拆分为正文 + 标题。
    例如:
      "…考核合格分数为85分。 第六章 信息安全管理"
      → ("…考核合格分数为85分。", "第六章 信息安全管理")
      "…提交《变更完成报告》。 第三章 网络与基础设施管理"
      → ("…提交《变更完成报告》。", "第三章 网络与基础设施管理")

    要求:
    - 终止符后至少 4 个字符（避免误拆编号引用如 "参见 1.1"）
    - 终止符后的部分必须被 detect_chapter 识别为章节标题
    - 正文部分至少 10 个字符（避免短行误拆）
    """
    sentence_end_chars = cfg.get("sentence_end_chars", _DEFAULT_SENTENCE_END_CHARS)
    min_remainder = cfg.get("inline_title_min_remainder", 4)
    min_body = cfg.get("inline_title_min_body", 10)
    for i, ch in enumerate(line):
        if ch not in sentence_end_chars:
            continue
        remainder = line[i + 1 :].strip()
        if len(remainder) < min_remainder:
            continue
        if detect_chapter(remainder, cfg):
            body = line[:i + 1].strip()
            if len(body) >= min_body:
                return (body, remainder)
    return None


def detect_chapter(text, cfg):
    """识别章节标题

    过滤规则（避免表格行/目录条目/正文碎片/列表项被误识别为标题）：
    - 标题长度不超过 max_chapter_title_length
    - 标题部分长度 >= 2（排除 "6 R" 之类的单字符）
    - 标题不含日期模式（排除 "3 2025-12-03 ..." 之类的表格行）
    - 标题不含过多列表分隔符（排除 "0.2-1、0.4-1、0.4-6、 ..." 之类的内容）
    - 标题不以列表项标点结尾（排除 "...负责...工作；" 之类的列表项）
    - 标题不以动作动词开头且过长（排除 "负责信息系统建设..." 之类的列表项）
    - 单纯数字编号 ^\\d+ 有额外限制：标题长度和编号大小（见配置项）
    - 中文编号标题含终止符时截断标题（处理"标题+正文同行"的情况）
    """
    text = text.strip()
    if not text or len(text) > cfg.get("max_chapter_title_length", 80):
        return None
    for pattern in cfg["chapter_patterns"]:
        m = re.match(pattern, text)
        if m:
            chapter = m.group(1)
            title = m.group(2).strip()
            # 中文编号：标题含终止符时截断（处理 "（一）恶劣天气运行风险． 7-8 月..." 的情况）
            if _CHINESE_NUM_RE.match(text):
                for term in cfg.get("heading_terminators", _DEFAULT_HEADING_TERMINATORS):
                    idx = title.find(term)
                    if 0 < idx < len(title) - 1:
                        title = title[:idx].strip()
                        break
            # 标题太短 → 可能是表格数字
            if len(title) < 2:
                return None
            # 标题含日期 → 可能是表格行
            if re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", title):
                return None
            # 标题含过多列表分隔符 → 可能是正文碎片
            list_sep_limit = cfg.get("list_separator_limit", 2)
            if title.count("、") + title.count("，") > list_sep_limit:
                return None
            # 标题以列表项标点结尾 → 列表项，非章节标题
            list_end_punct = cfg.get("list_end_punct", _DEFAULT_LIST_END_PUNCT)
            if title[-1] in list_end_punct:
                return None
            # 标题以动作动词开头且较长 → 列表项描述，非章节标题
            verb_prefixes = cfg.get("list_verb_prefixes", _DEFAULT_LIST_VERB_PREFIXES)
            verb_min_title_len = cfg.get("list_verb_min_title_length", 12)
            if len(title) > verb_min_title_len and title.startswith(tuple(verb_prefixes)):
                return None
            # 单纯数字编号 ^\d+ 的额外过滤：
            # - 编号值过大（如 2026）→ 可能是年份，非章节号
            # - 标题过长 → 可能是正文行误匹配（如 "6 月份，管理局通过..."）
            if _SINGLE_NUMBER_RE.match(text) and not re.match(r"^\d+\.\d+", text):
                try:
                    num_val = int(chapter)
                except ValueError:
                    num_val = 0
                if num_val > cfg.get("single_number_max_value", 999):
                    return None
                if len(title) > cfg.get("single_number_title_max_length", 20):
                    return None
                # 数字紧跟量词/统计单位（"3 万人次"/"6 月份"）→ 正文统计数据，非章节标题
                if title.startswith(cfg.get("num_unit_prefixes", _NUM_UNIT_PREFIXES)):
                    return None
            return (chapter, title)
    return None


def find_page(offset, page_boundaries):
    """根据字符偏移反查页码"""
    for start, end, page_num in page_boundaries:
        if start <= offset < end:
            return page_num
    return page_boundaries[-1][2] if page_boundaries else 0


def split_stream(full_text, cfg):
    """
    在流式全文上按语义分段

    分割信号（优先级从高到低）：
    1. 章节标题行前断开
    1b. 行内标题粘连：正文句号/分号后紧跟章节标题 → 拆分
    2. 连续空行
    3. 句末终止符 + 换行（段落已达一定长度）
    4. 长度上限强制断开
    """
    max_len = cfg["max_paragraph_length"]
    noise_line_re = [re.compile(p) for p in cfg.get("noise_line_patterns", [])]
    paragraphs = []
    lines = full_text.split("\n")
    current_lines = []
    current_start = 0
    char_pos = 0

    for line in lines:
        line_start = char_pos
        char_pos += len(line) + 1  # +1 for \n
        stripped = line.strip()

        # 空行 → 断开
        if not stripped:
            if current_lines:
                para_text = " ".join(current_lines)
                if len(para_text.strip()) > 0:
                    paragraphs.append((para_text, current_start, line_start))
                current_lines = []
            current_start = char_pos
            continue

        # 元数据行剥离：整行匹配配置的版本管理元数据模式 → 跳过（不进正文段落）
        # 避免"修订日期：2026-05-08"这类独立行被并入相邻正文造成句子拼接。
        if noise_line_re and any(p.match(stripped) for p in noise_line_re):
            if current_lines:
                para_text = " ".join(current_lines)
                if len(para_text.strip()) > 0:
                    paragraphs.append((para_text, current_start, line_start))
                current_lines = []
            current_start = char_pos
            continue

        # 章节标题 → 独占一段（先断开前面的，标题自身也单独成段）
        if detect_chapter(stripped, cfg):
            if current_lines:
                para_text = " ".join(current_lines)
                if len(para_text.strip()) > 0:
                    paragraphs.append((para_text, current_start, line_start))
                current_lines = []
            # 标题独占一段
            paragraphs.append((stripped, line_start, char_pos))
            current_start = char_pos
            continue

        # ★ 行内标题粘连检测：正文 + 句末终止符 + 章节标题 → 拆分
        # 例: "…考核合格分数为85分。 第六章 信息安全管理"
        #     → 正文 "…考核合格分数为85分。" + 标题 "第六章 信息安全管理"
        split_result = _try_split_inline_title(stripped, cfg)
        if split_result:
            body_text, title_text = split_result
            # 先追加正文部分到 current_lines 并断开
            if body_text:
                current_lines.append(body_text)
                para_text = " ".join(current_lines)
                if len(para_text.strip()) > 0:
                    paragraphs.append((para_text, current_start, char_pos))
                current_lines = []
            # 标题独占一段
            paragraphs.append((title_text, line_start, char_pos))
            current_start = char_pos
            continue

        # 正文行 → 先累积到 current_lines
        current_lines.append(stripped)
        current_text = " ".join(current_lines)

        # 句末终止符 + 段落已有一定长度 → 断开
        sentence_end_chars = cfg.get("sentence_end_chars", _DEFAULT_SENTENCE_END_CHARS)
        soft_break_chars = cfg.get("soft_break_chars", "。.;；，,")
        if (
            stripped
            and stripped[-1] in sentence_end_chars
            and len(current_text) >= cfg.get("sentence_break_min_length", 40)
        ) or (len(current_text) > max_len and stripped and stripped[-1] in soft_break_chars):
            paragraphs.append((current_text, current_start, char_pos))
            current_lines = []
            current_start = char_pos

    # 尾部
    if current_lines:
        para_text = " ".join(current_lines)
        if len(para_text.strip()) > 0:
            paragraphs.append((para_text, current_start, char_pos))

    return paragraphs


def segment_and_locate(full_text, page_boundaries, filepath, cfg):
    """在正文流上按语义分段，标注页码和章节"""
    if not full_text.strip():
        return []

    min_len = cfg["min_paragraph_length"]
    noise_patterns = [re.compile(p) for p in cfg.get("noise_patterns", [])]

    # 分段
    raw_paras = split_stream(full_text, cfg)

    # 组装 Paragraph 对象
    paragraphs = []
    current_chapter = ""
    current_chapter_title = ""

    for para_text, char_start, char_end in raw_paras:
        para_text = para_text.strip()

        # 章节检测（先检测，标题段免长度过滤）
        ch = detect_chapter(para_text, cfg)

        # 长度过滤（章节标题段免过滤，因为 "2 日常管理" 等标题较短）
        if not ch and len(para_text) < min_len:
            continue

        # 噪声正则过滤
        if any(p.match(para_text) for p in noise_patterns):
            continue

        if ch:
            current_chapter, current_chapter_title = ch

        # 反查页码
        page_start = find_page(char_start, page_boundaries)
        page_end = find_page(max(char_end - 1, char_start), page_boundaries)

        paragraphs.append(
            Paragraph(
                text=para_text,
                page=page_start,
                page_end=page_end if page_end != page_start else 0,
                chapter=current_chapter,
                chapter_title=current_chapter_title,
                source_file=os.path.basename(filepath),
                index=len(paragraphs) + 1,
            )
        )

    return paragraphs
