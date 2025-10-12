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


def _load_catalog_index(place: str) -> dict[str, Any]:
    """
    Load the catalog index YAML and retrieve information for the given place.
    
    Parameters:
    - place: The key to look up in the catalog_index.yaml (e.g., 'gangnam')
    
    Returns:
    - A dictionary containing the homepage URL, search_box selectors, and submit_btn selectors.
    
    Raises:
    - KeyError if the place is not found in the catalog index.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cfg_path = os.path.join(root, "configs", "catalog_index.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f: # what is f here? file object
        index = yaml.safe_load(f) # data type of index: dict[str, Any]
    key = place.lower()
    if key not in index:
        raise KeyError(f"place '{place}' not found in catalog_index.yaml")
    info = index[key] # data type of info: Any
    return info


def _prepare_output_paths(place: str) -> tuple[str, str, str]:
    """
    Prepare output directory and file paths for saving HTML and TEXT files.
    
    Parameters:
    - place: The place string used as part of the filename.
    
    Returns:
    - out_dir: The output directory path.
    - html_path: The full path to save the HTML file.
    - txt_path: The full path to save the TEXT file.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    today = str(datetime.date.today())
    out_dir = os.path.join(root, "data", "raw", today)
    os.makedirs(out_dir, exist_ok=True)
    base = f"{place}_{int(time.time())}"
    html_path = os.path.join(out_dir, f"{base}.html")
    txt_path = os.path.join(out_dir, f"{base}.txt")
    return out_dir, html_path, txt_path


# def _build_agent_task(homepage: str, search_box: list[str], submit_btn: list[str], title: str) -> str:
#     """
#     Construct the deterministic web agent task script for browsing and scraping.
    
#     Parameters:
#     - homepage: The homepage URL to navigate to.
#     - search_box: List of CSS selectors for search input boxes.
#     - submit_btn: List of CSS selectors for submit buttons.
#     - title: The search query string to input.
    
#     Returns:
#     - A string containing the multiline agent task instructions.
#     """
#     task = f"""
# You are a deterministic web agent. Do NOT take screenshots. Keep output short.
# Run each step EXACTLY once. Do NOT retry evaluate even if empty.

# 1) navigate: {homepage}

# 2) evaluate (find & submit once):
#    (function(){{
#      const inputs = {json.dumps(search_box)};
#      const buttons = {json.dumps(submit_btn)};
#      let usedInput = null, usedButton = null;
#      // input
#      for (const sel of inputs) {{
#        const el = document.querySelector(sel);
#        if (el) {{ el.focus(); el.value = {json.dumps(title)}; el.dispatchEvent(new Event('input',{{bubbles:true}})); usedInput = sel; break; }}
#      }}
#      // submit
#      for (const sel of buttons) {{
#        const el = document.querySelector(sel);
#        if (el) {{ try{{ el.click(); usedButton = sel; }}catch(_){{}} break; }}
#      }}
#      if (!usedButton && usedInput) {{
#        const el = document.querySelector(usedInput);
#        if (el) el.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter',bubbles:true}}));
#        usedButton = 'ENTER';
#      }}
#      return JSON.stringify({{input:usedInput, button:usedButton}});
#    }})()

# 3) evaluate HTML ONCE (no retry):
#    (function(){{
#      try {{
#        const html = document.documentElement ? document.documentElement.outerHTML : '';
#        return html ? html.slice(0, 10000) : '';
#      }} catch (e) {{
#        return 'ERROR:' + e.message;
#      }}
#    }})()

# 4) evaluate TEXT ONCE (no retry):
#    (function(){{
#      try {{
#        const t = document.body ? document.body.innerText : '';
#        return t ? t.slice(0, 10000) : '';
#      }} catch (e) {{
#        return 'ERROR:' + e.message;
#      }}
#    }})()

# 5) done: "ok"
# """.strip()
#     return task



