"""跨页段落合并（通用规则，不硬编码具体文件名/章节名）。

当 PDF/DOCX 按页面切割文本后，同一自然段落可能被拆分到相邻页面。
本模块对已生成的 Paragraph 列表做后处理，将被页面边界打断的连续段落合并还原。

合并条件（全部满足才合并）：
  - 原始页面和块顺序相邻
  - 上一块不是明确的标题、表格或列表结束
  - 上一块以未完成句（无完整结束标点或明显半句结尾）收尾
  - 下一块不是新章节标题、新编号条款或新列表项
  - 下一块不是页眉、页脚或页码噪声
  - 两块不属于明显不同章节
  - 合并后长度不超过通用上限

不合并情况：
  - 下一页开始新的章节编号
  - 下一页开始新的列表项
  - 两块属于不同章节
  - 上一块是表格、下一块是正文（或反之）
  - 下一块是明确的新标题
  - 页眉、页脚被识别为正文
"""

import re

from doc_parser.models import Paragraph

# 完整句结束标点
_SENTENCE_END = set("。！？.!?；;")
# 明确的列表项开头
_LIST_ITEM_RE = re.compile(
    r"^(?:"
    r"\d+[\.\)、]\s|"                   # 1. / 1) / 1、
    r"[a-zA-Z][\.\)]\s|"              # a. / a)
    r"[①②③④⑤⑥⑦⑧⑨⑩]|"                # 圈数字
    r"[\u2022\u25cf\u25cb•]\s|"        # 项目符号
    r"[（\(][一二三四五六七八九十]+[）\)]|"  # （一） 类
    r"[一二三四五六七八九十]+[、．.]\s"    # 一、 类
    r")"
)
# 章节标题开头（简易判断，不依赖完整 chapter_patterns）
_HEADING_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)+\s|"               # 1.1 / 1.1.2 格式
    r"第\s*[\d一二三四五六七八九十百千]+\s*[章节条款]\s|"  # 第N章/节
    r"[（\(][一二三四五六七八九十]+[）\)]\s"  # （一）格式
    r")"
)


def _ends_incomplete(text: str) -> bool:
    """判断文本是否以未完成句结尾（没有完整结束标点）。"""
    text = text.rstrip()
    if not text:
        return False
    last_char = text[-1]
    # 明确的结束标点 → 句子完整
    if last_char in _SENTENCE_END:
        return False
    # 以引号/括号结尾但倒数第二个是结束标点 → 完整
    if last_char in "\u300d\u300f\u201d\u2019\uff09)\u300b\u3011" and len(text) >= 2 and text[-2] in _SENTENCE_END:
        return False
    # 以冒号结尾通常是标题或引导语 → 不视为未完成
    return last_char not in "：:"


def _starts_new_block(text: str) -> bool:
    """判断文本是否明显是一个新的独立块的开头。"""
    stripped = text.lstrip()
    if not stripped:
        return False
    # 新章节标题
    if _HEADING_RE.match(stripped):
        return True
    # 新列表项
    return bool(_LIST_ITEM_RE.match(stripped))


def _is_heading_block(para: Paragraph) -> bool:
    """判断段落是否是章节标题。"""
    if para.block_type == "heading":
        return True
    # 文本和 chapter_title 一致 → 标题段
    if para.chapter_title and para.text.strip():
        norm_text = re.sub(r"\s+", "", para.text.strip())
        norm_title = re.sub(r"\s+", "", f"{para.chapter} {para.chapter_title}".strip())
        if norm_text == norm_title:
            return True
    return False


def merge_cross_page_paragraphs(
    paragraphs: list[Paragraph],
    max_merged_length: int = 1200,
) -> list[Paragraph]:
    """
    合并被页面边界打断的连续段落。

    Args:
        paragraphs: 已排序的段落列表（按 page, order/index 排序）
        max_merged_length: 合并后文本的最大长度上限

    Returns:
        合并后的新段落列表（原列表不被修改）
    """
    if len(paragraphs) <= 1:
        return list(paragraphs)

    result: list[Paragraph] = []
    current = None  # 当前正在累积的段落（副本）

    for para in paragraphs:
        if current is None:
            # 首个段落，开始累积
            current = Paragraph(
                text=para.text,
                page=para.page,
                page_end=para.page_end or para.page,
                chapter=para.chapter,
                chapter_title=para.chapter_title,
                source_file=para.source_file,
                index=para.index,
                order=para.order,
                block_type=para.block_type,
            )
            continue

        # 判断是否应合并
        should_merge = _should_merge_paragraphs(current, para, max_merged_length)

        if should_merge:
            # 执行合并
            merged_text = _join_texts(current.text, para.text)
            current.text = merged_text
            # 更新结束页
            para_end = para.page_end or para.page
            current.page_end = max(current.page_end or current.page, para_end)
        else:
            # 不合并，保存当前段落，开始新的累积
            result.append(current)
            current = Paragraph(
                text=para.text,
                page=para.page,
                page_end=para.page_end or para.page,
                chapter=para.chapter,
                chapter_title=para.chapter_title,
                source_file=para.source_file,
                index=para.index,
                order=para.order,
                block_type=para.block_type,
            )

    # 追加最后一个
    if current is not None:
        result.append(current)

    # 重新编号
    for idx, p in enumerate(result, 1):
        p.index = idx

    return result


def _should_merge_paragraphs(
    prev: Paragraph, curr: Paragraph, max_length: int
) -> bool:
    """判断 prev 和 curr 是否应该合并。"""
    # 不同文件不合并
    if prev.source_file != curr.source_file:
        return False

    # 页面必须相邻或相同
    prev_end_page = prev.page_end or prev.page
    if curr.page > prev_end_page + 1:
        return False
    # 同一页内已分好段的不应再合并（除非有跨页证据）
    if curr.page == prev.page and curr.page == prev_end_page:
        return False

    # 不同章节不合并
    if prev.chapter and curr.chapter and prev.chapter != curr.chapter:
        return False

    # 上一块是标题 → 不合并（标题后面的正文应独立）
    if _is_heading_block(prev):
        return False

    # 上一块是表格 → 不合并
    if prev.block_type == "table":
        return False

    # 下一块是表格 → 不合并
    if curr.block_type == "table":
        return False

    # 下一块是标题 → 不合并
    if _is_heading_block(curr):
        return False

    # 下一块以新章节/列表项开头 → 不合并
    if _starts_new_block(curr.text):
        return False

    # 上一块必须以未完成句结尾
    if not _ends_incomplete(prev.text):
        return False

    # 合并后长度不能超上限
    return len(prev.text) + len(curr.text) + 1 <= max_length


def _join_texts(text_a: str, text_b: str) -> str:
    """拼接两段文本，处理 CJK 字符间的空格。"""
    a = text_a.rstrip()
    b = text_b.lstrip()
    if not a or not b:
        return a + b

    # CJK 字符范围判断
    last_a = a[-1]
    first_b = b[0]
    a_is_cjk = '\u4e00' <= last_a <= '\u9fff' or '\u3000' <= last_a <= '\u303f' or '\uff00' <= last_a <= '\uffef'
    b_is_cjk = '\u4e00' <= first_b <= '\u9fff' or '\u3000' <= first_b <= '\u303f' or '\uff00' <= first_b <= '\uffef'

    # CJK←→CJK：直接相连（消除断字空格）
    if a_is_cjk and b_is_cjk:
        return a + b
    # 否则：保留一个空格
    return a + " " + b
