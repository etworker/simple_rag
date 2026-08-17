"""LLM 辅助文档清洗 — 页眉/页脚研判（可选，默认关闭）。

背景
----
PDF 页眉/页脚识别是典型的"规则算法麻烦、LLM 擅长"的场景：
  - margin 百分比位置因文档而异（红头文件/表格页面/封面不同）
  - 重复行阈值不通用（页数少时重复不明显）
  - 表格区域内的页眉行可能绕过坐标剔除
常规规则（header_margin_pct / repeat_line_threshold_pct）作为第一道防线，
本模块作为第二道兜底：把"疑似页眉/页脚残留"的段落交给 LLM 研判，确认后剥离。

用法
----
配置 rag_demo/config.json:
  "parse_cleanup": {
    "enabled": true,            # 默认 false（开启后解析结果变化，需重置知识库）
    "llm_profile": "pre_review" # 复用哪个 LLM profile
  }

调用方（review_runner 解析后）:
  from app.services.llm_cleanup import clean_headers
  doc = clean_headers(doc, config)
"""

import re

from loguru import logger as log

# 段首疑似页眉的启发式特征（规则预筛，减少 LLM 调用量）
_HEADER_PREFIX_HINTS = [
    r"^网络与信息安全管理手册",
    r"^信息技术管理手册",
    r"^[^。；，]{2,20}(?:管理手册|工作手册|安全手册|制度手册|规范)",
    r"^奥凯航空",
]


def _find_suspect_prefix(text: str):
    """返回段首疑似页眉前缀（<=30 字），否则 None。"""
    if not text:
        return None
    for pat in _HEADER_PREFIX_HINTS:
        m = re.match(pat, text)
        if m:
            prefix = m.group(0)
            rest = text[len(prefix):]
            if rest and len(rest) >= 6 and not rest.startswith(("：", ":", "。")):
                return prefix
    return None


def clean_headers(doc, config: dict | None = None):
    """LLM 辅助剥离段落段首页眉前缀（规则预筛 + LLM 确认）。"""
    cfg = config or {}
    if not cfg.get("enabled", False):
        return doc

    suspects = []
    for p in doc.paragraphs:
        prefix = _find_suspect_prefix(p.text or "")
        if prefix:
            suspects.append((p, prefix))
    if not suspects:
        return doc

    from llm_chat import ask_once_with_config
    from app.services.config_store import ConfigStore

    profile_name = cfg.get("llm_profile", "pre_review")
    llm_config = ConfigStore(config_path=cfg.get("config_path", "")).get_llm_profile(profile_name)

    items = chr(10).join(
        f"{i+1}. 段首: {chr(12376)}{prefix}{chr(12301)} | 段落: {text[:80]}..."
        for i, (p, prefix) in enumerate(suspects[:30])
    )
    prompt = (
        "你是文档解析专家。以下段落来自一份企业制度文档，段首可能混入了"
        "【页眉/页脚残留】（如文档名称、公司名、页码等重复出现的页眉文本），"
        "也可能段首本身就是正文内容（如正文中引用书名）。"
        "请逐条判断：段首的前缀是否是页眉/页脚残留（应该剥离），"
        "还是正文的一部分（应该保留）。"
        + chr(10)
        + items
        + chr(10)
        + '回复 JSON 数组，每项 {"index": N, "is_header": true/false}，true=页眉残留应剥离，false=正文应保留。'
    )
    try:
        from version_diff.llm_util import call_llm_json

        results = call_llm_json(prompt, llm_config)
    except Exception as e:
        log.warning(f"LLM 页眉研判失败，跳过清洗: {e}")
        return doc

    if not results:
        log.warning("LLM 页眉研判无结果，跳过清洗")
        return doc

    stripped_count = 0
    for r in results:
        if not isinstance(r, dict):
            continue
        idx = int(r.get("index", 0)) - 1
        if 0 <= idx < len(suspects) and r.get("is_header", False):
            p, prefix = suspects[idx]
            p.text = (p.text or "")[len(prefix):].lstrip()
            stripped_count += 1

    log.info(f"LLM 页眉清洗: 剥离 {stripped_count}/{len(suspects)} 处段首页眉残留")
    return doc
