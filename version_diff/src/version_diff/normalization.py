"""版本对比使用的无状态文本规范化与元数据噪声规则。"""

from __future__ import annotations

import re
import unicodedata

_REVISION_DATE_PATTERNS = (
    re.compile(r"修订日期[：:]\s*\d{4}[-年./]\s*\d{1,2}[-月./]\s*\d{1,2}[日]?"),
    re.compile(r"修订日期[：:]\s*\d{4}[-年./]\s*\d{1,2}[-月./]\s*\d{1,2}"),
)
_VERSION_STAMP_RE = re.compile(r"(?:R\d+-\d{2,}|BK-J-\d+|版次[：:]\s*\S+)")
_STANDALONE_DATE_RE = re.compile(r"(?:^\s*|\b)\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}\s*(?:$|\b)")
_TABLE_ROW_RE = re.compile(
    r"^(?:\d{1,4}\s+)?(?:R\d{2,3}|N|A|D)\s+\d{4}[-./]\d{1,2}[-./]\d{1,2}"
    r"|(?:R\d+-\d+\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2}(?:\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2})*\s*\|\s*(?:生效|无效|页))"
    r"|(?:\d{1,4}\s*\|\s*\d{1,3}\s*\|\s*[NRAD]\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2})",
    re.MULTILINE,
)
_TRACKING_TABLE_HINTS = re.compile(r"有效页清单|修订记录表|发放清单|修改记录")
_FULLWIDTH_TRANS = str.maketrans(
    {
        "（": "(",
        "）": ")",
        "：": ":",
        "；": ";",
        "《": "<",
        "》": ">",
        "、": ",",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)


def strip_revision_noise(text: str) -> str:
    """移除修订日期、版本标记等元数据，用于内容等价比较。"""
    if not text:
        return ""
    result = text
    for pattern in _REVISION_DATE_PATTERNS:
        result = pattern.sub("", result)
    result = _STANDALONE_DATE_RE.sub("", result)
    result = _VERSION_STAMP_RE.sub("", result)
    return re.sub(r"\s+", " ", result).strip()


def strip_configured_noise(text: str, patterns) -> str:
    """按调用方配置剥离元数据模式；无效模式安全忽略。"""
    if not text:
        return ""
    result = text
    for pattern in patterns:
        try:
            result = re.compile(pattern).sub("", result) if isinstance(pattern, str) else pattern.sub("", result)
        except Exception:
            continue
    return re.sub(r"\s+", " ", result).strip()


def is_tracking_table_row(change) -> bool:
    """判断 change 是否来自有效页、修订记录等跟踪表。"""
    if isinstance(change, dict):
        location = change.get("location", "") or ""
        text = change.get("new_text", "") or change.get("old_text", "") or ""
    else:
        location = getattr(change, "location", "") or ""
        text = getattr(change, "new_text", "") or getattr(change, "old_text", "") or ""
    return bool((location and _TRACKING_TABLE_HINTS.search(location)) or (text and _TABLE_ROW_RE.search(text)))


def normalize_text(text: str) -> str:
    """消除 PDF 提取产生的 Unicode、零宽字符与空白差异。"""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad\u2060\ufe0f\ufe0e]", "", normalized)
    normalized = normalized.translate(_FULLWIDTH_TRANS)
    return " ".join(filter(None, re.split(r"[\s\u00a0\u2000-\u200a\u202f\u205f\u3000]+", normalized)))