def _run_agent(task: str, timeout_sec: int, logger) -> Any:
    """
    Run the Agent asynchronously with a timeout and return the history object.
    
    Parameters:
    - task: The agent task script string.
    - timeout_sec: Timeout in seconds for the agent run.
    - logger: Logger instance for logging progress and errors.
    
    Returns:
    - The history object returned by the agent.
    
    Raises:
    - Exception if the agent run fails or times out.
    """
    llm = llm_from_settings()
    agent = Agent(task=task, llm=llm)

    async def _run():
        logger.info("agent.run() start")
        h = await agent.run()
        logger.info("agent.run() end")
        return h

    try:
        history = asyncio.run(asyncio.wait_for(_run(), timeout=timeout_sec))
    except Exception as e:
        logger.error(f"agent error: {e}")
        raise
    return history



def _extract_candidates(history: Any) -> List[str]:
    """
    Extract string candidates from the agent's history object to find HTML and TEXT.
    """
    cands: List[str] = []
    if hasattr(history, "action_results"):
        cands = _flatten_strings(history.action_results())  # type: ignore
    if not cands and hasattr(history, "model_outputs"):
        cands = _flatten_strings(history.model_outputs())   # type: ignore
    if not cands and hasattr(history, "final_result"):
        cands = _flatten_strings(history.final_result())    # type: ignore
    return cands

# ---- 셀렉터 결과 로깅 함수 ---------------------------------
def _log_selector_result(history, logger):
    """
    history에서 {"input":..., "button":...} 형태의 JSON 문자열을 찾아 logger.info로 출력.
    """
    cands = _flatten_strings(history)
    for s in cands:
        if not isinstance(s, str):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict) and "input" in obj and "button" in obj:
            logger.info(f"[selector result] input: {obj['input']!r}, button: {obj['button']!r}")
            # 여러 개 있을 경우 첫 것만 로그
            break


def run_ingest_v2(place: str, title: str, timeout_sec: int = 180) -> None:
    """
    Main function to run the ingest process:
    - Load catalog index info for the place.
    - Prepare output paths.
    - Build agent task script.
    - Run the agent to get browsing history.
    - Extract HTML and TEXT from history and save them.
    
    Parameters:
    - place: catalog_index.yaml의 키 (예: 'gangnam')
    - title: 검색어 (예: '숨결이 바람 될 때')
    - timeout_sec: Agent 실행 타임아웃(초)
    """
    # Initialize logger and session ID
    session_id = uuid.uuid4().hex[:8]
    logger = get_logger(f"ingest_v2_{session_id}")
    logger.info(f"[SESSION {session_id}] start | place={place} title={title!r} timeout={timeout_sec}s")

    # Load catalog index information
    info = _load_catalog_index(place)
    homepage: str = info["homepage"]
    search_box: list[str] = info["search_box"]
    submit_btn: list[str] = info["submit_btn"]
    logger.info(f"homepage={homepage} box={search_box} btn={submit_btn}")

    # Prepare output file paths
    out_dir, html_path, txt_path = _prepare_output_paths(place)
    logger.info(f"→ will save HTML: {html_path}")
    logger.info(f"→ will save TEXT: {txt_path}")

    # Build agent task string
    task = _build_agent_task(homepage, search_box, submit_btn, title)

    # Run the agent and get history
    history = _run_agent(task, timeout_sec, logger)

    # Extract candidate strings from history
    cands = _extract_candidates(history)
    # Log selector result if available
    _log_selector_result(history, logger)

    # Pick HTML and TEXT from candidates
    html_val, text_val = _pick_html_text(cands)

    # Save HTML if exists
    if html_val:
        with open(html_path, "w", encoding="utf-8") as wf:
            wf.write(html_val)
        logger.info(f"saved HTML → {html_path}")
    else:
        logger.warning("no HTML captured")

    # Save TEXT if exists
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
    run_ingest_v2("songpa", "숨결이 바람 될 때", timeout_sec=180)