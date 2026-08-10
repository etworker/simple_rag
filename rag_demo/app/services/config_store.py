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
import logging
import os
from copy import deepcopy
from typing import Any

log = logging.getLogger("rag_demo.config")

# 默认缓存根目录
_DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".simple_rag")

# 默认配置
DEFAULT_CONFIG = {
    "cache": {
        "base_dir": _DEFAULT_CACHE_DIR,
        "upload_dir": "",  # 空=默认 ~/.simple_rag/uploads/
    },
    "embedding": {
        "model": "BAAI/bge-small-zh-v1.5",
        "cache_dir": "",
        # device: "auto" / "cpu" / "cuda" / "cuda:0" / "mps"
        "device": "auto",
        # dtype: "" / "auto" / "float16" / "bfloat16" / "float32"
        "dtype": "auto",
        # GPU device id (多 GPU 场景)
        "gpu_id": 0,
    },
    "llm_profiles": {},
    "llm_routing": {
        "qa": "default",
        "pre_review": "default",
        "conflict_detection": "default",
    },
    "retrieval": {
        "top_k": 5,
        "similarity_threshold": 0.5,
    },
    "pre_review": {
        "similarity_threshold": 0.80,
        "batch_size": 0,  # 0=按 context_window 自动计算
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
            "1. 仅基于参考资料回答，不要编造信息\n"
            "2. 引用来源时使用简短编号，如 [1] [2]，对应参考资料列表中的编号，不要写出完整文件名或路径\n"
            "3. 参考资料已以 [1] 文件名 | 位置\\n段落内容 的形式给出，编号 [1] / [2] 对应底部来源编号 B1 / B2\n"
            "4. 如果参考资料中有矛盾描述，必须醒目指出\n"
            "5. 如果参考资料不足以回答，明确告知用户\n"
            "6. 对于关于知识库本身的元数据问题（如共几篇文档），根据参考资料的来源信息如实回答"
        ),
        "context_template": "[{idx}] {source} | {location}\n{text}\n",
        "conflict_warning": "\n⚠️ 注意：以下文档对此问题的描述存在矛盾：\n{conflicts}\n请以最新版本或上级文档为准。",
    },
    "chat": {
        "max_history": 20,
    },
    "extract": {
        "header_margin_pct": 8,
        "footer_margin_pct": 8,
        "repeat_line_threshold_pct": 30,
        "min_paragraph_length": 10,
        "max_paragraph_length": 600,
        "margin_number_x": 130,
        "margin_number_pattern": r"^(?:\d+(?:\.\d+)*|[A-Z])$",
        "table_empty_cell_threshold": 0.6,
        "table_empty_placeholders": ["□", "☐", "○", "——"],
        "chapter_patterns": [
            r"^(\d+\.\d+\.\d+)\s+(.+)",
            r"^(\d+\.\d+)\s+(.+)",
            r"^(\d+)\s+(.+)",
            r"^第\s*(\d+)\s*[章节]\s*(.+)",
        ],
        "noise_line_patterns": [
            r"^修订日期\s*[：:]\s*\S+$",
            r"^发布日期\s*[：:]\s*\S+$",
            r"^\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}$",
            r"^修订次数\s*[：:]\s*\d+\s+\S+\s+页码",
            r"修订日期\s*[：:]\s*\d{4}-\d{1,2}-\d{1,2}\s*$",
        ],
    },
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
    "extract.noise_patterns": "正文段落过滤正则表达式列表。匹配到的段落会被丢弃（如纯空行）。",
    "extract.noise_line_patterns": "元数据行过滤正则表达式列表。匹配到的独立行会从正文流中剥离，避免修订日期、版本号等元数据信息混入正文段落。",
    "extract.margin_number_x": "左侧编号列分离阈值（PDF坐标点数，pt）。部分PDF将章节编号排在页面左侧margin区域，正文在右侧。设置此值后，x坐标小于该值且匹配编号模式的文字会被识别为编号，固定拼接到正文前面（如输出'1.1 标题文字'），确保章节识别稳定。设为0则禁用此功能。",
    "extract.margin_number_pattern": "编号列文字匹配正则表达式。仅当文字的x坐标小于 margin_number_x 且匹配此正则时，才被视为编号。默认匹配多级数字编号（1.1, 1.1.2.1）和单字母编号（A, B, C）。",
    "extract.table_empty_cell_threshold": "空白模板表格过滤阈值（0~1）。当表格中空单元格的比例超过此值时，该表格被视为空白模板（如签到表、申请表）并从结果中排除。设为1.0则禁用过滤，不排除任何表格。",
    "extract.table_empty_placeholders": "空单元格占位符列表。这些字符（如□、☐、○）与空白、None一起被计入空单元格数量，用于判断表格是否为空白模板。",
    "embedding.model": "文本向量化模型名称。用于将文档段落转为向量，支持语义检索。推荐中文场景使用 BAAI/bge 系列。",
    "embedding.device": "向量模型运行设备。auto=自动选择，cpu=仅CPU，cuda=GPU，mps=Mac GPU。",
    "embedding.dtype": "向量模型精度。auto=自动，float16=半精度（省显存），float32=全精度（更准）。",
    "retrieval.top_k": "检索时返回的最相关段落数量。增大可提高召回率但可能引入噪音。",
    "retrieval.similarity_threshold": "检索相似度下限。低于此分数的段落不会返回给用户。",
    "pre_review.similarity_threshold": "版本对比时的段落配对相似度阈值。只有向量相似度超过此值的段落对才会进入差异比较流程。",
    "pre_review.batch_size": "版本对比时LLM批量调用大小。每批发送多少对差异给LLM判断。设为0表示根据模型上下文窗口自动计算。",
    "conflict_detection.min_score": "冲突检测最低置信度。LLM返回的冲突置信度低于此值时不报告为冲突。",
    "conflict_detection.min_similarity": "冲突检测段落配对最低相似度。只有超过此相似度的跨文档段落对才进入冲突检测。",
    "conflict_detection.max_similarity": "冲突检测段落配对最高相似度。超过此值的段落对被视为内容相同（非冲突），不进入检测。",
    "llm_routing.qa": "问答场景使用的LLM配置名称。对应 llm_profiles 中的某个 profile key。",
    "llm_routing.pre_review": "版本对比/预审核场景使用的LLM配置名称。",
    "llm_routing.conflict_detection": "冲突检测场景使用的LLM配置名称。",
    "chat.max_history": "对话历史保留轮数。超过此数量的历史消息会被截断，避免上下文过长。",
}


