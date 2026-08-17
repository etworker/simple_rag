"""doc_parser 的 opt-in 日志配置，供 examples / 独立使用。

应用（rag_server）请在自己的入口统一配置，不要调用本函数；库内部也不会自动配置。
用法（example 脚本开头）:
    from doc_parser.log import configure_logger
    configure_logger()

file 路径默认 ``./logs/doc_parser.log``，可用环境变量 ``DOC_PARSER_LOG_FILE`` 覆盖。
"""

from __future__ import annotations

import os
import sys

from loguru import logger

_LIB = "doc_parser"
_FMT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}"


def configure_logger(level: str = "INFO") -> None:
    """配置 console + file 两个 sink。

    - file 路径：环境变量 ``DOC_PARSER_LOG_FILE``，缺省 ``./logs/doc_parser.log``
    - 同时 ``logger.enable("doc_parser")`` 打开本库日志（库默认通过 disable 关闭）
    """
    logger.remove()
    logger.add(sys.stderr, level=level, format=_FMT)
    default_file = os.path.join("logs", f"{_LIB}.log")
    file_path = os.environ.get(f"{_LIB.upper()}_LOG_FILE", default_file)
    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    logger.add(file_path, level=level, rotation="10 MB", retention=3, encoding="utf-8", format=_FMT)
    logger.enable(_LIB)
