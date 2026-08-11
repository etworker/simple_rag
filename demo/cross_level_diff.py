#!/usr/bin/env python
"""
不同级别文档内容差异对比示例（demo）

用 version_diff 引擎，对比两份「不同级别」的文档（例如《二级…信息技术管理手册》与
《三级…信息技术部工作手册》），找出内容上的实质差异（非版本差异）。

特点：
- 文档路径作为命令行参数传入，用户友好
- 自动加载项目根目录 .env（供 LLM 鉴权，如 AWS_BEARER_TOKEN_BEDROCK）
- 自动过滤「目录 / 记录清单 / 页码占位 / 短编号」等版式噪声，聚焦实质内容差异
- 输出按「新增 / 删除 / 修改」分组的中文报告，保存为文件并打印控制台摘要

用法:
    uv run --project version_diff python demo/cross_level_diff.py <文档A路径> <文档B路径> [--out 报告输出路径] [--llm]

示例:
    uv run --project version_diff python demo/cross_level_diff.py \
        "data/pdf/(二级)(司批)信息技术管理手册/R3-3/(二级)(司批)信息技术管理手册.pdf" \
        "data/pdf/(三级)(司批)信息技术部工作手册/R6-7/(三级)(司批)信息技术部工作手册.pdf" \
        --out demo/reports/cross_level_diff.md

说明:
    - 文档A 视为「旧」，文档B 视为「新」；输出「新增」= 文档B 有而 A 无，「删除」= A 有而 B 无。
    - 默认纯规则模式（无需 LLM，新增/删除完整，修改类差异无摘要）；
      加 --llm 并对修改类差异生成摘要（需在 .env 配置有效 AWS_BEARER_TOKEN_BEDROCK）。
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

# 让脚本可从仓库任意位置运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "version_diff"))


# ---------------------------------------------------------------------------
# .env 加载（供 LLM 鉴权）
# ---------------------------------------------------------------------------
def load_dotenv(path: Path | None = None) -> None:
    """加载项目根 .env 到环境变量（若尚未加载）。"""
    dotenv = path or PROJECT_ROOT / ".env"
    if not dotenv.exists():
        return
    with open(dotenv, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and not os.environ.get(key):  # 已存在则不覆盖
                os.environ[key] = val


# ---------------------------------------------------------------------------
# 跨级别文档噪声过滤（通用、不依赖具体文档内容）
# ---------------------------------------------------------------------------
# 目录/记录清单/页码占位等「版式」行，跨级文档常因体例不同而整体不同，属噪声。
_NOISE_PATTERNS = [
    re.compile(r"^\d+\.\d*\s*手册记录清单\s*$"),          # 手册记录清单
    re.compile(r"^\d+\.\d*\s*[\u4e00-\u9fa5A-Za-z0-9 ]+\s+\d+-\d+\s+\d+\s*页\s*$"),  # 目录: "1.1 信息化管理内容 8 页"
    re.compile(r"^\d+(?:\.\d+)*\s+[\u4e00-\u9fa5A-Za-z ]+\s+\d+\s*页\s*$"),  # 目录条目: "1 组织机构及职责 35 页"
    re.compile(r"^\d+(?:\.\d+)*\s+[\u4e00-\u9fa5A-Za-z ]+\s+\d+\.\d+-\d+\s*$"),  # 目录条目带编号: "1.1 信息技术部职责 1.1-1"
    re.compile(r"^附录\s+\d+-\d+\s*$"),                    # 附录编号
    re.compile(r"^\d+\s+附录\s+[\u4e00-\u9fa5A-Za-z0-9 ]+[\u4e00-\u9fa5]+\s+附录\s+\d+-\d+\s*$"),  # "1 附录 XX 附录 1-1"
    re.compile(r"^\d+-\d+\s+[\u4e00-\u9fa5A-Za-z0-9 ]+\s+[\u4e00-\u9fa5]+.*\d+\s*页\s*$"),
    re.compile(r"^[A-Z]\s+根据公司下发的各类规范性或程序性文件.*$"),  # 修订说明
]

# 目录条目启发式：以章节编号开头、含「页」或「x.y-z」目录编号标记、且整体较短
_DIR_ENTRY_START_RE = re.compile(r"^\d+(?:\.\d+)*\s+\S")


def _is_noise(text: str) -> bool:
    """判断一条差异文本是否属版式噪声（目录/记录清单/页码占位等）。"""
    if not text:
        return True
    stripped = text.strip()
    if len(stripped) < 6:  # 过短，无实质内容
        return True
    for pat in _NOISE_PATTERNS:
        if pat.match(stripped):
            return True
    # 目录条目：章节编号开头 + 含页码「N 页」或目录编号「x.y-z」 + 短文本
    return (
        len(stripped) < 40
        and _DIR_ENTRY_START_RE.match(stripped)
        and ("页" in stripped or re.search(r"\d+\.\d+-\d+", stripped))
    )


def is_noise_change(c) -> bool:
    """一条 change 是否属噪声：其「实质文本」（新增看 new、删除看 old）全为噪声。"""
    if c.change_type == "added":
        return _is_noise(c.new_text or "")
    if c.change_type == "removed":
        return _is_noise(c.old_text or "")
    # modified：旧新都看，只要有一个是实质内容就保留
    return _is_noise(c.old_text or "") and _is_noise(c.new_text or "")


def filter_noise(changes) -> tuple[list, list]:
    """过滤噪声差异，返回 (实质差异, 被过滤的噪声差异)。"""
    real, noise = [], []
    for c in changes:
        (noise if is_noise_change(c) else real).append(c)
    return real, noise


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------
def build_report(doc_a, doc_b, real, noise, minor, elapsed) -> str:
    def label(t):
        return {"added": "新增", "removed": "删除", "modified": "修改"}.get(t, t)

    lines = []
    lines.append("# 跨级别文档内容差异对比")
    lines.append("")
    lines.append(f"- 文档A（旧）: `{doc_a}`")
    lines.append(f"- 文档B（新）: `{doc_b}`")
    lines.append(f"- 对比耗时: {elapsed:.1f}s")
    lines.append("")
    lines.append(f"**实质差异: {len(real)} 处**（新增 {sum(1 for c in real if c.change_type=='added')} / "
                 f"删除 {sum(1 for c in real if c.change_type=='removed')} / "
                 f"修改 {sum(1 for c in real if c.change_type=='modified')}）"
                 f"；噪声(版式/目录) {len(noise)}；细微 {len(minor)}")
    lines.append("")

    for key, title in [("added", "新增（文档B 有而 文档A 无）"),
                       ("removed", "删除（文档A 有而 文档B 无）"),
                       ("modified", "修改（共同内容但表述不同）")]:
        group = [c for c in real if c.change_type == key]
        if not group:
            continue
        lines.append(f"## {title}（{len(group)} 处）")
        lines.append("")
        for i, c in enumerate(group, 1):
            lines.append(f"### [{i}] 位置: {c.location or '未知'}")
            if c.section:
                lines.append(f"章节: {c.section}")
            if c.old_text:
                lines.append(f"- 旧: {c.old_text}")
            if c.new_text:
                lines.append(f"- 新: {c.new_text}")
            if c.summary:
                lines.append(f"- 摘要: {c.summary}")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="不同级别文档内容差异对比")
    parser.add_argument("doc_a", help="文档A 路径（视为旧）")
    parser.add_argument("doc_b", help="文档B 路径（视为新）")
    parser.add_argument("--out", default="", help="报告输出路径（默认打印到控制台，也可指定 .md 文件）")
    parser.add_argument("--llm", action="store_true", help="启用 LLM（对修改类差异生成摘要，需 .env 配置有效 token）")
    parser.add_argument("--embedding", default="BAAI/bge-small-zh-v1.5", help="embedding 模型名")
    args = parser.parse_args()

    for p in (args.doc_a, args.doc_b):
        if not os.path.exists(p):
            print(f"[错误] 文件不存在: {p}")
            sys.exit(1)

    load_dotenv()

    # LLM 仅用于「修改」类差异的摘要与噪声过滤（增强项）。
    # 默认纯规则模式（provider=noop 快速失败 → 全部 modified 保留，无网络调用）；
    # 加 --llm 并配置有效 token 后启用 LLM 摘要。
    if not args.llm or not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        if not args.llm:
            print("[提示] 纯规则模式：未启用 LLM 摘要（新增/删除差异完整，修改类差异无摘要）。加 --llm 可生成摘要。")
        elif not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
            print("[提示] 未找到 AWS_BEARER_TOKEN_BEDROCK，自动使用纯规则模式。加 --llm 需先配置 token。")
        llm_config = {
            "provider": "noop",  # 未知后端 → 快速失败 → engine 保留全部 modified
            "model": "",
            "api_key": "",
        }
        # 抑制 call_llm_json 的「未知后端」重试日志
        import logging
        logging.getLogger("version_diff.llm_util").setLevel(logging.CRITICAL)
    else:
        llm_config = {
            "provider": "bedrock_converse",
            "model": "zai.glm-4.7-flash",
            "max_tokens": 2048,
            "timeout": 120,
            "max_retries": 2,
            "retry_backoff": 2.0,
            "api_key_env": "AWS_BEARER_TOKEN_BEDROCK",
        }

    config = {
        "embedding": {"model": args.embedding, "device": "cpu"},
        "llm": llm_config,
        "diff": {
            "similarity_threshold": 0.80,
            "top_k": 3,
            "batch_size": 5,
            "noise_filter": {"enabled": True},
        },
    }

    from version_diff import DiffEngine

    print("加载对比引擎 ...")
    engine = DiffEngine(config=config)
    print(f"对比: {os.path.basename(args.doc_a)}  ->  {os.path.basename(args.doc_b)}")

    def on_progress(step, percent, message):
        print(f"\r[{step}] {message} {percent*100:.0f}%", end="", flush=True)

    t0 = time.time()
    res = engine.version_compare(args.doc_a, args.doc_b, on_progress=on_progress)
    elapsed = time.time() - t0
    print(f"\n对比完成: 耗时 {elapsed:.1f}s")

    real, noise = filter_noise(res.changes)
    report = build_report(args.doc_a, args.doc_b, real, noise, res.minor_changes, elapsed)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"报告已保存: {out_path}")
    else:
        print("\n" + report)

    # 控制台摘要
    print("\n===== 摘要 =====")
    print(f"实质差异 {len(real)} 处 | 新增 {sum(1 for c in real if c.change_type=='added')} | "
          f"删除 {sum(1 for c in real if c.change_type=='removed')} | "
          f"修改 {sum(1 for c in real if c.change_type=='modified')} | 噪声 {len(noise)} | 细微 {len(res.minor_changes)}")


if __name__ == "__main__":
    main()
