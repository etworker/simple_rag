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

import json
import logging
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from version_diff.prefilter import batch_pre_classify

log = logging.getLogger("version_diff.judge")


# ============================================================
# 判断结果
# ============================================================


@dataclass
class JudgeResult:
    """LLM 判断结果"""

    inconsistent_items: list = field(
        default_factory=list
    )  # 确认为不一致的 TextDiffItem
    rule_filtered: int = 0  # 规则预过滤排除的数量
    llm_judged: int = 0  # 实际送 LLM 判断的数量


# ============================================================
# Prompt
# ============================================================

CONSISTENCY_JUDGE_PROMPT = """你是企业文档一致性审核专家。我会给你若干对段落，每对来自同一体系下的不同文档（如：上级管理手册 vs 下级工作手册 vs 安全管理手册）。

你的任务：判断每对段落是否存在"文档间不一致"（即 RAG 检索时会导致矛盾答案的风险）。

**什么是"文档间不一致"：**
两篇文档都对**完全相同的具体事项**（同一个流程、同一个时限、同一个审批要求等）给出了**明确且具体的描述**，但描述内容互相矛盾，不可能同时为真。

核心判断标准：如果用户问一个具体问题（如"巡检频率是多少？"），从A文档和B文档会得到**两个不同的具体答案**，那才是矛盾。

**什么不是"文档间不一致"：**
- A 文档详细描述了某流程，B 文档完全没提到 → 不是矛盾，是覆盖范围不同
- A 写"全公司适用"，B 写"信息技术部适用" → 不是矛盾，是上下级文档的层级细化
- A 和 B 对同一事项的措辞不同但实际要求完全一致 → 不是矛盾（措辞差异≠语义矛盾）
- 仅章节编号/版本号/修订日期不同 → 不是矛盾
- A 比 B 多了几条细节补充（但不与 B 冲突） → 不是矛盾，是细化
- A 说"负责XX的维修、维护"，B 说"XX的维修、维护" → 不是矛盾，只是有无"负责"前缀词
- A 列出5项职责，B 列出其中3项 → 不是矛盾，是详略不同
- 上级文档用概括性表述，下级文档展开细化 → 不是矛盾，是文档层级关系的正常表现
- A 说"由部门负责人审批"，B 说"由信息技术部总经理审批"（而信息技术部总经理就是部门负责人）→ 不是矛盾，是同一角色的不同称呼
- A 和 B 描述的对象/范围其实不同（如A说的是"日常巡检"，B说的是"月度大检"）→ 不是矛盾，是不同事项

**矛盾的典型表现：**
- 同一操作的频率/时限/数量不同（如"每月巡检" vs "每季度巡检"）
- 同一流程的审批层级不同（如"经理审批" vs "需总监和经理双签"）
- 同一事项的责任部门不同（如"运维组负责" vs "安保组负责"）
- 同一规定的处罚/考核标准不同
- 同一操作的合格标准不同（如"90分合格" vs "80分合格"）

**判断前请自问：**
1. 这两段描述的是不是**完全相同的事项**？（不同事项不可能矛盾）
2. 如果是同一事项，两边给出的具体要求**是否不可调和**？（详略不同、措辞不同、有无前缀词 都不算不可调和）
3. 会不会是上下级文档的正常关系？（上级概括 + 下级细化 = 正常）

请对以下 {count} 对段落逐一判断：

{items}

请回复一个JSON数组。对每对段落：
- 如果**存在真正的矛盾**，返回 {{"index": N, "inconsistent": true, "point": "矛盾的具体事项", "doc_a_says": "A文档的说法", "doc_b_says": "B文档的说法"}}
- 如果**不存在矛盾**，返回 {{"index": N, "inconsistent": false}}

⚠️ 重要：宁可漏报也不要误报！你的误报率应该接近零。
只有当你**非常确信**两边对同一事项给出了不可调和的不同具体要求时，才判为 inconsistent。
有任何犹豫（"可能是措辞不同"、"可能是不同层级"、"可能是不同事项"）→ 一律判为 false。

[{{"index": 1, "inconsistent": true, "point": "巡检频率", "doc_a_says": "每日巡检", "doc_b_says": "每周巡检"}}, {{"index": 2, "inconsistent": false}}, ...]"""


# ============================================================
# 摘要兜底
# ============================================================

_GENERIC_PATTERNS = [
    re.compile(r"^数值变更\s*[\(（]"),
    re.compile(r"^大幅内容变更$"),
    re.compile(r"^部分内容修改$"),
]


def _is_generic_summary(summary: str) -> bool:
    if not summary:
        return True
    return any(p.match(summary.strip()) for p in _GENERIC_PATTERNS)


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


