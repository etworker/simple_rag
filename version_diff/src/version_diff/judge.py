"""
LLM 一致性判断模块（单次推理版）

设计理念：
  直接问 LLM 一个核心问题："这两段文字是否对同一件事给出了矛盾的描述？"
  让 LLM 充分发挥理解能力，给出结构化的判断结果。

流水线：
  1. 规则预过滤：明显的编号/元数据/层级名称差异直接排除
  2. LLM 单次推理：对每对段落判断是否存在真正的矛盾
  3. 输出：仅保留 LLM 确认为 inconsistent 的项（附带矛盾点说明）
"""

import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from loguru import logger as log

from version_diff.llm_util import call_llm_json
from version_diff.prefilter import batch_pre_classify

# ============================================================
# 判断结果
# ============================================================


@dataclass
class JudgeResult:
    """LLM 判断结果"""

    inconsistent_items: list = field(default_factory=list)  # 确认为不一致的 TextDiffItem
    suspect_items: list = field(default_factory=list)  # 疑似不一致（confidence=low）
    rule_filtered: int = 0  # 规则预过滤排除的数量
    llm_judged: int = 0  # 实际送 LLM 判断的数量


# ============================================================
# Prompt
# ============================================================

# ============================================================
# Prompt 模板
# ============================================================

# 默认 prompt 文件随包发布，路径相对于本模块
_DEFAULT_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompts", "consistency_judge.txt")

# 极简兜底（仅在随包 .txt 文件丢失时使用）
_FALLBACK_PROMPT = """请判断以下 {count} 对段落是否存在文档间不一致（矛盾）。

{items}

回复JSON数组：[{{"index": N, "inconsistent": true/false, "point": "...", "doc_a_says": "...", "doc_b_says": "..."}}]
宁可漏报不要误报。"""


