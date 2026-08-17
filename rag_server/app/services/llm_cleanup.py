"""LLM 辅助文档清洗 — 页眉/页脚研判 + 版本号提取（可选，默认关闭）。

背景
----
PDF 页眉/页脚识别是典型的"规则算法麻烦、LLM 擅长"的场景：
  - margin 百分比位置因文档而异（红头文件/表格页面/封面不同）
  - 重复行阈值不通用（页数少时重复不明显）
  - 表格区域内的页眉行可能绕过坐标剔除
常规规则（header_margin_pct / repeat_line_threshold_pct）作为第一道防线，
本模块作为第二道兜底：把"疑似页眉/页脚残留"的段落交给 LLM 研判，确认后剥离。
同时提供版本号提取的 LLM 兜底（规则提取失败时用）。

用法
----
配置 rag_server/config.json:
  "parse_cleanup": {
    "enabled": true,            # 默认 false（开启后解析结果变化，需重置知识库）
    "llm_profile": "pre_review" # 复用哪个 LLM profile
  }
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


def _find_suspect_prefix(text):
    """返回段首疑似页眉前缀（<=30 字），否则 None。"""
    if not text:
        return None
    for pat in _HEADER_PREFIX_HINTS:
        m = re.match(pat, text)
        if m:
            prefix = m.group(0)
            rest = text[len(prefix) :]
            if rest and len(rest) >= 6 and not rest.startswith(("：", ":", "。")):
                return prefix
    return None


def clean_headers(doc, config=None):
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

    from app.services.config_store import ConfigStore

    profile_name = cfg.get("llm_profile", "pre_review")
    llm_config = ConfigStore(config_path=cfg.get("config_path", "")).get_llm_profile(profile_name)

    items = chr(10).join(
        f"{i + 1}. 段首: 「{prefix}」 | 段落: {p.text[:80]}..." for i, (p, prefix) in enumerate(suspects[:30])
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
            p.text = (p.text or "")[len(prefix) :].lstrip()
            stripped_count += 1

    log.info(f"LLM 页眉清洗: 剥离 {stripped_count}/{len(suspects)} 处段首页眉残留")
    return doc


def extract_version_with_llm(doc, llm_config=None):
    """用 LLM 从文档内容判断版本号（规则提取失败时的兜底）。

    规则提取假设"修订记录表含 R5-xx + 生效列"（hardcode），并非每个文档
    都有此结构。此函数把文档开头段落 + 修订记录表文本交给 LLM，让它判断
    文档当前版本号（如 R5-21 / V2.0 / 2026-05 等，格式不限）。

    Returns:
        版本号字符串，失败返回 ""
    """
    if llm_config is None:
        try:
            from app.services.config_store import ConfigStore

            llm_config = ConfigStore().get_llm_profile("pre_review")
        except Exception:
            return ""

    samples = []
    for p in doc.paragraphs[:20]:
        t = (p.text or "").strip()
        if t:
            samples.append(t[:120])
    for t in doc.tables[:3]:
        md = t.to_markdown() if hasattr(t, "to_markdown") else ""
        if md:
            samples.append(md[:300])
    if not samples:
        return ""

    prompt = (
        "以下是某份文档的开头段落和修订记录表文本片段。请判断这份文档"
        "【当前生效的版本号】。版本号可能是 R5-21、V2.0、2.0、2026-05-09、"
        "第三版 等任意格式（不一定是 R 开头）。"
        + chr(10)
        + chr(10).join(samples)
        + chr(10)
        + "请只回复版本号本身（如 R5-21），不要解释；如果无法判断回复 N/A。"
    )
    try:
        from llm_chat import ask_once_with_config

        resp = ask_once_with_config(llm_config, prompt)
        resp = (resp or "").strip()
        if not resp or resp.upper() in ("N/A", "NA", "无", "无法判断", "未知"):
            return ""
        return resp[:30]
    except Exception as e:
        log.warning(f"LLM 版本提取失败: {e}")
        return ""
