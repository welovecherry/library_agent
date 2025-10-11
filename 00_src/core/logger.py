"""
logger.py
---
중앙 로깅 설정. 파일(logs/graph.log)과 콘솔에 동시에 남긴다.
회전 로그(RotatingFileHandler)로 용량을 관리한다.
"""

from __future__ import annotations
import logging, os
from logging.handlers import RotatingFileHandler

def get_logger(
    name: str = "agent",
    log_dir: str = "00_src/logs",
    level: int = logging.INFO,
) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        # 이미 초기화된 로거 재사용
        return logger

    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s - %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "graph.log"),
        maxBytes=2_000_000,  # 2MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(stream)

    return logger