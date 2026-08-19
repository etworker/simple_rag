"""
配置管理 — 运行时可动态修改

支持:
  - LLM 模型/参数配置
  - Embedding 模型配置
  - 检索参数（top_k, threshold）
  - Prompt 模板
  - 预审核参数
"""

import json
import os
from copy import deepcopy
from typing import Any

# doc_parser 的默认解析配置（单一数据源，避免两处维护不一致）
from doc_parser.parser import DEFAULT_CONFIG as _PARSER_DEFAULT_CONFIG
from llm_chat.defaults import BEDROCK_API_KEY_ENV, DEFAULT_LLM_MODEL
from loguru import logger as log
from version_diff.paths import DEFAULT_EMBEDDING_MODEL

from app.paths import DEFAULT_CACHE_ROOT

# 默认配置
DEFAULT_CONFIG = {
    "cache": {
        "base_dir": DEFAULT_CACHE_ROOT,  # 解析优先级见 app.paths.resolve_cache_root
        "upload_dir": "",  # 空=默认 ~/.simple_rag/uploads/
    },
    "embedding": {
        "model": DEFAULT_EMBEDDING_MODEL,
        "cache_dir": "",
        # device: "auto" / "cpu" / "cuda" / "cuda:0" / "mps"
        "device": "auto",
        # dtype: "" / "auto" / "float16" / "bfloat16" / "float32"
        "dtype": "auto",
        # GPU device id (多 GPU 场景)
        "gpu_id": 0,
    },
    "llm_profiles": {
        "bedrock_glm_flash": {
            "provider": "bedrock",
            "model": DEFAULT_LLM_MODEL,
            "region": "us-east-1",
            "api_key_env": BEDROCK_API_KEY_ENV,
            "max_tokens": 2048,
            "timeout": 120,
            "max_retries": 3,
            "retry_backoff": 2.0,
            "context_window": 8192,
            "concurrency": 1,
        },
    },
    "llm_routing": {
        "qa": "bedrock_glm_flash",
        "pre_review": "bedrock_glm_flash",
        "conflict_detection": "bedrock_glm_flash",
        "parse_qa": "bedrock_glm_flash",
    },
    "retrieval": {
        "top_k": 5,
        "similarity_threshold": 0.5,
        "context_radius": 2,
    },
    "pre_review": {
        "similarity_threshold": 0.80,
        "version_similarity_threshold": 0.90,
        "parse_backend": "auto",
        "batch_size": 0,  # 0=按 context_window 自动计算
    },
    # 前端预审核页面布局偏好；不参与解析、审核或缓存配置计算
    "review_layout": "side-by-side",
    "parse_qa": {
        "enabled": False,
        # 默认复用预审核 LLM 路由；也可配置为 llm_routing 中的其他 use case。
        "llm_profile": "pre_review",
        "max_candidates": 20,
        "batch_size": 10,
        "max_retries": 2,
        "retry_backoff": 1.0,
    },
    "judge": {
        "prompt_file": "",  # 空=用随包默认, 设路径则加载自定义 prompt
        "prompt_template": "",  # 直接传字符串（优先级低于 prompt_file）
    },
    "conflict_detection": {
        "min_score": 0.7,
        "min_similarity": 0.5,
        "max_similarity": 0.95,
    },
    "prompts": {
        "system": (
            "你是一个企业文档问答助手。根据提供的参考资料回答用户问题。\n"
            "要求：\n"
            "1. 先直接回答用户问题，再补充必要的解释；仅基于参考资料，不要编造信息。\n"
            "2. 先综合所有相关资料，再组织成一份连贯、去重的答案；不要把每个参考片段逐条改写成列表。相同事实只表达一次，可在结论末尾合并多个引用编号。\n"
            "3. 流程、方法、规范类问题：按照资料明确的先后顺序或逻辑阶段整理（例如适用条件/触发场景→识别或执行→审批、记录、上报→后续处置）；资料没有明确顺序时不要自行补造顺序。\n"
            "4. 资料中有明确的条款、方法、条件或例外时，应完整归纳关键内容，不要只回答章节标题或转述‘见某表/某程序’。如果细节确实只在被引用的表单或附件中，需明确说明资料边界。\n"
            "5. 引用来源时使用简短编号，如 [1] [2]，对应参考资料列表中的编号，不要写出完整文件名或路径；只在对应事实后使用必要引用，不要单独罗列来源清单。\n"
            "6. 参考资料已以 [1] 文件名 | 位置\\n段落内容的形式给出，编号 [1] / [2] 对应底部来源编号 B1 / B2。\n"
            "7. 如果参考资料中有矛盾描述，必须醒目指出，并分别说明差异；不要自行选择或合并矛盾内容。\n"
            "8. 如果参考资料不足以回答，明确告知用户缺少什么信息；不要用常识补全。\n"
            "9. 对于关于知识库本身的元数据问题（如共几篇文档），根据参考资料的来源信息如实回答"
        ),
        "context_template": "[{idx}] {source} | {location}\n{text}\n",
        "conflict_warning": "\n⚠️ 注意：以下文档对此问题的描述存在矛盾：\n{conflicts}\n请以最新版本或上级文档为准。",
    },
    "chat": {
        "max_history": 20,
    },
    "extract": deepcopy(_PARSER_DEFAULT_CONFIG),
}

