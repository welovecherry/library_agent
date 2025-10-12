from __future__ import annotations
"""
ingest_catalog node: navigate catalog index, search, then SAVE LOCALLY (no agent-side write_file).

▶ 변경사항(NEW):
- 에이전트는 오직 페이지 로딩/검색/평가(evaluate)만 수행.
- HTML(outerHTML) / TEXT(innerText)는 에이전트의 evaluate 결과에서 회수.
- 파이썬에서 프로젝트 내 경로로 직접 저장 (임시폴더 이탈).
- ✅ NEW: 히스토리에서 문자열을 회수할 때 action_results() 외에도
          extracted_content(), model_outputs(), action_history(), final_result()까지 폴백.
- ✅ NEW: 이번 세션 로그 파일 경로를 시작 시점에 출력.
- ✅ NEW: 저장 경로를 로그에 항상 전체 경로로 남김.

안전성과 비용을 위해 스크린샷/무한 저장 루프/임시 위치 저장을 모두 차단한다.
"""
import os
import sys
import json
import time
import uuid
import yaml
import asyncio
import logging
import datetime
from typing import Any, Iterable, List, Optional
from contextlib import contextmanager
from browser_use import Agent, ChatOpenAI
from dotenv import load_dotenv

# ── logger import (패키지 경로 문제가 있을 수 있어, 안전하게 sys.path 보강)
_THIS_DIR = os.path.dirname(__file__)
_PKG_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))  # 00_src/
if _PKG_ROOT not in sys.path:
    sys.path.append(_PKG_ROOT)
from core.logger import get_logger  # 00_src/core/logger.py

load_dotenv()

# ── 스텝 타이머 컨텍스트: 시작/종료/예외를 모두 파일 로그에 남긴다
@contextmanager
def step(logger: logging.Logger, name: str):
    t0 = time.time()
    logger.info(f"STEP START: {name}")
    try:
        yield
        ms = (time.time() - t0) * 1000
        logger.info(f"STEP END:   {name} ({ms:.0f} ms)")
    except Exception:
        ms = (time.time() - t0) * 1000
        logger.exception(f"STEP ERROR: {name} after {ms:.0f} ms")
        raise


def _flatten_strings(x: Any) -> List[str]:
    """✅ NEW: 임의의 중첩 구조에서 문자열만 납작하게 수집."""
    out: List[str] = []
    if x is None:
        return out
    if isinstance(x, str):
        out.append(x)
        return out
    if isinstance(x, (list, tuple, set)):
        for v in x:
            out.extend(_flatten_strings(v))
        return out
    if isinstance(x, dict):
        for v in x.values():
            out.extend(_flatten_strings(v))
        return out
    # pydantic 객체나 임의 객체일 수 있음 → dict로 시도
    try:
        d = x.dict()  # pydantic BaseModel
        out.extend(_flatten_strings(d))
    except Exception:
        # asdict/fallback
        try:
            out.extend(_flatten_strings(vars(x)))
        except Exception:
            pass
    return out


def _pick_html_text(candidates: Iterable[str]) -> tuple[Optional[str], Optional[str]]:
    """✅ NEW: 문자열들 중에서 HTML/텍스트 1개씩 고른다.
    - HTML: '<html' 포함(대/소문자 무시), 길이 가장 긴 것
    - TEXT: '<html' 미포함, 길이 가장 긴 것
    """
    html_best = None
    text_best = None
    maxh = -1
    maxt = -1
    for s in candidates:
        if not isinstance(s, str):
            continue
        s_clean = s.lstrip()
        if "<html" in s_clean.lower():
            if len(s) > maxh:
                html_best = s
                maxh = len(s)
        else:
            if len(s) > maxt:
                text_best = s
                maxt = len(s)
    return html_best, text_best


