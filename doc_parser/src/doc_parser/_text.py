"""文本分段与章节识别（PDF / DOCX 共享）"""

import os
import re

from doc_parser.models import Paragraph

# 列表项特征：以动作动词开头（后面通常跟长描述）
_LIST_VERB_PREFIXES = (
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
_LIST_END_PUNCT = "；，。、；,.；"

# 单纯数字编号的正则（用于在 _detect_chapter 中识别并施加更严格的过滤）
_SINGLE_NUMBER_RE = re.compile(r"^\d+\s+(.+)")

# 中文编号正则（用于判断是否为中文数字编号模式，施加标题终止符截断）
_CHINESE_NUM_RE = re.compile(r"^（?[一二三四五六七八九十]+[、）)]")

# 标题终止符：标题后紧跟正文时，标题通常以这些符号结尾
# 遇到这些符号且后面还有内容时，只取终止符前的部分作为标题
_HEADING_TERMINATORS = ("．", "。", "：", ":")


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
                for term in _HEADING_TERMINATORS:
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
            if title.count("、") + title.count("，") > 2:
                return None
            # 标题以列表项标点结尾 → 列表项，非章节标题
            if title[-1] in _LIST_END_PUNCT:
                return None
            # 标题以动作动词开头且较长 → 列表项描述，非章节标题
            if len(title) > 12 and title.startswith(_LIST_VERB_PREFIXES):
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

        # 正文行 → 先累积到 current_lines
        current_lines.append(stripped)
        current_text = " ".join(current_lines)

        # 句末终止符 + 段落已有一定长度 → 断开
        if (
            stripped
            and stripped[-1] in "。；！？.;!?"
            and len(current_text) >= cfg.get("sentence_break_min_length", 40)
        ) or (len(current_text) > max_len and stripped and stripped[-1] in "。.;；，,"):
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