def _load_default_prompt() -> str:
    """从随包 .txt 文件加载默认 prompt 模板"""
    try:
        with open(_DEFAULT_PROMPT_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        log.warning(f"加载默认 prompt 文件失败，使用兜底: {e}")
        return _FALLBACK_PROMPT


# 模块加载时读取一次（文件内容不变，无需每次调用都读磁盘）
CONSISTENCY_JUDGE_PROMPT = _load_default_prompt()


# ============================================================
# 摘要兜底
# ============================================================


def _generate_specific_summary(item) -> str:
    """从标注片段自动生成变更描述（兜底）"""
    from version_diff.prefilter import annotate_fragments

    annotated = annotate_fragments(item)
    if not annotated:
        return item.description or "内容有差异"
    parts = []
    for a in annotated[:3]:
        old = a.old_text.strip()
        new = a.new_text.strip()
        if a.annotation == "section_ref" and a.ftype == "replace":
            parts.append(f"引用号§{old}→§{new}")
        elif a.annotation == "numeric_value" and a.ftype == "replace":
            parts.append(f"「{old}」→「{new}」")
        elif a.annotation == "text_content":
            if a.ftype == "replace":
                parts.append(f"「{old[:15]}...」→「{new[:15]}...」")
            elif a.ftype == "insert":
                parts.append(f"新增「{new[:20]}...」")
            elif a.ftype == "delete":
                parts.append(f"删除「{old[:20]}...」")
    return "；".join(parts) if parts else (item.description or "内容有差异")


# ============================================================
# LLM 调用
# ============================================================


# ============================================================
# 格式化 + 批量判断
# ============================================================


def _format_judge_items(items):
    """将候选段落对格式化为判断 prompt"""
    parts = []
    for i, item in enumerate(items, 1):
        text_a = item.para_a.text[:300].replace("\n", " ")
        text_b = item.para_b.text[:300].replace("\n", " ")
        src_a = item.para_a.source_file
        src_b = item.para_b.source_file
        loc_a = item.para_a.location
        loc_b = item.para_b.location
        parts.append(f"--- 第 {i} 对 ---\n文档A [{src_a}] {loc_a}:\n{text_a}\n\n文档B [{src_b}] {loc_b}:\n{text_b}")
    return "\n\n".join(parts)


def _resolve_prompt_template(judge_config: dict) -> str:
    """
    解析 prompt 模板，优先级：
      1. judge_config["prompt_file"] — 外部文件
      2. judge_config["prompt_template"] — 直接传入的字符串
      3. 内置 CONSISTENCY_JUDGE_PROMPT
    """
    # 优先级 1: 外部文件
    prompt_file = judge_config.get("prompt_file", "")
    if prompt_file and os.path.exists(prompt_file):
        with open(prompt_file, encoding="utf-8") as f:
            template = f.read().strip()
        if template:
            log.info(f"  使用外部 prompt 文件: {prompt_file}")
            return template

    # 优先级 2: 配置直传
    template = judge_config.get("prompt_template", "")
    if template:
        log.info("  使用自定义 prompt 模板")
        return template

    # 优先级 3: 内置默认
    return CONSISTENCY_JUDGE_PROMPT


def _judge_batch(items, llm_config: dict, prompt_template: str = ""):
    """对一批候选对调用 LLM 判断是否存在不一致"""
    items_text = _format_judge_items(items)
    template = prompt_template or CONSISTENCY_JUDGE_PROMPT
    prompt = template.format(count=len(items), items=items_text)
    return call_llm_json(prompt, llm_config)


# ============================================================
# 主入口
# ============================================================


def _calculate_batch_size(llm_config: dict, sample_items: list) -> int:
    """
    根据 context_window 动态计算 batch_size

    如果配置了 batch_size > 0，直接使用。
    否则根据 context_window、max_tokens、prompt 模板开销、平均每对大小估算。
    """
    # 如果显式配置了 batch_size > 0，直接使用
    explicit_bs = llm_config.get("batch_size", 0) or 0
    if explicit_bs > 0:
        return explicit_bs

    context_window = llm_config.get("context_window", 8192)
    max_tokens = llm_config.get("max_tokens", 2048)

    # prompt 模板开销（估算）
    prompt_overhead = len(CONSISTENCY_JUDGE_PROMPT)  # ~2000 chars

    # 采样估算每对的大小
    if sample_items:
        sample_text = _format_judge_items(sample_items[:3])
        avg_pair_size = len(sample_text) / max(len(sample_items[:3]), 1)
    else:
        avg_pair_size = 700  # 默认：~300字×2 + 格式

    # 可用空间 = 上下文窗口 - prompt模板 - 响应预留
    usable = context_window - prompt_overhead - max_tokens
    if usable <= 0:
        return 5  # 安全默认

    calculated = int(usable / avg_pair_size)
    return max(1, min(calculated, 50))  # clamp [1, 50]


def _run_batches_sequential(batches, llm_config, prompt_template, num_batches):
    """串行执行所有批次"""
    results = []
    for batch_idx, batch in enumerate(batches):
        log.info(f"    batch {batch_idx + 1}/{num_batches} ({len(batch)} pairs)...")
        batch_results = _judge_batch(batch, llm_config, prompt_template)
        results.append((batch_idx, batch, batch_results))
    return results


def _run_batches_concurrent(batches, llm_config, prompt_template, num_batches, concurrency):
    """
    并发执行批次，遇到 429 自动降级

    策略：
    - 初始并发度 = 配置值
    - 如果连续失败，降级到串行
    """
    results = []
    effective_concurrency = concurrency
    sequential_fallback = []

    with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
        future_to_batch = {}
        for batch_idx, batch in enumerate(batches):
            log.info(f"    batch {batch_idx + 1}/{num_batches} ({len(batch)} pairs) submitted")
            future = executor.submit(_judge_batch, batch, llm_config, prompt_template)
            future_to_batch[future] = (batch_idx, batch)

        for future in as_completed(future_to_batch):
            batch_idx, batch = future_to_batch[future]
            try:
                batch_results = future.result()
                if batch_results is None:
                    # LLM 调用失败，加入串行重试队列
                    sequential_fallback.append((batch_idx, batch))
                    log.warning(f"    batch {batch_idx + 1} 失败，加入串行重试队列")
                else:
                    results.append((batch_idx, batch, batch_results))
                    log.info(f"    batch {batch_idx + 1}/{num_batches} 完成")
            except Exception as e:
                sequential_fallback.append((batch_idx, batch))
                log.warning(f"    batch {batch_idx + 1} 异常: {e}，加入串行重试队列")

    # 对失败的批次进行串行重试
    if sequential_fallback:
        log.info(f"    串行重试 {len(sequential_fallback)} 个失败批次...")
        for batch_idx, batch in sequential_fallback:
            log.info(f"    重试 batch {batch_idx + 1}/{num_batches}...")
            batch_results = _judge_batch(batch, llm_config, prompt_template)
            if batch_results is not None:
                results.append((batch_idx, batch, batch_results))
            else:
                results.append((batch_idx, batch, None))

    # 按批次顺序排序
    results.sort(key=lambda x: x[0])
    return results


def _process_batch_results(batch, results):
    """处理单个批次的 LLM 结果，返回 (确认不一致项, 疑似不一致项)"""
    new_items = []
    suspect_items = []
    if not results:
        return new_items, suspect_items
    for r in results:
        if not isinstance(r, dict):
            log.warning(f"    ↪ 跳过非结构化 judge 输出项: {r!r}")
            continue
        try:
            idx = int(r.get("index", 0)) - 1
        except (ValueError, TypeError):
            log.warning(f"    ↪ 非法 index 字段: {r.get('index')!r}")
            continue
        if 0 <= idx < len(batch) and r.get("inconsistent", False):
            item = batch[idx]
            point = r.get("point", "")
            doc_a_says = r.get("doc_a_says", "")
            doc_b_says = r.get("doc_b_says", "")
            confidence = r.get("confidence", "high")
            item.llm_point = point
            item.llm_doc_a_says = doc_a_says
            item.llm_doc_b_says = doc_b_says
            if point and doc_a_says and doc_b_says:
                item.llm_reason = f"{point}：A称「{doc_a_says}」，B称「{doc_b_says}」"
            elif point:
                item.llm_reason = point
            else:
                item.llm_reason = _generate_specific_summary(item)
                item.llm_point = item.llm_reason
            if confidence == "low":
                item.__dict__["category"] = "suspect"
                item.__dict__["category_label"] = "疑似不一致"
                suspect_items.append(item)
            else:
                item.__dict__["category"] = "inconsistency"
                item.__dict__["category_label"] = "文档间不一致"
                new_items.append(item)
    return new_items, suspect_items


def filter_diffs(diff_items, llm_config: dict | None = None, judge_config: dict | None = None, on_batch=None):
    """
    一致性判断流水线

    Args:
        diff_items: 字级 diff 结果列表
        llm_config: LLM 配置字典。如果为 None，使用默认配置。
        judge_config: 判断配置字典。可选。
        on_batch: 增量回调 fn(batch_idx: int, total_batches: int, new_inconsistent_items: list)
                  每批 LLM 完成后调用，用于前端增量展示。

    Returns:
        JudgeResult 包含:
          - inconsistent_items: 确认为不一致的 TextDiffItem 列表
          - rule_filtered: 规则预过滤排除的数量
          - llm_judged: 实际送 LLM 判断的数量
    """
    if not diff_items:
        return JudgeResult()

    if llm_config is None:
        llm_config = {}

    if judge_config is None:
        judge_config = {}
    prompt_template = _resolve_prompt_template(judge_config)

    log.info("🧠 一致性判断流水线")

    # ========== 阶段1：规则预过滤 ==========
    log.info("  阶段1: 规则预过滤...")
    pre_classified, uncertain = batch_pre_classify(diff_items)

    pre_counts = {}
    for _, cat, _ in pre_classified:
        pre_counts[cat] = pre_counts.get(cat, 0) + 1

    log.info(f"  规则预过滤: {len(pre_classified)} 项排除 ({pre_counts})")
    log.info(f"  待 LLM 判断: {len(uncertain)} pairs")

    # ========== 阶段2：LLM 判断（支持增量回调）==========
    inconsistent_items = []

    if uncertain:
        batch_size = _calculate_batch_size(llm_config, uncertain)
        concurrency = llm_config.get("concurrency", 1) or 1

        num_batches = math.ceil(len(uncertain) / batch_size)
        batches = []
        for batch_idx in range(num_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, len(uncertain))
            batches.append(uncertain[start:end])

        log.info(
            f"  阶段2: LLM 判断（{len(uncertain)} pairs, {num_batches} batches, "
            f"batch_size={batch_size}, concurrency={concurrency}）..."
        )

        # 有回调 → 逐批串行处理（保证顺序 + 每批完成后回调）
        # 无回调且并发 > 1 → 并发执行（旧行为）
        suspect_items = []

        if on_batch is not None:
            for batch_idx, batch in enumerate(batches):
                log.info(f"    batch {batch_idx + 1}/{num_batches} ({len(batch)} pairs)...")
                batch_results = _judge_batch(batch, llm_config, prompt_template)
                new_items, new_suspects = _process_batch_results(batch, results=batch_results)
                inconsistent_items.extend(new_items)
                suspect_items.extend(new_suspects)
                log.info(
                    f"    batch {batch_idx + 1}/{num_batches} 完成 "
                    f"(+{len(new_items)} 矛盾, +{len(new_suspects)} 疑似, "
                    f"累计 {len(inconsistent_items)})"
                )
                on_batch(batch_idx, num_batches, new_items)
        elif concurrency > 1:
            batch_results = _run_batches_concurrent(batches, llm_config, prompt_template, num_batches, concurrency)
            for _, batch, results in batch_results:
                new_items, new_suspects = _process_batch_results(batch, results)
                inconsistent_items.extend(new_items)
                suspect_items.extend(new_suspects)
        else:
            batch_results = _run_batches_sequential(batches, llm_config, prompt_template, num_batches)
            for _, batch, results in batch_results:
                new_items, new_suspects = _process_batch_results(batch, results)
                inconsistent_items.extend(new_items)
                suspect_items.extend(new_suspects)

    log.info(
        f"  ✅ 确认不一致: {len(inconsistent_items)} 处" + (f" (+{len(suspect_items)} 疑似)" if suspect_items else "")
    )
    log.info(
        f"  📊 总计: {len(diff_items)} 候选 → 规则排除 {len(pre_classified)} "
        f"→ LLM判断 {len(uncertain)} → 确认矛盾 {len(inconsistent_items)}"
    )

    return JudgeResult(
        inconsistent_items=inconsistent_items,
        suspect_items=suspect_items,
        rule_filtered=len(pre_classified),
        llm_judged=len(uncertain),
    )


# ============================================================
# 公共接口
# ============================================================


def judge_pairs(pairs, llm_config: dict, judge_config: dict | None = None):
    """
    判断若干段落对（dict 形式）是否存在矛盾。

    公共接口，供 RAG 问答冲突检测等外部调用，避免外部依赖 judge 模块
    的内部私有函数与数据结构（鸭子类型）。

    Args:
        pairs: list[dict]，每个元素:
            {
              "a": {"text": str, "source_file": str, "location": str},
              "b": {"text": str, "source_file": str, "location": str},
            }
        llm_config: LLM 配置字典（model / region / api_key_env / api_key / ...），
            直接透传给底层 ask_once。
        judge_config: 可选，prompt 覆盖配置（prompt_file / prompt_template）。

    Returns:
        list[dict] — LLM 结构化结果，每项含
            {"index", "inconsistent", "point", "doc_a_says", "doc_b_says"}
        或 None — LLM 不可用 / 调用失败
    """
    if not pairs:
        return []

    from types import SimpleNamespace

    # 构造 judge 内部兼容的 item（鸭子类型），细节封装在 version_diff 内部，
    # 外部调用方无需感知 TextDiffItem 结构。
    items = []
    for pair in pairs:
        a, b = pair["a"], pair["b"]
        items.append(
            SimpleNamespace(
                para_a=SimpleNamespace(
                    text=a.get("text", ""),
                    source_file=a.get("source_file", ""),
                    location=a.get("location", ""),
                ),
                para_b=SimpleNamespace(
                    text=b.get("text", ""),
                    source_file=b.get("source_file", ""),
                    location=b.get("location", ""),
                ),
            )
        )

    prompt_template = _resolve_prompt_template(judge_config or {})
    return _judge_batch(items, llm_config, prompt_template)
