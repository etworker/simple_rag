"""Version comparison stateless text normalization and metadata noise rules.

All regex patterns can be injected via parameters; defaults are built-in fallbacks
for easy customer customization via config overrides.

Configurable items:
- version_stamp_patterns: version stamp regex list (strip_revision_noise)
- tracking_hints: tracking table hint regex (is_tracking_table_row)
- tracking_row_patterns: tracking table row regex list (is_tracking_table_row)
"""

from __future__ import annotations

import re
import unicodedata

_REVISION_DATE_PATTERNS = (
    re.compile(r"\u4fee\u8ba2\u65e5\u671f[\uff1a:]\s*\d{4}[-\u5e74./]\s*\d{1,2}[-\u6708./]\s*\d{1,2}[\u65e5]?"),
    re.compile(r"\u4fee\u8ba2\u65e5\u671f[\uff1a:]\s*\d{4}[-\u5e74./]\s*\d{1,2}[-\u6708./]\s*\d{1,2}"),
)

# Default version stamp patterns (overridable via config)
_DEFAULT_VERSION_STAMP_PATTERNS = [
    r"R\d+-\d{2,}",
    r"BK-J-\d+",
    r"\u7248\u6b21[\uff1a:]\s*\S+",
]
_VERSION_STAMP_RE = re.compile(r"(?:R\d+-\d{2,}|BK-J-\d+|\u7248\u6b21[\uff1a:]\s*\S+)")
_STANDALONE_DATE_RE = re.compile(r"(?:^\s*|\b)\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}\s*(?:$|\b)")

# Default tracking table row patterns (overridable via config)
_DEFAULT_TRACKING_TABLE_ROW_PATTERNS = [
    r"^(?:\d{1,4}\s+)?(?:R\d{2,3}|N|A|D)\s+\d{4}[-./]\d{1,2}[-./]\d{1,2}",
    r"(?:R\d+-\d+\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2}(?:\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2})*\s*\|\s*(?:\u751f\u6548|\u65e0\u6548|\u9875))",
    r"(?:\d{1,4}\s*\|\s*\d{1,3}\s*\|\s*[NRAD]\s*\|\s*\d{4}[-./]\d{1,2}[-./]\d{1,2})",
]
_TABLE_ROW_RE = re.compile(
    "|".join(_DEFAULT_TRACKING_TABLE_ROW_PATTERNS),
    re.MULTILINE,
)

# Default tracking table hints (overridable via config)
_DEFAULT_TRACKING_TABLE_HINTS = (
    r"\u6709\u6548\u9875\u6e05\u5355"
    r"|\u4fee\u8ba2\u8bb0\u5f55\u8868"
    r"|\u53d1\u653e\u6e05\u5355"
    r"|\u4fee\u6539\u8bb0\u5f55"
)
_TRACKING_TABLE_HINTS = re.compile(_DEFAULT_TRACKING_TABLE_HINTS)

_FULLWIDTH_TRANS = str.maketrans(
    {
        "\uff08": "(",
        "\uff09": ")",
        "\uff1a": ":",
        "\uff1b": ";",
        "\u300a": "<",
        "\u300b": ">",
        "\u3001": ",",
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
)


def strip_revision_noise(
    text: str,
    version_stamp_patterns: list[str] | None = None,
) -> str:
    """Remove revision dates, version stamps and other metadata for content equivalence comparison.

    Args:
        text: text to process
        version_stamp_patterns: custom version stamp regex list (defaults to built-in patterns)
    """
    if not text:
        return ""
    result = text
    for pattern in _REVISION_DATE_PATTERNS:
        result = pattern.sub("", result)
    result = _STANDALONE_DATE_RE.sub("", result)
    if version_stamp_patterns:
        for p in version_stamp_patterns:
            try:
                result = re.compile(p).sub("", result)
            except Exception:
                continue
    else:
        result = _VERSION_STAMP_RE.sub("", result)
    return re.sub(r"\s+", " ", result).strip()


def strip_configured_noise(text: str, patterns) -> str:
    """Strip metadata patterns per caller config; invalid patterns are safely ignored."""
    if not text:
        return ""
    result = text
    for pattern in patterns:
        try:
            result = re.compile(pattern).sub("", result) if isinstance(pattern, str) else pattern.sub("", result)
        except Exception:
            continue
    return re.sub(r"\s+", " ", result).strip()


def is_tracking_table_row(
    change,
    tracking_hints: str | None = None,
    tracking_row_patterns: list[str] | None = None,
) -> bool:
    """Determine if a change originates from tracking tables (effective pages, revision logs, etc.).

    Args:
        change: VersionChange object or dict
        tracking_hints: custom tracking table hint regex string (defaults to built-in pattern)
        tracking_row_patterns: custom tracking table row regex list (defaults to built-in patterns)
    """
    if isinstance(change, dict):
        location = change.get("location", "") or ""
        text = change.get("new_text", "") or change.get("old_text", "") or ""
    else:
        location = getattr(change, "location", "") or ""
        text = getattr(change, "new_text", "") or getattr(change, "old_text", "") or ""

    hints_re = re.compile(tracking_hints) if tracking_hints else _TRACKING_TABLE_HINTS
    row_re = (
        re.compile("|".join(tracking_row_patterns), re.MULTILINE)
        if tracking_row_patterns
        else _TABLE_ROW_RE
    )
    return bool((location and hints_re.search(location)) or (text and row_re.search(text)))


def normalize_text(text: str) -> str:
    """Eliminate Unicode, zero-width characters and whitespace differences from PDF extraction."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad\u2060\ufe0f\ufe0e]", "", normalized)
    normalized = normalized.translate(_FULLWIDTH_TRANS)
    return " ".join(filter(None, re.split(r"[\s\u00a0\u2000-\u200a\u202f\u205f\u3000]+", normalized)))
