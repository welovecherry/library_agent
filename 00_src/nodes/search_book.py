from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
import urllib.parse as _urlparse  # 도메인 추출용
from datetime import datetime
from pathlib import Path

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

# Quick SPA readiness keywords for Korean library sites
SPA_READY_KEYWORDS = ["검색결과", "소장", "대출", "관심도서", "상세보기"]

def _build_browser_use_task(catalog_home_url: str, title: str, hint: Dict[str, Any], backoff: List[int]) -> str:
    """DOM 신호 기반: 보이면 즉시 진행, 보이지 않으면 충분히 대기 후 종료."""
    return f"""
1) navigate to "{catalog_home_url}"
2) if a VISIBLE search input exists (placeholder/aria-label/label text includes: 검색|도서|자료), DO NOT WAIT: focus it immediately.
   else wait up to 10s for SPA to load; if still hidden, STOP with no_results. DO NOT REFRESH.
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
    if not place:
        # fallback: try to infer later from saved filename; still keep non-empty token for downstream
        place = state["place"] = "unknown"
    if not home or not title:
        return {**state, "ok": False, "result_hint": "invalid_input", "page_url": None}

    # 텔레메트리 비활성(불필요 백오프 방지) - 모든 변수 강제 설정
    os.environ["POSTHOG_DISABLED"] = "1"
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["TELEMETRY_DISABLED"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"

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
                minimum_wait_page_load_time=0.5,
                wait_for_network_idle_page_load_time=0.8,
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
    llm = ChatOpenAI(model=state.get("llm_model", "gpt-5-mini"))

    # 1단계: 규칙 기반(아주 짧은 태스크, max_steps=8)
    task_rules = _build_browser_use_task(home, title, {}, [30, 60, 90])
    agent_rules = Agent(task=task_rules, llm=llm, browser=browser) if browser else Agent(task=task_rules, llm=llm)

    import asyncio
    try:
        # asyncio 내에서 CDP/URL/HTML 추출하는 함수
        async def run_and_extract():
            history = await agent_rules.run(max_steps=int(state.get("max_steps_rules", 8)))
            
            # SPA 로딩 완료 대기: 네트워크 아이들 + 본문 키워드 등장 대기(최대 10s)
            try:
                await browser.wait_for_network_idle(timeout=10000)
            except Exception:
                await asyncio.sleep(1.5)

            # 추가: 본문에 라이브러리 결과 키워드가 나타날 때까지 폴링(최대 10s)
            ready = False
            for _ in range(20):  # 20 * 0.5s = 10s
                try:
                    eval_result = await browser.cdp_client.send.Runtime.evaluate(
                        params={
                            "expression": "document.body ? document.body.innerText : ''",
                            "returnByValue": True
                        },
                        session_id=browser.agent_focus.session_id
                    )
                    body_text = (eval_result.get("result", {}) or {}).get("value", "") or ""
                    if any(k in body_text for k in SPA_READY_KEYWORDS):
                        ready = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)

            print(f"[search_book] SPA 로딩 대기 완료 ({'성공' if ready else '타임아웃'})")
            
            # 추가 대기: 검색 결과 데이터가 완전히 로드될 시간 확보 (5초)
            if ready:
                print(f"[search_book] 검색 결과 데이터 로딩 대기 중... (5초)")
                await asyncio.sleep(5)
            
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
                        
                        # 메타데이터 사이드카 저장(.meta.json)
                        try:
                            meta = {
                                "place": place,
                                "page_url": page_url,
                                "captured_at": datetime.now().isoformat(timespec="seconds"),
                                "cdp_endpoint": cdp
                            }
                            with open(saved_path + ".meta.json", "w", encoding="utf-8") as mf:
                                import json as _json
                                mf.write(_json.dumps(meta, ensure_ascii=False))
                        except Exception as _e:
                            print(f"[search_book] 메타 저장 경고: {_e}")
                        
                        html_size = len(html_content)
                        print(f"[search_book] ✅ HTML 저장 완료 (페이지 1): {saved_path} ({html_size:,} bytes)")
                    else:
                        print(f"[search_book] ⚠️ HTML 내용이 비어있음")
                        
                except Exception as e:
                    print(f"[search_book] ❌ HTML 추출/저장 실패: {e}")
                    import traceback
                    traceback.print_exc()
            
            # ========== 다중 페이지 처리: LLM에게 2페이지 클릭 요청 ==========
            saved_html_paths = [saved_path]  # 1페이지 경로 저장
            
            # 2페이지 저장을 위한 변수 준비
            today = datetime.now().strftime("%Y-%m-%d")
            timestamp = int(datetime.now().timestamp())
            dir_path = Path(f"00_src/data/raw/{today}")
            
            if browser:
                try:
                    print(f"[search_book] 🤖 LLM에게 2페이지 클릭 요청...")
                    
                    # 2페이지 클릭 태스크 (JavaScript 링크 명시)
                    page2_task = """
