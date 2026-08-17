"""
文档解析入口

支持: PDF (.pdf), Word (.docx)

核心解决的问题：
1. 页眉/页脚/水印噪声 → 用坐标位置过滤
2. 表格文字混入正文 → 提取正文时排除表格区域
3. 跨页段落截断 → 流式拼接全文后按语义分段
4. 重复内容（页眉页脚） → 统计高频重复行自动识别并过滤

模块拆分:
    parser.py    — 配置 + 统一入口 + 公共 API
    _pdf.py      — PDF 解析（pdfplumber + 智能后端选择）
    _docx.py     — Word 解析（python-docx）
    _text.py     — 文本分段 + 章节识别（PDF / DOCX 共享）
    _tables.py   — 表格清洗 / 过滤 / 跨页合并（PDF / DOCX 共享）
"""

import copy
from pathlib import Path

from doc_parser._docx import extract_docx

# ── 向后兼容 re-exports（测试和 rag_server 直接 import 这些私有函数） ──
from doc_parser._pdf import _is_in_table_region, _words_to_lines, extract_pdf  # noqa: F401
from doc_parser._tables import (  # noqa: F401
    _append_rows_skip_dup_header,
    _row_token_jaccard,
    _should_merge_tables,
)
from doc_parser._tables import (  # noqa: F401
    assign_table_chapters as _assign_table_chapters,
)
from doc_parser._tables import clean_table as _clean_table  # noqa: F401
from doc_parser._tables import filter_template_tables as _filter_template_tables  # noqa: F401
from doc_parser._tables import merge_cross_page_tables as _merge_cross_page_tables  # noqa: F401
from doc_parser._text import detect_chapter as _detect_chapter  # noqa: F401
from doc_parser._text import find_page as _find_page  # noqa: F401
from doc_parser._text import segment_and_locate as _segment_and_locate  # noqa: F401
from doc_parser._text import split_stream as _split_stream  # noqa: F401
from doc_parser.models import Document

