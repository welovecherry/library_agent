from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
import urllib.parse as _urlparse  # 도메인 추출용

# Agent 모드용 라이브러리 (로컬 브라우저 직접 제어)
try:
    from browser_use import Agent, ChatOpenAI, Browser  # type: ignore
except Exception:
    Agent = None  # type: ignore
    ChatOpenAI = None  # type: ignore
    Browser = None  # type: ignore

# .env 자동 로드(있으면)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

try:
    from browser_use_sdk import BrowserUseClient  # type: ignore
except Exception:
    BrowserUseClient = None  # 런타임에 미설치면 '실행 계획'만 반환하여 디버깅 가능

SELECTORS_PATH = "00_src/configs/selectors.yaml"

def _build_browser_use_task(catalog_home_url: str, title: str, hint: Dict[str, Any], backoff: List[int]) -> str:
    """DOM 신호 기반: 보이면 즉시 진행, 보이지 않으면 짧게 재시도 후 종료."""
    return f"""
1) navigate to "{catalog_home_url}"
2) if a VISIBLE search input exists (placeholder/aria-label/label text includes: 검색|도서|자료), DO NOT WAIT: focus it immediately.
   else wait up to 5s; if still hidden, refresh ONCE and wait up to 5s again. if still hidden, STOP with no_results.
3) type "{title}" and press Enter. if not submitted, click the search/돋보기 button ONCE (no repeats).
4) if URL changed OR the page contains any of [검색결과, 소장, 대출, 건], STOP immediately with success (done).
5) NEVER repeat the same action twice. at most 2 attempts TOTAL. do not open new tabs. do not save HTML.
"""

def search_book(state: Dict[str, Any]) -> Dict[str, Any]:
    """도서관 홈 이동 + 초간단 검색(게이트드 LLM). 캡처는 하지 않음."""
    # 핵심 입력
    home = str(state.get("catalog_home_url", "")).strip()
    title = str(state.get("title", "")).strip()
    place = str(state.get("place", "")).strip()
    if not home or not title:
        return {**state, "ok": False, "result_hint": "invalid_input", "page_url": None}

    # 텔레메트리 비활성(불필요 백오프 방지)
    os.environ.setdefault("POSTHOG_DISABLED", "1")

    # 브라우저 제한(필요 시 state로 받기)
    default_allowed = [
        "*.gangnam.go.kr",
        "*.seocholib.or.kr",
        "*.splib.or.kr",
    ]
    allowed = state.get("allowed_domains") or default_allowed

    # 한국어 주석: 홈 URL에서 도메인을 추출해 allowed_domains를 더 타이트하게 설정
    def _derive_allowed_from_home(url: str) -> List[str]:
        try:
            netloc = _urlparse.urlparse(url).netloc
            if netloc and "." in netloc:
                base = netloc.split(":")[0]
                # 예: library.gangnam.go.kr -> *.gangnam.go.kr 로 축소 허용
                parts = base.split(".")
                if len(parts) >= 3:
                    return [f"*.{'.'.join(parts[-3:])}"]
                return [base]
        except Exception:
            pass
        return []

    derived = _derive_allowed_from_home(home)
    if derived:
        allowed = derived

    # 브라우저 생성(보여주기 모드)
    browser = None
    if Browser is not None:
        try:
            # 한국어 주석: 액션 간 최소 대기 등 장시간 대기 방지용 파라미터 추가
            browser = Browser(
                headless=False,
                allowed_domains=allowed,
                window_size={"width": 1280, "height": 900},
                keep_alive=True,
                minimum_wait_page_load_time=0.3,
                wait_for_network_idle_page_load_time=0.5,
                wait_between_actions=0.3,
                highlight_elements=False,
                prohibited_domains=["*.posthog.com", "eu.i.posthog.com"],
            )
        except Exception:
            browser = None

    # LLM (소형 모델)
    if Agent is None or ChatOpenAI is None:
        # 라이브러리 미설치 시 계획만 반환
        task_preview = _build_browser_use_task(home, title, {}, [30, 60, 90])
        return {**state, "ok": False, "result_hint": "plan_only", "page_url": None, "log": ["browser_use Agent 미설치"], "task_prompt": task_preview}
    llm = ChatOpenAI(model=state.get("llm_model", "gpt-5-mini"))

    # 1단계: 규칙 기반(아주 짧은 태스크, max_steps=8)
    task_rules = _build_browser_use_task(home, title, {}, [30, 60, 90])
    agent_rules = Agent(task=task_rules, llm=llm, browser=browser) if browser else Agent(task=task_rules, llm=llm)

    import asyncio
    try:
        # 한국어 주석: 규칙 기반 시도 단계 수 기본값 8로 축소
        history1 = asyncio.run(agent_rules.run(max_steps=int(state.get("max_steps_rules", 8))))
        # 성공 가정: 규칙 태스크에서 URL 이동/제출 신호면 충분하다고 판단
        page_url = None
        try:
            if getattr(agent_rules, "browser", None) and getattr(agent_rules.browser, "current_url", None):
                page_url = agent_rules.browser.current_url  # type: ignore
        except Exception:
            pass
        return {**state, "ok": True, "result_hint": "results_detected", "page_url": page_url, "used_frame": None, "markers": [], "log": [f"rules_steps={len(history1) if isinstance(history1, list) else 'unknown'}"]}
    except Exception as e1:
        # 2단계: 유연 태스크(한 번만), max_steps=15
        task_llm = f"수정된 시도: 위와 동일하지만 다른 경로도 허용. 실패 시 즉시 종료.\n" + _build_browser_use_task(home, title, {}, [30, 60, 90])
        agent_llm = Agent(task=task_llm, llm=llm, browser=browser) if browser else Agent(task=task_llm, llm=llm)
        try:
            # 한국어 주석: LLM 기반 시도 단계 수 기본값 15로 축소
            history2 = asyncio.run(agent_llm.run(max_steps=int(state.get("max_steps_llm", 15))))
            page_url = None
            try:
                if getattr(agent_llm, "browser", None) and getattr(agent_llm.browser, "current_url", None):
                    page_url = agent_llm.browser.current_url  # type: ignore
            except Exception:
                pass
            return {**state, "ok": True, "result_hint": "results_detected", "page_url": page_url, "used_frame": None, "markers": [], "log": [f"llm_steps={len(history2) if isinstance(history2, list) else 'unknown'}", str(e1)]}
        except Exception as e2:
            return {**state, "ok": False, "result_hint": "execution_error", "page_url": None, "used_frame": None, "markers": [], "log": ["rules_failed", str(e1), "llm_failed", str(e2)]}