Task: Click pagination button '2' to go to page 2.

Steps:
1) Scroll down slowly (2-3 pages) to find pagination area at the bottom
2) Look for a link or button with text '2' (it may be <a href="javascript:fnList(2);">2</a>)
3) Click that '2' link/button
4) Immediately call 'done' after clicking

Important: The '2' button is a JavaScript link, not a regular button. Look carefully.
Maximum 4 steps allowed.
"""
                    
                    page2_agent = Agent(
                        task=page2_task,
                        llm=llm,
                        browser=browser,
                        use_vision=False,
                        max_steps=4,  # 스크롤 + 클릭 여유있게
                    )
                    
                    print(f"[search_book] Agent 실행 중 (2페이지 클릭)...")
                    page2_history = await page2_agent.run()
                    print(f"[search_book] ✅ 2페이지 클릭 Agent 완료 (steps={len(page2_history)})")
                    
                    # 페이지 로딩 대기
                    print(f"[search_book] 2페이지 로딩 대기 중... (5초)")
                    await asyncio.sleep(5)
                    
                    # 2페이지 HTML 추출
                    print(f"[search_book] 2페이지 HTML 추출 중...")
                    page2_html = await browser.cdp_client.send.Runtime.evaluate(
                        params={
                            "expression": "document.documentElement.outerHTML",
                            "returnByValue": True
                        },
                        session_id=browser.agent_focus.session_id
                    )
                    page2_html_content = page2_html.get("result", {}).get("value", "")
                    
                    if page2_html_content and len(page2_html_content) > 1000:
                        # 2페이지 파일명 생성
                        page2_filename = f"{place}_{timestamp}_results_page2.html"
                        page2_path = dir_path / page2_filename
                        
                        # 2페이지 HTML 저장
                        page2_path.write_text(page2_html_content, encoding="utf-8")
                        page2_size = len(page2_html_content)
                        print(f"[search_book] ✅ 2페이지 HTML 저장 완료: {page2_path} ({page2_size:,} bytes)")
                        
                        saved_html_paths.append(str(page2_path))
                    else:
                        print(f"[search_book] ⚠️ 2페이지 HTML 추출 실패 또는 내용 부족")
                    
                except Exception as e:
                    print(f"[search_book] ⚠️ 2페이지 처리 실패 (계속 진행): {e}")
            
            # 브라우저 종료 (async 컨텍스트 내부에서)
            if browser:
                try:
                    print(f"[search_book] 브라우저 종료 중...")
                    await browser.stop()  # BrowserSession은 close() 대신 stop() 사용
                    print(f"[search_book] ✅ 브라우저 종료 완료")
                except Exception as e:
                    print(f"[search_book] ⚠️ 브라우저 종료 경고: {e}")
            
            return history, page_url, cdp, saved_html_paths, html_size
        
        # asyncio 실행
        history1, page_url, cdp_endpoint, saved_html_paths, html_size = asyncio.run(run_and_extract())
        
        total_pages = len(saved_html_paths)
        print(f"[search_book] 📊 총 {total_pages}개 페이지 HTML 저장 완료")
        
        return {
            **state, 
            "ok": True, 
            "result_hint": "results_detected", 
            "page_url": page_url, 
            "cdp_endpoint": cdp_endpoint, 
            "saved_html_path": saved_html_paths[0] if saved_html_paths else None,  # 1페이지 경로 (하위 호환)
            "saved_html_paths": saved_html_paths,  # 전체 페이지 경로 리스트
            "total_pages": total_pages,
            "html_size": html_size, 
            "used_frame": None, 
            "markers": [], 
            "log": [f"rules_steps={len(history1) if isinstance(history1, list) else 'unknown'}"], 
            "place": place
        }
    except Exception as e1:
        # 2단계: 유연 태스크(한 번만), max_steps=15
        task_llm = f"수정된 시도: 위와 동일하지만 다른 경로도 허용. 실패 시 즉시 종료.\n" + _build_browser_use_task(home, title, {}, [30, 60, 90])
        agent_llm = Agent(task=task_llm, llm=llm, browser=browser) if browser else Agent(task=task_llm, llm=llm)
        try:
            # asyncio 내에서 CDP/URL/HTML 추출하는 함수 (LLM 경로)
            async def run_and_extract_llm():
                history = await agent_llm.run(max_steps=int(state.get("max_steps_llm", 15)))
                
                # SPA 로딩 완료 대기: 네트워크 아이들 + 본문 키워드 등장 대기(최대 10s)
                try:
                    await browser.wait_for_network_idle(timeout=10000)
                except Exception:
                    await asyncio.sleep(1.5)

                # 추가: 본문에 라이브러리 결과 키워드가 나타날 때까지 폴링(최대 10s)
                ready = False
                for _ in range(20):  # 20 * 0.5s = 10s
                    try:
                        eval_result = await browser.cdp_client.send.Runtime.evaluate(
                            params={
                                "expression": "document.body ? document.body.innerText : ''",
                                "returnByValue": True
                            },
                            session_id=browser.agent_focus.session_id
                        )
                        body_text = (eval_result.get("result", {}) or {}).get("value", "") or ""
                        if any(k in body_text for k in SPA_READY_KEYWORDS):
                            ready = True
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)

                print(f"[search_book LLM] SPA 로딩 대기 완료 ({'성공' if ready else '타임아웃'})")
                
                # 추가 대기: 검색 결과 데이터가 완전히 로드될 시간 확보 (5초)
                if ready:
                    print(f"[search_book LLM] 검색 결과 데이터 로딩 대기 중... (5초)")
                    await asyncio.sleep(5)
                
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
                            
                            # 메타데이터 사이드카 저장(.meta.json)
                            try:
                                meta = {
                                    "place": place,
                                    "page_url": page_url,
                                    "captured_at": datetime.now().isoformat(timespec="seconds"),
                                    "cdp_endpoint": cdp
                                }
                                with open(saved_path + ".meta.json", "w", encoding="utf-8") as mf:
                                    import json as _json
                                    mf.write(_json.dumps(meta, ensure_ascii=False))
                            except Exception as _e:
                                print(f"[search_book LLM] 메타 저장 경고: {_e}")
                            
                            html_size = len(html_content)
                            print(f"[search_book LLM] ✅ HTML 저장 완료: {saved_path} ({html_size:,} bytes)")
                        else:
                            print(f"[search_book LLM] ⚠️ HTML 내용이 비어있음")
                            
                    except Exception as e:
                        print(f"[search_book LLM] ❌ HTML 추출/저장 실패: {e}")
                        import traceback
                        traceback.print_exc()
                
                # 브라우저 종료 (async 컨텍스트 내부에서)
                if browser:
                    try:
                        print(f"[search_book] 브라우저 종료 중...")
                        await browser.stop()  # BrowserSession은 close() 대신 stop() 사용
                        print(f"[search_book] ✅ 브라우저 종료 완료")
                    except Exception as e:
                        print(f"[search_book] ⚠️ 브라우저 종료 경고: {e}")
                
                return history, page_url, cdp, saved_path, html_size
            
            history2, page_url, cdp_endpoint, saved_html_path, html_size = asyncio.run(run_and_extract_llm())
            
            return {**state, "ok": True, "result_hint": "results_detected", "page_url": page_url, "cdp_endpoint": cdp_endpoint, "saved_html_path": saved_html_path, "html_size": html_size, "used_frame": None, "markers": [], "log": [f"llm_steps={len(history2) if isinstance(history2, list) else 'unknown'}", str(e1)], "place": place}
        except Exception as e2:
            return {**state, "ok": False, "result_hint": "execution_error", "page_url": None, "cdp_endpoint": None, "saved_html_path": None, "html_size": 0, "used_frame": None, "markers": [], "log": ["rules_failed", str(e1), "llm_failed", str(e2)]}
