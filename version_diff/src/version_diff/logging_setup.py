"""统一日志（loguru）—— 极简。

各模块直接用 ``from loguru import logger as log`` 即可使用同一个全局 logger。
应用入口调用一次 :func:`setup_logging`，把日志写到统一文件
``~/.simple_rag/log/simple_rag.log``，同时保留 stderr 控制台输出（无颜色、同步写）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger

DEFAULT_LOG_PATH = Path("~/.simple_rag/log/simple_rag.log").expanduser()

_FMT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} | {message}"


def setup_logging(log_file: str | os.PathLike | None = None, level: str = "INFO") -> None:
    """初始化全局 loguru 日志：写统一文件 + stderr。每个进程调用一次即可。

    Args:
        log_file: 日志文件完整路径（缺省 ``~/.simple_rag/log/simple_rag.log``）
        level: 日志级别（缺省 INFO）
    """
    log_path = Path(log_file) if log_file else DEFAULT_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(str(log_path), level=level, encoding="utf-8", format=_FMT)
    logger.add(sys.stderr, level=level, format=_FMT)