def _call_llm(prompt: str, llm_config: dict) -> str:
    """
    调用 LLM API（委托给 llm_chat.ask_once）

    Args:
        prompt: 完整 prompt 文本
        llm_config: LLM 配置字典（包含 model, region, api_key_env 等）
    """
    try:
        from llm_chat import ask_once

        backend = llm_config.get("provider", "bedrock")
        # 别名（如 bedrock_converse → bedrock）由 llm_chat backends 层统一处理

        return ask_once(
            prompt,
            backend=backend,
            model=llm_config.get("model", ""),
            region=llm_config.get("region", ""),
            api_key_env=llm_config.get("api_key_env", ""),
            api_key=llm_config.get("api_key", ""),
            base_url=llm_config.get("base_url", ""),
            endpoint=llm_config.get("endpoint", "chat"),
            max_tokens=llm_config.get("max_tokens", 0),
            timeout=llm_config.get("timeout", 0),
            max_retries=llm_config.get("max_retries", 0),
            retry_backoff=llm_config.get("retry_backoff", 0),
        )
    except Exception as e:
        log.warning(f"LLM 调用失败: {e}")
        return None


def _parse_json_response(response):
    """从 LLM 响应中解析 JSON 数组"""
    if not response:
        return None
    try:
        json_match = re.search(r"\[[\s\S]*\]", response)
        if json_match:
            return json.loads(json_match.group())
    except (json.JSONDecodeError, AttributeError) as e:
        log.warning(f"JSON 解析失败: {e}")
    return None


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
        parts.append(
            f"--- 第 {i} 对 ---\n"
            f"文档A [{src_a}] {loc_a}:\n{text_a}\n\n"
            f"文档B [{src_b}] {loc_b}:\n{text_b}"
        )
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
        with open(prompt_file, "r", encoding="utf-8") as f:
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
    response = _call_llm(prompt, llm_config)
    return _parse_json_response(response)


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


def _run_batches_concurrent(
    batches, llm_config, prompt_template, num_batches, concurrency
):
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
            log.info(
                f"    batch {batch_idx + 1}/{num_batches} ({len(batch)} pairs) submitted"
            )
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


def filter_diffs(diff_items, llm_config: dict = None, judge_config: dict = None):
    """
    一致性判断流水线

    Args:
        diff_items: 字级 diff 结果列表
        llm_config: LLM 配置字典。如果为 None，使用默认配置。
        judge_config: 判断配置字典。可选。

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
    for item, cat, reason in pre_classified:
        pre_counts[cat] = pre_counts.get(cat, 0) + 1

    log.info(f"  规则预过滤: {len(pre_classified)} 项排除 ({pre_counts})")
    log.info(f"  待 LLM 判断: {len(uncertain)} pairs")

    # ========== 阶段2：LLM 判断 ==========
    inconsistent_items = []

    if uncertain:
        # 动态计算 batch_size（根据 context_window 或使用显式配置）
        batch_size = _calculate_batch_size(llm_config, uncertain)
        concurrency = llm_config.get("concurrency", 1) or 1

        # 构建批次列表
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

        # 选择执行策略
        if concurrency > 1:
            batch_results = _run_batches_concurrent(
                batches, llm_config, prompt_template, num_batches, concurrency
            )
        else:
            batch_results = _run_batches_sequential(
                batches, llm_config, prompt_template, num_batches
            )

        # 处理结果
        for batch_idx, batch, results in batch_results:
            if not results:
                continue
            for r in results:
                # LLM 偶发返回非结构化条目（如裸 true/false 或字符串）；
                # 只处理 dict 类型，静默跳过其它形态。
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
                    # 直接填充结构化字段
                    item.llm_point = point
                    item.llm_doc_a_says = doc_a_says
                    item.llm_doc_b_says = doc_b_says
                    # 兼容性：同时填充 llm_reason
                    if point and doc_a_says and doc_b_says:
                        item.llm_reason = (
                            f"{point}：A称「{doc_a_says}」，B称「{doc_b_says}」"
                        )
                    elif point:
                        item.llm_reason = point
                    else:
                        item.llm_reason = _generate_specific_summary(item)
                        item.llm_point = item.llm_reason
                    item.__dict__["category"] = "inconsistency"
                    item.__dict__["category_label"] = "文档间不一致"
                    inconsistent_items.append(item)

    log.info(f"  ✅ 确认不一致: {len(inconsistent_items)} 处")
    log.info(
        f"  📊 总计: {len(diff_items)} 候选 → 规则排除 {len(pre_classified)} → LLM判断 {len(uncertain)} → 确认矛盾 {len(inconsistent_items)}"
    )

    return JudgeResult(
        inconsistent_items=inconsistent_items,
        rule_filtered=len(pre_classified),
        llm_judged=len(uncertain),
    )
