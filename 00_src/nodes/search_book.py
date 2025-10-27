from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
import urllib.parse as _urlparse  # 도메인 추출용
from datetime import datetime

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

    # 브라우저 제한: 홈 URL에서 도메인 추출
    def _derive_allowed_from_home(url: str) -> List[str]:
        try:
            netloc = _urlparse.urlparse(url).netloc
            if netloc and "." in netloc:
                base = netloc.split(":")[0]
                parts = base.split(".")
                if len(parts) >= 2:
                    return [base, f"*.{'.'.join(parts[-2:])}"]
                return [base]
        except Exception:
            pass
        return ["*.go.kr", "*.or.kr"]  # fallback
    
    allowed = state.get("allowed_domains") or _derive_allowed_from_home(home)

    # 브라우저 생성
    browser = None
    if Browser is not None:
        try:
            browser = Browser(
                headless=False,
                allowed_domains=allowed,
                window_size={"width": 1280, "height": 900},
                keep_alive=True,
                minimum_wait_page_load_time=0.1,
                wait_for_network_idle_page_load_time=0.3,
                wait_between_actions=0.2,
                highlight_elements=False,
            )
            print(f"[search_book] Browser 생성 완료")
        except Exception as e:
            print(f"[search_book] ❌ Browser 생성 실패: {e}")
            browser = None

    # LLM (소형 모델)
    if Agent is None or ChatOpenAI is None:
        # 라이브러리 미설치 시 계획만 반환
        task_preview = _build_browser_use_task(home, title, {}, [30, 60, 90])
        return {**state, "ok": False, "result_hint": "plan_only", "page_url": None, "log": ["browser_use Agent 미설치"], "task_prompt": task_preview}
    llm = ChatOpenAI(model=state.get("llm_model", "gpt-5"))

    # 1단계: 규칙 기반(아주 짧은 태스크, max_steps=8)
    task_rules = _build_browser_use_task(home, title, {}, [30, 60, 90])
    agent_rules = Agent(task=task_rules, llm=llm, browser=browser) if browser else Agent(task=task_rules, llm=llm)

    import asyncio
    try:
        # asyncio 내에서 CDP/URL/HTML 추출하는 함수
        async def run_and_extract():
            history = await agent_rules.run(max_steps=int(state.get("max_steps_rules", 8)))
            
            # SPA 로딩 완료 대기: network idle or main content visible
            try:
                await browser.wait_for_network_idle(timeout=10000)
            except Exception:
                await asyncio.sleep(1.5)
            print(f"[search_book] SPA 로딩 대기 완료 (5초)")
            
            # CDP endpoint & page_url 추출
            page_url = None
            cdp = None
            
            if browser:
                try:
                    cdp = browser.cdp_url
                    print(f"[search_book] CDP: {cdp}")
                except Exception as e:
                    print(f"[search_book] CDP 추출 실패: {e}")
                
                try:
                    page_url = await browser.get_current_page_url()
                    print(f"[search_book] URL: {page_url}")
                except Exception as e:
                    print(f"[search_book] URL 추출 실패: {e}")
            
            # HTML 추출 및 저장
            saved_path = None
            html_size = 0
            
            if browser and hasattr(browser, 'cdp_client') and browser.cdp_client:
                try:
                    print(f"[search_book] HTML 추출 시작...")
                    result = await browser.cdp_client.send.Runtime.evaluate(
                        params={
                            "expression": "document.documentElement.outerHTML",
                            "returnByValue": True
                        },
                        session_id=browser.agent_focus.session_id
                    )
                    html_content = result.get("result", {}).get("value", "")
                    
                    if html_content:
                        # 저장 경로 생성
                        today = datetime.now().strftime("%Y-%m-%d")
                        timestamp = int(datetime.now().timestamp())
                        dir_path = f"00_src/data/raw/{today}"
                        os.makedirs(dir_path, exist_ok=True)
                        
                        filename = f"{place}_{timestamp}_results.html"
                        saved_path = os.path.join(dir_path, filename)
                        
                        # 파일 저장
                        with open(saved_path, "w", encoding="utf-8") as f:
                            f.write(html_content)
                        
                        html_size = len(html_content)
                        print(f"[search_book] ✅ HTML 저장 완료: {saved_path} ({html_size:,} bytes)")
                    else:
                        print(f"[search_book] ⚠️ HTML 내용이 비어있음")
                        
                except Exception as e:
                    print(f"[search_book] ❌ HTML 추출/저장 실패: {e}")
                    import traceback
                    traceback.print_exc()
            
            return history, page_url, cdp, saved_path, html_size
        
        # asyncio 실행
        history1, page_url, cdp_endpoint, saved_html_path, html_size = asyncio.run(run_and_extract())
        
        return {**state, "ok": True, "result_hint": "results_detected", "page_url": page_url, "cdp_endpoint": cdp_endpoint, "saved_html_path": saved_html_path, "html_size": html_size, "used_frame": None, "markers": [], "log": [f"rules_steps={len(history1) if isinstance(history1, list) else 'unknown'}"]}
    except Exception as e1:
        # 2단계: 유연 태스크(한 번만), max_steps=15
        task_llm = f"수정된 시도: 위와 동일하지만 다른 경로도 허용. 실패 시 즉시 종료.\n" + _build_browser_use_task(home, title, {}, [30, 60, 90])
        agent_llm = Agent(task=task_llm, llm=llm, browser=browser) if browser else Agent(task=task_llm, llm=llm)
        try:
            # asyncio 내에서 CDP/URL/HTML 추출하는 함수 (LLM 경로)
            async def run_and_extract_llm():
                history = await agent_llm.run(max_steps=int(state.get("max_steps_llm", 15)))
                
                # SPA 로딩 완료 대기: network idle or main content visible
                try:
                    await browser.wait_for_network_idle(timeout=10000)
                except Exception:
                    await asyncio.sleep(1.5)
                print(f"[search_book LLM] SPA 로딩 대기 완료 (5초)")
                
                # CDP endpoint & page_url 추출
                page_url = None
                cdp = None
                
                if browser:
                    try:
                        cdp = browser.cdp_url
                        print(f"[search_book LLM] CDP: {cdp}")
                    except Exception as e:
                        print(f"[search_book LLM] CDP 추출 실패: {e}")
                    
                    try:
                        page_url = await browser.get_current_page_url()
                        print(f"[search_book LLM] URL: {page_url}")
                    except Exception as e:
                        print(f"[search_book LLM] URL 추출 실패: {e}")
                
                # HTML 추출 및 저장
                saved_path = None
                html_size = 0
                
                if browser and hasattr(browser, 'cdp_client') and browser.cdp_client:
                    try:
                        print(f"[search_book LLM] HTML 추출 시작...")
                        result = await browser.cdp_client.send.Runtime.evaluate(
                            params={
                                "expression": "document.documentElement.outerHTML",
                                "returnByValue": True
                            },
                            session_id=browser.agent_focus.session_id
                        )
                        html_content = result.get("result", {}).get("value", "")
                        
                        if html_content:
                            today = datetime.now().strftime("%Y-%m-%d")
                            timestamp = int(datetime.now().timestamp())
                            dir_path = f"00_src/data/raw/{today}"
                            os.makedirs(dir_path, exist_ok=True)
                            
                            filename = f"{place}_{timestamp}_results.html"
                            saved_path = os.path.join(dir_path, filename)
                            
                            with open(saved_path, "w", encoding="utf-8") as f:
                                f.write(html_content)
                            
                            html_size = len(html_content)
                            print(f"[search_book LLM] ✅ HTML 저장 완료: {saved_path} ({html_size:,} bytes)")
                        else:
                            print(f"[search_book LLM] ⚠️ HTML 내용이 비어있음")
                            
                    except Exception as e:
                        print(f"[search_book LLM] ❌ HTML 추출/저장 실패: {e}")
                        import traceback
                        traceback.print_exc()
                
                return history, page_url, cdp, saved_path, html_size
            
            history2, page_url, cdp_endpoint, saved_html_path, html_size = asyncio.run(run_and_extract_llm())
            
            return {**state, "ok": True, "result_hint": "results_detected", "page_url": page_url, "cdp_endpoint": cdp_endpoint, "saved_html_path": saved_html_path, "html_size": html_size, "used_frame": None, "markers": [], "log": [f"llm_steps={len(history2) if isinstance(history2, list) else 'unknown'}", str(e1)]}
        except Exception as e2:
            return {**state, "ok": False, "result_hint": "execution_error", "page_url": None, "cdp_endpoint": None, "saved_html_path": None, "html_size": 0, "used_frame": None, "markers": [], "log": ["rules_failed", str(e1), "llm_failed", str(e2)]}