# 配置项描述（用于前端 UI 展示）
# key 与 DEFAULT_CONFIG 对应，前端可通过 /api/config/schema 获取
CONFIG_DESCRIPTIONS = {
    "extract.header_margin_pct": "页眉区域高度（占页面高度的百分比）。该区域内的文字会被识别为页眉并过滤掉，不进入正文。",
    "extract.footer_margin_pct": "页脚区域高度（占页面高度的百分比）。该区域内的文字会被识别为页脚并过滤掉，不进入正文。",
    "extract.repeat_line_threshold_pct": "重复行检测阈值（百分比）。如果某一行在超过此比例的页面中重复出现，则自动识别为页眉/页脚并过滤。例如设为30表示出现在30%以上页面中的行会被过滤。",
    "extract.min_paragraph_length": "最小段落长度（字符数）。短于此长度的段落会被丢弃，避免零散碎片干扰检索。",
    "extract.max_paragraph_length": "最大段落长度（字符数）。超过此长度的段落会在句号等标点处强制断开，避免单个段落过长影响向量检索精度。",
    "extract.chapter_patterns": "章节标题识别正则表达式列表（有序）。每条正则需包含两个捕获组：(编号, 标题文字)。系统按顺序尝试匹配，第一个命中的为准。",
    "extract.noise_line_patterns": "元数据行过滤正则表达式列表。匹配到的独立行会从正文流中剥离，避免修订日期、版本号等元数据信息混入正文段落。",
    "extract.margin_number_x": "左侧编号列分离阈值（PDF坐标点数，pt）。部分PDF将章节编号排在页面左侧margin区域，正文在右侧。设置此值后，x坐标小于该值且匹配编号模式的文字会被识别为编号，固定拼接到正文前面（如输出'1.1 标题文字'），确保章节识别稳定。设为0则禁用此功能。",
    "extract.margin_number_pattern": "编号列文字匹配正则表达式。仅当文字的x坐标小于 margin_number_x 且匹配此正则时，才被视为编号。默认匹配多级数字编号（1.1, 1.1.2.1）和单字母编号（A, B, C）。",
    "extract.table_empty_cell_threshold": "空白模板表格过滤阈值（0~1）。当表格中空单元格的比例超过此值时，该表格被视为空白模板（如签到表、申请表）并从结果中排除。设为1.0则禁用过滤，不排除任何表格。",
    "extract.table_empty_placeholders": "空单元格占位符列表。这些字符（如□、☐、○）与空白、None一起被计入空单元格数量，用于判断表格是否为空白模板。",
    "embedding.model": "文本向量化模型名称。用于将文档段落转为向量，支持语义检索。推荐中文场景使用 BAAI/bge 系列。",
    "embedding.device": "向量模型运行设备。auto=自动选择，cpu=仅CPU，cuda=GPU，mps=Mac GPU。",
    "embedding.dtype": "向量模型精度。auto=自动，float16=半精度（省显存），float32=全精度（更准）。",
    "retrieval.top_k": "检索时返回的最相关段落数量。增大可提高召回率但可能引入噪音。",
    "retrieval.context_radius": "每个命中段落额外带入的同文档前后相邻段落数量。用于补齐被拆分的流程上下文。",
    "retrieval.similarity_threshold": "检索相似度下限。低于此分数的段落不会返回给用户。",
    "pre_review.similarity_threshold": "版本对比时的段落配对相似度阈值。只有向量相似度超过此值的段落对才会进入差异比较流程。",
    "pre_review.version_similarity_threshold": "整篇文档相似度达到此阈值时走版本差异对比，否则走跨文档一致性检查。",
    "pre_review.batch_size": "版本对比时LLM批量调用大小。每批发送多少对差异给LLM判断。设为0表示根据模型上下文窗口自动计算。",
    "pre_review.parse_backend": "PDF 解析后端。auto=智能路由（扫描件→mineru，无框线表格→docling，数字文本→pymupdf）；pymupdf=快路径；docling=深度学习版面+表格（最准，GPU 加速）；mineru=中文扫描件 OCR；pdfplumber=轻量兜底。",
    "pre_review.docling_device": "docling 推理设备。auto=自动探测（有 GPU 用 cuda）；cuda=强制 GPU；cpu=强制 CPU。仅 parse_backend=docling 时生效。",
    "pre_review.docling_batch_size": "docling 推理 batch（0=默认 4）。layout/table/ocr 多页一起推理可提升 GPU 利用率；T4 建议 16-32，显存小或报 OOM 时调回 4。",
    "review_layout": "预审核页面布局。side-by-side=审核结果与文档预览左右并排；stacked=上下排列；single=仅显示单文档预览。该项保存后当前页面立即生效。",
    "judge.prompt_file": "跨文档 Judge Prompt 文件路径。设置后优先使用该文件；留空则使用下面的自定义模板或系统内置默认。",
    "judge.prompt_template": "跨文档一致性判断 Prompt 模板。必须保留 {count}（段落对数量）和 {items}（待判断内容）占位符；留空使用系统内置默认。该配置同时用于预审核跨文档矛盾检测和问答冲突检测。",
    "conflict_detection.min_score": "冲突检测最低置信度。LLM返回的冲突置信度低于此值时不报告为冲突。",
    "conflict_detection.min_similarity": "冲突检测段落配对最低相似度。只有超过此相似度的跨文档段落对才进入冲突检测。",
    "conflict_detection.max_similarity": "冲突检测段落配对最高相似度。超过此值的段落对被视为内容相同（非冲突），不进入检测。",
    "llm_routing.qa": "问答场景使用的LLM配置名称。对应 llm_profiles 中的某个 profile key。",
    "llm_routing.pre_review": "版本对比/预审核场景使用的LLM配置名称。",
    "llm_routing.conflict_detection": "冲突检测场景使用的LLM配置名称。",
    "llm_routing.parse_qa": "解析质量审查使用的LLM配置名称；当 parse_qa.llm_profile=parse_qa 时生效。",
    "parse_qa.enabled": "解析结果质量检查开关。默认关闭；开启后在预审核解析完成后执行规则检查和有限的 LLM 审查，不修改原始解析缓存。",
    "parse_qa.llm_profile": "解析质量 LLM 使用的路由 use case 名称。默认复用 pre_review，可配置为 llm_routing 中的其他路由。",
    "parse_qa.max_candidates": "每份文档最多发送给 LLM 的解析质量候选数，避免将整份 Markdown 放入上下文。",
    "parse_qa.batch_size": "解析质量 LLM 每批审查的候选数。",
    "parse_qa.max_retries": "解析质量 LLM 单批调用失败时的最大重试次数。",
    "parse_qa.retry_backoff": "解析质量 LLM 重试之间的等待秒数。",
    "chat.max_history": "对话历史保留轮数。超过此数量的历史消息会被截断，避免上下文过长。",
}


