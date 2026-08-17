"""
示例：验证 simple_rag 的「版本差异识别 + LLM 归纳」端到端能力。

真实跑通两个环节：
  1. DiffEngine.version_compare —— 解析 PDF → 语义配对 → 文本/表格 diff
     → 调用 LLM 过滤「实质性变更」
  2. 用 LLM 把识别出的差异归纳成结构化中文摘要

用法（在 version_diff 目录下执行）：
    # 默认对比 data/pdf/IT运维管理规范 的 v1 / v2
    uv run python examples/verify_version_diff.py

    # 指定任意两个版本文件
    uv run python examples/verify_version_diff.py <old.pdf> <new.pdf>

    # 仅做差异识别、跳过 LLM 归纳（更快，离线可跑）
    uv run python examples/verify_version_diff.py --no-summary

    # 列出 data/pdf 下所有可对比的多版本组
    uv run python examples/verify_version_diff.py --list-pairs

    # 指定归纳用的 LLM 模型
    uv run python examples/verify_version_diff.py --model zai.glm-4.7-flash
"""

import argparse
import os
import pathlib
import sys
import time

from loguru import logger as log

from version_diff.log import configure_logger

configure_logger()  # 独立 example：开启 version_diff 日志（console + ./logs/version_diff.log）

# 脚本位于 version_diff/examples/ → parents[2] 即项目根（simple_rag）
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PDF_DIR = REPO_ROOT / "data" / "pdf"
DEFAULT_OLD = PDF_DIR / "IT运维管理规范" / "v1" / "IT运维管理规范.pdf"
DEFAULT_NEW = PDF_DIR / "IT运维管理规范" / "v2" / "IT运维管理规范.pdf"


def build_summary_prompt(old_name, new_name, result):
    """把 version_compare 的变更整理成中文归纳 prompt。"""
    changes = result.changes
    added = [c for c in changes if c.change_type == "added"]
    removed = [c for c in changes if c.change_type == "removed"]
    modified = [c for c in changes if c.change_type == "modified"]

    lines = []
    lines.append(f"你是文档版本对比分析师。以下是文档《{old_name}》与《{new_name}》的逐条差异。")
    lines.append(
        f"共检测到 {len(changes)} 处实质性变更：新增 {len(added)} 处、"
        f"删除 {len(removed)} 处、修改 {len(modified)} 处。\n"
    )
    lines.append("请使用中文，按以下结构归纳：")
    lines.append("1. 【主要变更类别】用 3-5 个类别概括本次版本演进的方向。")
    lines.append("2. 【实质性变更摘要】挑选最重要的 5-10 处变更，说明旧版如何、新版如何、为何重要。")
    lines.append("3. 【整体评估】一句话评估本次版本更新的性质（如：常规维护 / 内容扩充 / 流程变更 / 风险收紧等）。\n")
    lines.append("逐条差异如下（已截断长文本）：\n")

    # 上限：避免超长 prompt（优先展示修改类，其次新增/删除）
    capped = (modified[:20] + added[:5] + removed[:5])[:30]
    if len(changes) > len(capped):
        lines.append(f"（仅展示最重要的 {len(capped)} / {len(changes)} 处）\n")

    def trunc(s, n=120):
        s = (s or "").replace("\n", " ")
        return s if len(s) <= n else s[:n] + "…"

    for idx, c in enumerate(capped, start=1):
        t = {"added": "新增", "removed": "删除", "modified": "修改"}.get(c.change_type, c.change_type)
        sec = c.section or ""
        loc = c.location or ""
        if c.change_type == "modified":
            body = f"旧: {trunc(c.old_text)}  →  新: {trunc(c.new_text)}"
            if getattr(c, "summary", ""):
                body += f"  [LLM标记为: {c.summary}]"
        elif c.change_type == "added":
            body = f"新增内容: {trunc(c.new_text)}"
        else:
            body = f"删除内容: {trunc(c.old_text)}"
        lines.append(f"{idx}. [{t}] 章节={sec} 位置={loc} | {body}")

    return "\n".join(lines)