class ConfigStore:
    """运行时配置存储"""

    def __init__(self, config_path: str | None = None):
        self._config = deepcopy(DEFAULT_CONFIG)
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

        Args:
            use_case: 用途标识 ("qa" | "pre_review" | "conflict_detection")

        Returns:
            完整的 LLM 配置字典

        Example:
            config.get_llm_profile("qa")         → self_hosted_glm 的配置
            config.get_llm_profile("pre_review") → bedrock_kimi_thinking 的配置

        Raises:
            KeyError: 如果 routing 指向的 profile 不存在
        """
        profiles = self._config.get("llm_profiles", {})
        routing = self._config.get("llm_routing", {})

        profile_name = routing.get(use_case, "default")

        if profile_name in profiles:
            return deepcopy(profiles[profile_name])

        # fallback: 第一个 profile
        if profiles:
            first_key = next(iter(profiles))
            return deepcopy(profiles[first_key])

        raise KeyError(f"未配置 LLM profile（llm_profiles 为空，use_case='{use_case}'）")

    def to_dict(self) -> dict:
        """导出完整配置"""
        return deepcopy(self._config)

    def update(self, updates: dict):
        """批量更新配置"""
        self._deep_merge(self._config, updates)

    def save(self, path: str | None = None):
        """保存配置到文件"""
        path = path or self._config_path
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            log.info(f"配置已保存: {path}")

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