# 默认配置
DEFAULT_CONFIG = {
    # PDF 后端: "auto"(默认) / "pdfplumber" / "pymupdf" / "mineru" / "docling"
    # - pymupdf  : PyMuPDF 极速文本+规则表格（数字文本 PDF 快路径，0.01-0.1s/页）
    # - mineru   : MinerU VLM/OCR（扫描件/复杂版面，CPU 慢）
    # - docling  : IBM Docling TableFormer（复杂版面/跨页表/深度学习表格，GPU 加速）
    # - auto     : 智能路由（扫描件→mineru，无框线表格→docling，正常→pymupdf）
    "backend": "auto",
    "header_margin_pct": 8,
    "footer_margin_pct": 8,
    "repeat_line_threshold_pct": 30,
    "min_paragraph_length": 10,
    "max_paragraph_length": 600,
    "chapter_patterns": [
        r"^(\d+\.\d+\.\d+)\s+(.+)",
        r"^(\d+\.\d+)\s+(.+)",
        r"^(\d+)\s+(.+)",
        r"^第\s*(\d+)\s*[章节]\s*(.+)",
        # 中文数字章节/节（如"第六章 信息安全管理"、"第三节 访问控制"）
        r"^第\s*([一二三四五六七八九十百千]+)\s*[章节]\s*(.+)",
        # 中文数字编号（公文常见格式）
        r"^([一二三四五六七八九十]+)、\s*(.+)",
        r"^（([一二三四五六七八九十]+)）\s*(.+)",
    ],
    # 单纯数字编号 ^\d+ 的标题最大长度（超过则视为正文而非章节标题）
    # 真实章节标题如 "总则"/"日常管理" 通常 ≤20 字，
    # 而 "6 月份，管理局通过..." 等正文行会误匹配
    "single_number_title_max_length": 15,
    # 单纯数字编号的最大值（超过则视为年份等而非章节号）
    "single_number_max_value": 999,
    "noise_patterns": [
        r"^\s*$",
    ],
    # 元数据行剥离：独立成行的版本管理元数据（修订日期/发布日期/独立日期/版次等）
    # 整行匹配时从段落流中剥离（不进正文段落），避免被并入相邻正文造成句子拼接。
    # 通用文档元数据模式，可被用户 config['extract']['noise_line_patterns'] 覆盖。
    "noise_line_patterns": [
        r"^修订日期\s*[：:]\s*\S+$",
        r"^发布日期\s*[：:]\s*\S+$",
        r"^修订时间\s*[：:]\s*\S+$",
        r"^\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}$",
        r"^版\s*次\s*[：:]\s*\S+$",
        r"^版本号\s*[：:]\s*\S+$",
        r"^(?:R\d{2,}|版本\s*\S+)\s*$",
        r"^修订次数\s*[：:]\s*\d+\s+\S+\s+页码",
        r"修订日期\s*[：:]\s*\d{4}-\d{1,2}-\d{1,2}\s*$",
        # 页码标记 -N-（公文底部页码）
        r"^-\d+-\s*$",
    ],
    # ========== 左 margin 编号列分离 ==========
    # 部分 PDF 排版将章节/条款编号放在页面左侧 margin 区域（如 x≈76），
    # 正文内容在右侧（如 x≈152）。pdfplumber 按 y 坐标聚行时会把两列混在一起，
    # 导致编号出现在行首或行尾不可预测，章节识别不稳定。
    # 开启后，x < margin_number_x 的 word 若匹配 margin_number_pattern，
    # 会被识别为编号并固定拼接到正文前面，保证 chapter_patterns 稳定匹配。
    # 设为 0 表示禁用此功能。
    "margin_number_x": 130,
    # 编号列中 word 必须匹配此正则才被视为"编号"（避免左侧其他内容被误识别）。
    # 默认匹配：多级数字编号（1.1, 1.1.2.1）和单字母编号（A, B, C）。
    "margin_number_pattern": r"^(?:\d+(?:\.\d+)*|[A-Z])$",
    # ========== 空白模板表格过滤 ==========
    # 空单元格率超过此阈值的表格视为"空白模板"（如签到表、申请表），
    # 将从解析结果中排除。这些表格无实际信息价值，会干扰检索和版本对比。
    # 设为 1.0 表示禁用此功能（不过滤任何表格）。
    "table_empty_cell_threshold": 0.6,
    # 这些字符（除 None 和纯空白外）也视为"空"单元格，
    # 与 table_empty_cell_threshold 配合使用。
    "table_empty_placeholders": ["□", "☐", "○", "——"],
    # 章节标题最大长度（超过此值的不视为标题）
    "max_chapter_title_length": 80,
    # 句末断句的最小段落长度（段落达到此长度且遇到句末终止符时断开）
    "sentence_break_min_length": 40,
    # ========== 行内标题粘连 / 句末终止符 / 列表项过滤 ==========
    # 句末终止符集合（用于行内标题粘连检测 + 句末断句）
    # 可覆盖以适配不同语言/文档风格
    "sentence_end_chars": "。！？.!？",
    # 软断句字符（段落超长时遇到这些字符可断开）
    "soft_break_chars": "。.;；，,",
    # 标题终止符：标题后紧跟正文时，标题通常以这些符号结尾
    "heading_terminators": ["．", "。", "：", ":"],
    # 列表项特征：以动作动词开头且长度超过 list_verb_min_title_length 的不视为标题
    "list_verb_prefixes": [
        "负责", "建立", "整合", "加强", "完成", "管理与",
        "参与", "组织", "规划", "做好", "开展", "制定",
        "依据", "按照", "定期", "管理维护", "采集", "编制", "审批", "评估",
    ],
    # 动词开头的列表项最小标题长度（超过此值且以动词开头 → 列表项而非标题）
    "list_verb_min_title_length": 12,
    # 列表项结尾标点（真实章节标题不会以这些结尾）
    "list_end_punct": "；，。、；,.；",
    # 列表分隔符上限（标题含“、”“，”超过此数 → 正文碎片而非标题）
    "list_separator_limit": 2,
    # 行内标题粘连拆分：终止符后剩余部分最小长度
    "inline_title_min_remainder": 4,
    # 行内标题粘连拆分：正文部分最小长度
    "inline_title_min_body": 10,
    # 行内标题拆分的终止符（独立于 sentence_end_chars：后者不含"；"，
    # 因为"；"是列表项分隔符不应打断段落；但"正文…；第二章 标题"同行时
    # 仍需按"；"拆分出标题）
    "inline_title_end_chars": "。；！？.!？",
    # ========== PDF 智能后端选择 ==========
    # 快速预扫描的页数（0 表示全部页）
    "scan_sample_pages": 5,
    # 扫描件判定：平均每页文字低于此值 → 疑似扫描件
    "scan_text_per_page_threshold": 50,
    # 大图片覆盖率阈值（超过此比例 → 疑似扫描件）
    "scan_large_image_ratio": 0.5,
    # 大图片判定需与文本量联合：仅当"大图覆盖高 且 每页文本仍低于此值"才判扫描件。
    # 避免公文红头/印章/配图（大图但文本充足）被误判为扫描件走 MinerU。
    "scan_image_text_per_page_threshold": 300,
    # 有绘图线但 pdfplumber 未提取到表格的页数比例阈值
    "scan_drawings_no_tables_ratio": 0.4,
    # 低表格提取率阈值（低于此值且有绘图线 → 可能无框线表格）
    "scan_low_table_rate": 0.3,
    # 无框线表格检测：文本样本中表头关键词列表
    "table_keyword_list": [
        "序号", "名称", "描述", "风险", "措施",
        "类别", "编号", "责任人", "频率", "要求",
        "备注", "检查项", "标准",
    ],
    # 无框线表格检测：关键词命中比例阈值
    "table_keyword_hit_ratio": 0.5,
    # 无框线表格检测：列对齐行命中比例阈值
    "table_aligned_line_ratio": 0.3,
    # word 行聚合的 Y 坐标容差（pt）
    "y_tolerance": 3,
    # PDF 文本层数字断字后处理：把 "28. 55" 修复为 "28.55"
    # （部分 PDF 数字按字符拆成独立 text run，所有文本后端都会断字）。
    # 设为 False 可关闭。
    "normalize_number_spacing": True,
    # MinerU 后端：起始/结束页（0-based，None=全部）。无 GPU 时建议限定页数。
    "mineru_start_page": 0,
    "mineru_end_page": None,
    # Docling 后端：起始/结束页（1-based，None=全部，用于限页省时）
    "docling_start_page": None,
    "docling_end_page": None,
    # Docling 后端：文本型 PDF 关闭 OCR（True 仅当扫描件）
    "docling_ocr": False,
    # Docling 后端：推理设备 auto/cpu/cuda/mps（auto=自动探测，有 CUDA torch 即用 GPU）
    "docling_device": "auto",
}


