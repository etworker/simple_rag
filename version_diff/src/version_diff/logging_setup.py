"""
统一日志配置（loguru）。

各模块（doc_parser / llm_chat / version_diff / rag_demo / demo）统一用 loguru 记录日志，
默认写到同一个目录同一个文件：``~/.cache/simple_rag/log/simple_rag.log``。

用法（在应用入口调用一次，全局生效）：
    from version_diff.logging_setup import setup_logging
    setup_logging()   # 或 setup_logging(log_dir=..., log_file=..., level=...)

说明：
    - loguru 的默认 logger 是全局单例，各模块 ``from loguru import logger`` 拿到同一个；
      顶层调用一次 setup_logging 后，所有模块日志都进同一文件。
    - 幂等：重复调用不会重复添加文件 sink。
    - 可传入自定义 sink（如 WebSocket 实时推送），供 rag_demo 使用。
    - InterceptHandler 会把第三方库（httpx/uvicorn 等）的标准 logging 日志也汇入统一文件。
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from typing import Any

from loguru import logger

_DEFAULT_LOG_DIR = os.path.expanduser("~/.cache/simple_rag/log")
_DEFAULT_LOG_FILE = "simple_rag.log"

# 已添加过的文件 sink（避免重复）
_added_files: set[str] = set()
# stderr sink 是否已添加
_stderr_added = False
# InterceptHandler 是否已挂到根 logging logger
_intercept_installed = False


class InterceptHandler(logging.Handler):
    """把标准 logging 的日志重定向到 loguru（官方推荐的做法）。

    使第三方库（httpx / uvicorn / pdfminer 等）的日志也汇入统一的 loguru 输出。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _install_intercept() -> None:
    """把标准 logging 的根 logger 重定向到 loguru（幂等）。"""
    global _intercept_installed
    if _intercept_installed:
        return
    handler = InterceptHandler()
    logging.basicConfig(handlers=[handler], level=0, force=True)
    logging.getLogger().addHandler(handler)
    _intercept_installed = True


def set_stdlib_level(logger_name: str, level: str) -> None:
    """设置某个标准 logging logger 的日志级别（如关闭过吵的第三方库）。"""
    logging.getLogger(logger_name).setLevel(level.upper())


def setup_logging(
    log_dir: str | None = None,
    log_file: str | None = None,
    level: str = "INFO",
    rotation: str = "20 MB",
    extra_sink: Callable[[str], None] | None = None,
    **sink_kwargs: Any,
) -> Any:
    """初始化全局 loguru 日志，默认写统一文件。

    Args:
        log_dir: 日志目录（缺省 ~/.cache/simple_rag/log）
        log_file: 日志文件名（缺省 simple_rag.log）
        level: 日志级别（缺省 INFO）
        rotation: 文件轮转（缺省 20 MB）
        extra_sink: 额外 sink（如 WebSocket 实时推送），接收格式化后的日志行
        **sink_kwargs: 透传给 logger.add 的其他参数

    Returns:
        loguru logger（全局单例）
    """
    log_dir = log_dir or _DEFAULT_LOG_DIR
    log_file = log_file or _DEFAULT_LOG_FILE
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, log_file)

    fmt = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} | {message}"

    # 文件 sink（幂等去重）
    if log_path not in _added_files:
        logger.add(
            log_path,
            level=level,
            rotation=rotation,
            encoding="utf-8",
            enqueue=True,
            format=fmt,
            backtrace=False,
            **sink_kwargs,
        )
        _added_files.add(log_path)

    # stderr sink（幂等去重）
    global _stderr_added
    if not _stderr_added:
        logger.add(
            sys.stderr,
            level=level,
            format=fmt,
            colorize=True,
        )
        _stderr_added = True

    # 额外 sink（WebSocket 推送等）
    if extra_sink is not None:
        logger.add(
            extra_sink,
            level=level,
            format=fmt,
        )

    # 拦截标准 logging → 统一汇入 loguru（第三方库日志也进同一文件）
    _install_intercept()

    return logger


def default_log_path() -> str:
    """返回默认日志文件完整路径。"""
    return os.path.join(_DEFAULT_LOG_DIR, _DEFAULT_LOG_FILE)
