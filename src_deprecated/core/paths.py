# 00_src/core/paths.py
from __future__ import annotations
from pathlib import Path
from datetime import datetime
import re

ROOT = Path(__file__).resolve().parents[1]  # 00_src/
RESULT_DIR = ROOT / "data" / "results"
LOG_RUN_DIR = ROOT / "logs" / "run"

def slug(s: str) -> str:
    s = re.sub(r"\s+", "", s)  # 공백 제거 (파일명 간결)
    s = re.sub(r"[^\w\uac00-\ud7a3\-]", "", s)  # 한글/영문/숫자/_/-만
    return s

def now_kr_iso(compact: bool = True) -> str:
    # Asia/Seoul 기준 문자열만 필요하면 로컬시간 기준으로 찍어도 충분
    ts = datetime.now().strftime("%Y-%m-%dT%H%M%S" if compact else "%Y-%m-%dT%H:%M:%S")
    return ts

def dated_dir(base: Path) -> Path:
    d = datetime.now().strftime("%Y-%m-%d")
    p = base / d
    p.mkdir(parents=True, exist_ok=True)
    return p

def result_json_path(district: str, query: str) -> Path:
    dd = dated_dir(RESULT_DIR)
    fname = f"{slug(district)}_{slug(query)}_{now_kr_iso(compact=True)}.json"
    return dd / fname

def run_log_path() -> Path:
    dd = dated_dir(LOG_RUN_DIR)
    fname = f"run_{now_kr_iso(compact=True)}.log"
    return dd / fname