def list_pairs():
    """扫描 data/pdf，列出「含多个版本子目录」的文档组，及其可用对比对。"""
    if not PDF_DIR.exists():
        log.warning(f"未找到 PDF 目录: {PDF_DIR}")
        return
    print("\ndata/pdf 下的多版本文档组（版本在子目录名中）：")
    for group in sorted(p for p in PDF_DIR.iterdir() if p.is_dir()):
        versions = sorted(v for v in group.iterdir() if v.is_dir() and any(v.glob("*.pdf")))
        if len(versions) >= 2:
            print(f"\n  {group.name}")
            for v in versions:
                pdfs = list(v.glob("*.pdf"))
                print(f"    {v.name:>10}  ←  {pdfs[0].name if pdfs else '(无 PDF)'}")
            first, last = versions[0], versions[-1]
            print(f"    可用对比: {first}  →  {last}")


def main(old_path, new_path, model, do_summary):
    from llm_chat import ask_once

    from version_diff import DiffEngine

    if not (os.path.exists(old_path) and os.path.exists(new_path)):
        log.error(f"PDF 不存在:\n  old={old_path}\n  new={new_path}")
        sys.exit(1)

    old_name = os.path.basename(old_path)
    new_name = os.path.basename(new_path)
    print("\n=== 验证版本差异识别 ===")
    print(f"旧版: {old_path}")
    print(f"新版: {new_path}")

    # 与 web 流程（rag_server）对齐：从 rag_server/config.json 读取 embedding /
    # LLM / pre_review 配置，解析后端用 pdfplumber（页眉剥离 + 碎尾合并），
    # embedding device=auto（有 GPU 用 GPU）。保证脚本结果与 web 一致。
    # 兜底：无 config.json 时退回内置默认（bge-small + bedrock_converse + cpu）。
    import json as _json

    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _config_path = os.path.join(_repo, "rag_server", "config.json")
    _llm = None
    _parse_cfg = None
    if os.path.exists(_config_path):
        try:
            with open(_config_path, encoding="utf-8") as _f:
                _app_cfg = _json.load(_f)
            _emb = dict(_app_cfg.get("embedding", {}))
            _pre_review = _app_cfg.get("pre_review", {})
            _pb = _pre_review.get("parse_backend", "pdfplumber")
            _extract = {"backend": _pb}
            if _pb == "docling":
                _extract["docling_device"] = _pre_review.get("docling_device", "auto")
                _batch = _pre_review.get("docling_batch_size", 0)
                if _batch:
                    _extract["docling_batch_size"] = int(_batch)
                _extract["docling_merge_split_paras"] = True
                _extract["docling_strip_header_prefix"] = True
            _parse_cfg = {"extract": _extract}
            _emb.setdefault("device", "auto")
            _emb["parse_config"] = _parse_cfg
            _profiles = _app_cfg.get("llm_profiles", {})
            _routing = _app_cfg.get("llm_routing", {})
            _llm = _profiles.get(_routing.get("pre_review", ""), None)
            if _llm:
                # 保留 --model 覆盖能力（默认 zai.glm-4.7-flash 与 profile 一致）
                _llm = dict(_llm)
                _llm["model"] = model
            _diff = dict(_pre_review)
            _diff.setdefault("similarity_threshold", 0.80)
            _diff.setdefault("top_k", 3)
            _diff.setdefault("batch_size", 5)
        except Exception as e:
            log.warning(f"加载 rag_server/config.json 失败，使用内置默认: {e}")
            _llm, _parse_cfg = None, None

    if _llm is None:
        _emb = {"model": "BAAI/bge-small-zh-v1.5", "device": "auto"}
        _diff = {"similarity_threshold": 0.80, "top_k": 3, "batch_size": 5}
        _llm = {
            "provider": "bedrock_converse",
            "model": model,
            "max_tokens": 2048,
            "timeout": 120,
            "max_retries": 3,
            "retry_backoff": 2.0,
        }

    cfg = {
        "embedding": _emb,
        "llm": _llm,
        "diff": _diff,
        "cache": {
            "vector_cache_dir": os.path.join(os.path.expanduser("~"), ".simple_rag", "vector_cache"),
            "parse_cache_dir": os.path.join(os.path.expanduser("~"), ".simple_rag", "parse_cache"),
        },
    }
    if _parse_cfg is not None:
        print(f"解析后端: {_parse_cfg['extract']['backend']} | embedding: {_emb.get('model')} (device={_emb.get('device')})")

    engine = DiffEngine(config=cfg)

    t0 = time.time()
    result = engine.version_compare(old_path, new_path)
    elapsed = time.time() - t0

    print(f"\n--- version_compare 结果（耗时 {elapsed:.1f}s）---")
    print(f"旧版段落数: {result.old_paragraph_count}")
    print(f"新版段落数: {result.new_paragraph_count}")
    added = [c for c in result.changes if c.change_type == "added"]
    removed = [c for c in result.changes if c.change_type == "removed"]
    modified = [c for c in result.changes if c.change_type == "modified"]
    print(f"实质性变更总数: {len(result.changes)} (新增 {len(added)} / 删除 {len(removed)} / 修改 {len(modified)})")

    print("\n--- 抽样变更 ---")
    for c in result.changes[:8]:
        t = {"added": "新增", "removed": "删除", "modified": "修改"}.get(c.change_type, c.change_type)
        old_t = (c.old_text or "")[:50].replace("\n", " ")
        new_t = (c.new_text or "")[:50].replace("\n", " ")
        print(f"  [{t}] {c.section or ''} | 旧: {old_t} → 新: {new_t}")

    summary = ""
    if do_summary:
        print("\n=== 验证 LLM 归纳能力 ===")
        t1 = time.time()
        prompt = build_summary_prompt(old_name, new_name, result)
        summary = ask_once(
            prompt,
            system_prompt="你是一名严谨的文档版本分析专家，输出结构化、客观的中文分析。",
            model=model,
            max_tokens=1500,
            timeout=180,
        )
        s_elapsed = time.time() - t1
        print(f"LLM 归纳完成（耗时 {s_elapsed:.1f}s）\n")
        print("=" * 60)
        print(summary)
        print("=" * 60)
    else:
        print("\n（已跳过 LLM 归纳：--no-summary）")

    # 落盘
    out_dir = REPO_ROOT / "temp"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "verify_version_diff_output.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"版本对比: {old_name} -> {new_name}\n")
        f.write(f"旧版段落: {result.old_paragraph_count}  新版段落: {result.new_paragraph_count}\n")
        f.write(f"实质性变更: {len(result.changes)} (新增 {len(added)} / 删除 {len(removed)} / 修改 {len(modified)})\n")
        f.write(f"version_compare 耗时: {elapsed:.1f}s\n\n")
        if summary:
            f.write("【LLM 归纳摘要】\n")
            f.write(summary)
    print(f"\n输出已保存: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证版本差异识别 + LLM 归纳（示例脚本）")
    parser.add_argument("old", nargs="?", default=str(DEFAULT_OLD), help="旧版 PDF 路径")
    parser.add_argument("new", nargs="?", default=str(DEFAULT_NEW), help="新版 PDF 路径")
    parser.add_argument("--model", default="zai.glm-4.7-flash", help="归纳用 LLM 模型")
    parser.add_argument("--no-summary", action="store_true", help="仅做差异识别，跳过 LLM 归纳")
    parser.add_argument("--list-pairs", action="store_true", help="列出 data/pdf 下所有可对比的多版本组")
    args = parser.parse_args()

    if args.list_pairs:
        list_pairs()
    else:
        main(args.old, args.new, args.model, not args.no_summary)