def run_ingest(place: str, title: str, watchdog_sec: int = 300) -> None:
    """
    정적 인덱스에서 카탈로그로 진입 → 검색 → 결과 페이지 HTML/텍스트 저장.
    어댑티브 대기 및 세션별 로그파일을 적용하고 스크린샷은 억제.

    임시 ASCII-safe 파일명으로 에이전트가 파일을 쓰고, 종료 후 최종 경로로 이동함.

    Args:
        place: 예) "gangnam" (catalog_index.yaml의 키)
        title: 예) "숨결이 바람 될 때"
        watchdog_sec: 에이전트 최대 실행 시간(초). 초과 시 취소/로그 후 종료.
    """
    session_id = uuid.uuid4().hex[:8]
    logger = get_logger(f"ingest_{session_id}")
    # ✅ NEW: 이번 세션 로그 파일 경로 안내
    logger.info(f"[SESSION {session_id}] log file → 00_src/logs/graph-{logger.name}.log")
    logger.info(f"[SESSION {session_id}] run_ingest start | place={place} title={title!r} watchdog={watchdog_sec}s")

    # 1) 설정 로드: load config file and validate place key
    with step(logger, "load_config"):
        cfg_path = os.path.join(_PKG_ROOT, "configs", "catalog_index.yaml")
        with open(cfg_path, "r", encoding="utf-8") as f:
            index = yaml.safe_load(f)
        key = place.lower()
        if key not in index:
            raise KeyError(f"place '{place}' not found in catalog_index.yaml keys={list(index.keys())}")
        info = index[key]
        homepage: str = info["homepage"]
        box_selectors: list[str] = info["search_box"]
        btn_selectors: list[str] = info["submit_btn"]
        logger.info(f"homepage={homepage} box={box_selectors} btn={btn_selectors}")

    # 2) 저장 경로 준비 (파일명 안전하게 변경)
    with step(logger, "prepare_paths"):
        today = str(datetime.date.today())
        date_dir = os.path.join(_PKG_ROOT, "data", "raw", today)
        os.makedirs(date_dir, exist_ok=True)
        safe_base = f"{place}_{int(time.time())}"  # ✅ NEW: 파일명 ASCII-safe
        html_path = os.path.join(date_dir, f"{safe_base}.html")
        text_path = os.path.join(date_dir, f"{safe_base}.txt")
        # ✅ NEW: 경로를 명시적으로 로그
        logger.info(f"output_html={html_path}")
        logger.info(f"output_text={text_path}")

    # 3) 로컬 browser_use Agent 실행 + watchdog (스크린샷 억제, 어댑티브 대기 적용)
    with step(logger, "build_task"):
        llm = ChatOpenAI(model="gpt-5")  # 짧은 지시로 비용 최소화

        task_text = f"""
You are a deterministic web agent. Do NOT take screenshots. Keep output short.

STEPS
1) Navigate to {homepage}
2) Find a search input; try in order: {box_selectors}
3) Type EXACTLY: {title}
4) Find a submit button; try in order: {btn_selectors}
   - If clicking fails, focus input and press Enter.
   - If still failing, run JS:
     evaluate:
       document.querySelector('{btn_selectors[0]}')?.click();
       document.querySelector('{btn_selectors[-1]}')?.click();

5) Evaluate HTML (exact string return):
   evaluate: (function(){{try{{return document.documentElement?document.documentElement.outerHTML:'';}}catch(e){{return 'ERROR:'+e.message;}}}})()

6) Evaluate TEXT (exact string return):
   evaluate: (function(){{try{{return document.body?document.body.innerText:'';}}catch(e){{return 'ERROR:'+e.message;}}}})()

7) Immediately call done with a one-line summary like "ok".
"""
        agent = Agent(task=task_text, llm=llm)

    async def _run_agent():
        logger.info("agent.run() start")
        h = await agent.run()
        logger.info("agent.run() end")
        return h

    history = None
    # 4) 에이전트 실행 및 watchdog 적용, 비동기 처리
    with step(logger, f"agent_run.watchdog({watchdog_sec}s)"):
        try:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(_run_agent())
                history = loop.run_until_complete(asyncio.wait_for(task, timeout=watchdog_sec))
            except RuntimeError:
                history = asyncio.run(asyncio.wait_for(_run_agent(), timeout=watchdog_sec))
        except asyncio.TimeoutError:
            logger.error(f"watchdog timeout ({watchdog_sec}s) → agent task cancelled")
        except KeyboardInterrupt:
            logger.warning("KeyboardInterrupt received → stopping agent")
            raise
        except Exception as e:
            logger.exception(f"agent execution failed: {e}")
            raise

    # ✅ NEW: 히스토리 덤프 (이번 세션 1회용, 원인 파악용) — 필요 없으면 주석 처리
    with step(logger, "debug_dump_history"):
        try:
            hist_dir = os.path.join(_PKG_ROOT, "logs", "history")
            os.makedirs(hist_dir, exist_ok=True)
            dump_path = os.path.join(hist_dir, f"history-{session_id}.jsonl")
            # action_history()가 없을 수도 있어 안전 처리
            action_history = []
            if hasattr(history, "action_history"):
                try:
                    action_history = history.action_history()  # helper 제공 시
                except Exception:
                    action_history = []
            # 없는 경우, model_actions() 등으로 대체 수집
            if not action_history:
                try:
                    if hasattr(history, "model_actions"):
                        action_history = history.model_actions()
                except Exception:
                    action_history = []
            with open(dump_path, "w", encoding="utf-8") as wf:
                if isinstance(action_history, list):
                    for item in action_history:
                        try:
                            wf.write(json.dumps(item, ensure_ascii=False) + "\n")
                        except Exception:
                            # 객체면 dict로 변환 시도
                            try:
                                wf.write(json.dumps(item.__dict__, ensure_ascii=False) + "\n")
                            except Exception:
                                pass
            logger.info(f"dumped action history → {dump_path}")
        except Exception:
            logger.exception("history dump failed")

    # 5) 히스토리에서 문자열 수집 및 HTML/TEXT 선택 후 저장
    with step(logger, "collect_and_save"):
        candidates: List[str] = []

        # 1) action_results()
        try:
            if hasattr(history, "action_results") and callable(history.action_results):
                ars = history.action_results()
                for ar in ars:
                    candidates.extend(_flatten_strings(ar))
        except Exception:
            logger.exception("collect action_results failed")

        # 2) extracted_content()
        try:
            if hasattr(history, "extracted_content") and callable(history.extracted_content):
                ec = history.extracted_content()
                candidates.extend(_flatten_strings(ec))
        except Exception:
            logger.exception("collect extracted_content failed")

        # 3) model_outputs()
        try:
            if hasattr(history, "model_outputs") and callable(history.model_outputs):
                mo = history.model_outputs()
                candidates.extend(_flatten_strings(mo))
        except Exception:
            logger.exception("collect model_outputs failed")

        # 4) action_history()
        try:
            if hasattr(history, "action_history") and callable(history.action_history):
                ah = history.action_history()
                candidates.extend(_flatten_strings(ah))
        except Exception:
            logger.exception("collect action_history failed")

        # 5) final_result()
        try:
            if hasattr(history, "final_result") and callable(history.final_result):
                fr = history.final_result()
                candidates.extend(_flatten_strings(fr))
        except Exception:
            logger.exception("collect final_result failed")

        # ✅ NEW: 선택 로직
        html_val, text_val = _pick_html_text(candidates)

        # 저장
        saved_html = False
        saved_text = False
        try:
            if html_val:
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as wf:
                    wf.write(html_val)
                saved_html = True
                logger.info(f"saved HTML → {html_path}")
            else:
                logger.warning("no HTML candidate found")
        except Exception:
            logger.exception("save HTML failed")

        try:
            if text_val:
                os.makedirs(os.path.dirname(text_path), exist_ok=True)
                with open(text_path, "w", encoding="utf-8") as wf:
                    wf.write(text_val)
                saved_text = True
                logger.info(f"saved TEXT → {text_path}")
            else:
                logger.warning("no TEXT candidate found")
        except Exception:
            logger.exception("save TEXT failed")

    # 6) 최종 확인 및 로그 기록
    with step(logger, "finalize"):
        exists_html = os.path.exists(html_path)
        exists_text = os.path.exists(text_path)
        logger.info(f"files_exist html={exists_html} text={exists_text}")
        if exists_html and exists_text:
            logger.info(f"✅ Saved raw HTML/Text → {os.path.dirname(html_path)}/")
        else:
            logger.warning("⚠️ Files missing (one or both). Check previous steps/logs.")

    logger.info(f"[SESSION {session_id}] run_ingest end")


if __name__ == "__main__":
    # 기본 실행: 강남 / 숨결이 바람 될 때
    run_ingest("gangnam", "숨결이 바람 될 때", watchdog_sec=300)