# 00_src/nodes/ingest_catalog_v2.py
from __future__ import annotations
# import sys, os
# sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
"""
ingest_catalog_v2: 최소 구현의 '수집(ingest)' 노드

- LangChain/LangGraph 미사용. browser_use.Agent + LLM 팩토리만 사용.
- 에이전트는: 카탈로그 홈 → 검색 → HTML/TEXT evaluate → done
- 파이썬이 에이전트 히스토리에서 문자열 후보를 모아 HTML/TEXT를 선택 저장.
- 과도한 예외처리 제거, 필수 경로/로그만 남김.
- 스크린샷/어댑티브 대기 없음. (빠르게 끝내는 목적)

입출력
- 입력: place(예: "gangnam"), title(예: "숨결이 바람 될 때")
- 출력: 00_src/data/raw/YYYY-MM-DD/{place}_{epoch}.html / .txt
"""

import os
import sys
import json
import time
import uuid
import yaml
import asyncio
import datetime
from typing import Any, List, Optional

from dotenv import load_dotenv
from browser_use import Agent
# LLM은 팩토리로 교체 가능 (환경변수/설정으로 모델 스위치)
from core.llm_factory import llm_from_settings
from core.logger import get_logger  # 세션별 로그 파일 생성

load_dotenv()

# ---- 유틸: 문자열 펼치기 / HTML·TEXT 고르기 ---------------------------------
def _flatten_strings(x: Any, out: Optional[List[str]] = None) -> List[str]:
    """중첩 자료구조에서 문자열만 추출."""
    if out is None:
        out = []
    if x is None:
        return out
    if isinstance(x, str):
        out.append(x)
        return out
    if isinstance(x, (list, tuple, set)):
        for v in x:
            _flatten_strings(v, out)
        return out
    if isinstance(x, dict):
        for v in x.values():
            _flatten_strings(v, out)
        return out
    # pydantic/임의 객체 최소 대응
    if hasattr(x, "dict"):
        try:
            _flatten_strings(x.dict(), out)  # type: ignore
            return out
        except Exception:
            pass
    if hasattr(x, "__dict__"):
        try:
            _flatten_strings(vars(x), out)
        except Exception:
            pass
    return out


def _pick_html_text(cands: List[str]) -> tuple[Optional[str], Optional[str]]:
    """후보 중 HTML(‘<html’)과 TEXT(그 외) 최장 문자열 각각 선택."""
    html_val, text_val = None, None
    maxh, maxt = -1, -1
    for s in cands:
        sc = s.lstrip()
        if "<html" in sc.lower():
            if len(s) > maxh:
                html_val, maxh = s, len(s)
        else:
            if len(s) > maxt:
                text_val, maxt = s, len(s)
    return html_val, text_val


# ---- 본 로직 ---------------------------------------------------------------
def run_ingest_v2(place: str, title: str, timeout_sec: int = 180) -> None:
    """
    카탈로그 인덱스에 진입하여 검색 후, 결과 페이지의 HTML/TEXT를 저장한다.
    - place: catalog_index.yaml의 키 (예: 'gangnam')
    - title: 검색어 (예: '숨결이 바람 될 때')
    - timeout_sec: Agent 실행 타임아웃(초)
    """
    # 로거 & 세션
    session_id = uuid.uuid4().hex[:8]
    logger = get_logger(f"ingest_v2_{session_id}")
    logger.info(f"[SESSION {session_id}] start | place={place} title={title!r} timeout={timeout_sec}s")

    # 설정 로드 (catalog_index.yaml)
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cfg_path = os.path.join(root, "configs", "catalog_index.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        index = yaml.safe_load(f)
    key = place.lower()
    if key not in index:
        raise KeyError(f"place '{place}' not found in catalog_index.yaml")
    info = index[key]
    homepage: str = info["homepage"]
    search_box: list[str] = info["search_box"]
    submit_btn: list[str] = info["submit_btn"]
    logger.info(f"homepage={homepage} box={search_box} btn={submit_btn}")

    # 저장 경로
    today = str(datetime.date.today())
    out_dir = os.path.join(root, "data", "raw", today)
    os.makedirs(out_dir, exist_ok=True)
    base = f"{place}_{int(time.time())}"
    html_path = os.path.join(out_dir, f"{base}.html")
    txt_path = os.path.join(out_dir, f"{base}.txt")
    logger.info(f"→ will save HTML: {html_path}")
    logger.info(f"→ will save TEXT: {txt_path}")

    # 에이전트 태스크 (간결)
    llm = llm_from_settings()
    task = f"""
You are a deterministic web agent. No screenshots. Keep output short.

1) navigate: {homepage}
2) input: find a search input among {search_box}; type EXACTLY: {title}
3) submit: click the first existing button among {submit_btn}; if click fails, press Enter on the input; if still fails, run:
   evaluate:
     document.querySelector('{submit_btn[0]}')?.click();
     document.querySelector('{submit_btn[-1]}')?.click();
4) evaluate HTML:
   evaluate: (function(){{try{{return document.documentElement?document.documentElement.outerHTML:'';}}catch(e){{return 'ERROR:'+e.message;}}}})()
5) evaluate TEXT:
   evaluate: (function(){{try{{return document.body?document.body.innerText:'';}}catch(e){{return 'ERROR:'+e.message;}}}})()
6) done: "ok"
""".strip()

    agent = Agent(task=task, llm=llm)

    async def _run():
        logger.info("agent.run() start")
        h = await agent.run()
        logger.info("agent.run() end")
        return h

    # 실행 (간단 타임아웃)
    try:
        history = asyncio.run(asyncio.wait_for(_run(), timeout=timeout_sec))
    except Exception as e:
        logger.error(f"agent error: {e}")
        raise

    # 히스토리에서 문자열 모으기 (필요 최소만)
    cands: List[str] = []
    if hasattr(history, "action_results"):
        cands = _flatten_strings(history.action_results())  # type: ignore
    # 보강
    if not cands and hasattr(history, "model_outputs"):
        cands = _flatten_strings(history.model_outputs())   # type: ignore
    if not cands and hasattr(history, "final_result"):
        cands = _flatten_strings(history.final_result())    # type: ignore

    html_val, text_val = _pick_html_text(cands)
    if html_val:
        with open(html_path, "w", encoding="utf-8") as wf:
            wf.write(html_val)
        logger.info(f"saved HTML → {html_path}")
    else:
        logger.warning("no HTML captured")

    if text_val:
        with open(txt_path, "w", encoding="utf-8") as wf:
            wf.write(text_val)
        logger.info(f"saved TEXT → {txt_path}")
    else:
        logger.warning("no TEXT captured")

    ok = os.path.exists(html_path) and os.path.exists(txt_path)
    logger.info(f"done: ok={ok} dir={out_dir}")

if __name__ == "__main__":
    # 예시 실행
    run_ingest_v2("gangnam", "숨결이 바람 될 때", timeout_sec=180)