def get_extract_config(config=None):
    """合并用户配置和默认配置

    兼容两种传入形态：
      - {"extract": {...}}：规范包装（parse / extract_pdf 推荐）
      - 裸 extract 字段（如 {"backend": "docling", "docling_device": "cuda"}）：
        version_diff 引擎传入的 parse_config 曾用此形态，历史兼容
    """
    result = copy.deepcopy(DEFAULT_CONFIG)
    if config:
        merge = config.get("extract", config) if isinstance(config, dict) else None
        if isinstance(merge, dict):
            result.update(merge)
    return result


# ============================================================
# 统一入口
# ============================================================


def extract_document(filepath, config=None):
    """统一文档解析入口"""
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        return extract_pdf(filepath, config, get_config=get_extract_config)
    elif ext == ".docx":
        return extract_docx(filepath, config, get_config=get_extract_config)
    elif ext == ".doc":
        raise ValueError("不支持的格式: .doc（旧版 Word 二进制格式）。请先转换为 .docx 格式。")
    else:
        raise ValueError(f"不支持的格式: {ext}")


# ============================================================
# 公共 API
# ============================================================


def parse(filepath: str, config: dict | None = None) -> Document:
    """
    解析文档为结构化段落+表格（公共 API）

    Args:
        filepath: 文件路径 (支持 .pdf, .docx)
        config: 解析配置字典，可选。支持的 key:
            - header_margin_pct: 页眉区域百分比 (默认 8)
            - footer_margin_pct: 页脚区域百分比 (默认 8)
            - repeat_line_threshold_pct: 重复行检测阈值 (默认 30)
            - min_paragraph_length: 最小段落长度 (默认 10)
            - max_paragraph_length: 最大段落长度 (默认 600)

    Returns:
        Document 对象，包含 .paragraphs 和 .tables

    Example:
        from doc_parser import parse
        doc = parse("manual.pdf", config={"min_paragraph_length": 20})
        for para in doc.paragraphs:
            print(para.text, para.location)
    """
    return extract_document(filepath, config)


def parse_to_markdown(filepath: str, config: dict | None = None) -> str:
    """
    解析文档并直接转为 Markdown（便捷 API）

    等价于 parse(filepath, config).to_markdown()

    Args:
        filepath: 文件路径 (支持 .pdf, .docx)
        config: 解析配置字典，可选

    Returns:
        Markdown 格式的字符串

    Example:
        from doc_parser import parse_to_markdown
        md = parse_to_markdown("manual.pdf")
        with open("manual.md", "w", encoding="utf-8") as f:
            f.write(md)
    """
    return parse(filepath, config).to_markdown()