class ConfigStore:
    """运行时配置存储"""

    def __init__(self, config_path: str | None = None):
        self._config = deepcopy(DEFAULT_CONFIG)
        self._pending_config: dict | None = None
        self._config_path = config_path
        if config_path and os.path.exists(config_path):
            self.load(config_path)

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值，支持点号分隔的 key

        Example:
            config.get("llm.model")  → "zai.glm-4.7-flash"
            config.get("retrieval.top_k")  → 5
        """
        keys = key.split(".")
        val = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def set(self, key: str, value: Any):
        """设置配置值"""
        keys = key.split(".")
        d = self._config
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        log.info(f"配置更新: {key} = {value}")

    def get_section(self, section: str) -> dict:
        """获取整个配置段"""
        return deepcopy(self._config.get(section, {}))

    def get_llm_profile(self, use_case: str = "qa") -> dict:
        """
        获取指定用途的 LLM 配置

        复用 ``llm_chat.resolve_llm_profile`` 的查找逻辑（单一实现），
        仅保留 ConfigStore 特有的"use_case 未配置时默认指向 default profile"
        约定与 deepcopy 语义。

        Args:
            use_case: 用途标识 ("qa" | "pre_review" | "conflict_detection")

        Returns:
            完整的 LLM 配置字典（深拷贝，调用方修改不影响内部配置）

        Raises:
            KeyError: 如果 llm_profiles 为空
        """
        from llm_chat import resolve_llm_profile

        profiles = self._config.get("llm_profiles", {})
        routing = self._config.get("llm_routing", {})
        # ConfigStore 约定：routing 未配置的 use_case 默认指向 "default" profile
        resolved = {use_case: routing.get(use_case, "default")}
        cfg = resolve_llm_profile(profiles, resolved, use_case)
        return deepcopy(cfg)

    def to_dict(self) -> dict:
        """导出磁盘目标配置；存在待重启更新时返回待生效值。"""
        return deepcopy(self._pending_config or self._config)

    def stage_updates(self, updates: dict) -> dict:
        """原子保存待重启配置，但不修改当前进程正在使用的配置。"""
        pending = deepcopy(self._pending_config or self._config)
        self._deep_merge(pending, updates)
        path = self._config_path
        if not path:
            raise ValueError("未配置配置文件路径")

        tmp_path = f"{path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(pending, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        self._pending_config = pending
        log.info(f"待重启配置已保存: {path}")
        return deepcopy(pending)

    def update(self, updates: dict):
        """批量更新配置"""
        self._deep_merge(self._config, updates)

    def save(self, path: str | None = None):
        """原子保存配置到文件。"""
        path = path or self._config_path
        if path:
            tmp_path = f"{path}.tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self._config, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, path)
                log.info(f"配置已保存: {path}")
            except Exception:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise

    def load(self, path: str):
        """从文件加载配置"""
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        self._deep_merge(self._config, loaded)
        self._config_path = path
        log.info(f"配置已加载: {path}")

    @staticmethod
    def _deep_merge(base: dict, override: dict):
        """深度合并字典"""
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                ConfigStore._deep_merge(base[k], v)
            else:
                base[k] = v
