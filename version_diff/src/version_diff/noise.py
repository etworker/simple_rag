"""
跨文档内容差异的噪声过滤机制（内置、可配置）。

背景：
    用 version_compare 对比两份「不同级别/不同体例」的文档（如《二级…管理手册》与
    《三级…工作手册》）时，二者目录、记录清单、页码占位等「版式」内容往往整体不同，
    会产生大量与实质内容无关的差异噪声。

本模块提供通用的噪声判定器 `CrossNoiseFilter`：
    - 内置通用噪声模式（目录条目 / 记录清单 / 页码占位 / 修订说明 / 过短文本），非行业词；
    - 超参数（正则列表、长度阈值、开关）全部可经配置注入，调用方按需覆盖。

用法：
    config = {"enabled": True, "patterns": [...], "min_length": 6, "dir_entry_max_length": 40}
    nf = CrossNoiseFilter(config)
    real, noise = nf.filter_changes(engine.version_compare(a, b).changes)
"""

from __future__ import annotations

import re

# 通用噪声模式（非行业词）：目录条目 / 记录清单 / 页码占位 / 修订说明
DEFAULT_NOISE_PATTERNS = [
    # 手册记录清单
    r"^\d+\.\d*\s*手册记录清单\s*$",
    # 目录行: "1.1 信息化管理内容 8 页"
    r"^\d+\.\d*\s*[\u4e00-\u9fa5A-Za-z0-9 ]+\s+\d+-\d+\s+\d+\s*页\s*$",
    # 目录条目: "1 组织机构及职责 35 页"
    r"^\d+(?:\.\d+)*\s+[\u4e00-\u9fa5A-Za-z ]+\s+\d+\s*页\s*$",
    # 目录条目带编号: "1.1 信息技术部职责 1.1-1"
    r"^\d+(?:\.\d+)*\s+[\u4e00-\u9fa5A-Za-z ]+\s+\d+\.\d+-\d+\s*$",
    # 附录编号 / 附录目录
    r"^附录\s+\d+-\d+\s*$",
    r"^\d+\s+附录\s+[\u4e00-\u9fa5A-Za-z0-9 ]+[\u4e00-\u9fa5]+\s+附录\s+\d+-\d+\s*$",
    # "X-X 章节目录 页码"
    r"^\d+-\d+\s+[\u4e00-\u9fa5A-Za-z0-9 ]+\s+[\u4e00-\u9fa5]+.*\d+\s*页\s*$",
    # 修订说明
    r"^[A-Z]\s+根据公司下发的各类规范性或程序性文件.*$",
]

# 目录条目启发式：以章节编号开头
_DIR_ENTRY_START_RE = re.compile(r"^\d+(?:\.\d+)*\s+\S")


class CrossNoiseFilter:
    """可配置的跨文档噪声判定器。

    超参数（经配置注入，调用方可覆盖默认值）：
        enabled (bool):              是否启用，默认 True
        patterns (list[str]):        整行匹配即判为噪声的正则列表，默认 DEFAULT_NOISE_PATTERNS
        min_length (int):            短于该长度且无实质内容视为噪声，默认 6
        dir_entry_max_length (int):  目录条目启发式的最大长度，默认 40
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.min_length = int(cfg.get("min_length", 6))
        self.dir_entry_max_length = int(cfg.get("dir_entry_max_length", 40))
        patterns = cfg.get("patterns", DEFAULT_NOISE_PATTERNS)
        self._patterns = [re.compile(p) for p in patterns]

    # ------------------------------------------------------------------
    def is_noise(self, text: str) -> bool:
        """判断一段文本是否属版式噪声（目录/记录清单/页码占位/过短）。"""
        if not text:
            return True
        stripped = text.strip()
        if len(stripped) < self.min_length:
            return True
        for pat in self._patterns:
            if pat.match(stripped):
                return True
        # 目录条目启发式：章节编号开头 + 含页码「N 页」或目录编号「x.y-z」 + 短文本
        return (
            len(stripped) < self.dir_entry_max_length
            and _DIR_ENTRY_START_RE.match(stripped) is not None
            and ("页" in stripped or re.search(r"\d+\.\d+-\d+", stripped) is not None)
        )

    def is_noise_change(self, change) -> bool:
        """一条 change 是否属噪声：其「实质文本」（新增看 new、删除看 old）全为噪声。"""
        if change.change_type == "added":
            return self.is_noise(change.new_text or "")
        if change.change_type == "removed":
            return self.is_noise(change.old_text or "")
        # modified：旧、新都看，只要有一个是实质内容就保留
        return self.is_noise(change.old_text or "") and self.is_noise(change.new_text or "")

    def filter_changes(self, changes) -> tuple[list, list]:
        """过滤噪声差异，返回 (实质差异, 噪声差异)。"""
        if not self.enabled:
            return list(changes), []
        real, noise = [], []
        for c in changes:
            (noise if self.is_noise_change(c) else real).append(c)
        return real, noise
