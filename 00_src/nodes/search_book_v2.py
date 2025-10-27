from __future__ import annotations
import os
import json
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse as _urlparse  # 도메인 추출용
from datetime import datetime

# Agent 모드용 라이브러리 (로컬 브라우저 직접 제어)
try:
    from browser_use import Agent, ChatOpenAI, Browser  # type: ignore
except Exception:
    Agent = None  # type: ignore
    ChatOpenAI = None  # type: ignore
    Browser = None  # type: ignore

# LangChain 메시지 타입
try:
    from langchain_core.messages import HumanMessage  # type: ignore
except Exception:
    HumanMessage = None  # type: ignore

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

async def _parse_html_with_llm(
    html_content: str,
    search_title: str,
    place: str
) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    """
    LLM을 사용하여 HTML에서 도서 정보를 추출합니다.
    
    Args:
        html_content: 추출한 HTML 문자열
        search_title: 검색한 책 제목 (필터링용)
        place: 도서관 지역명 (검증용)
    
    Returns:
        (parsed_books, parse_success, parse_error):
        - parsed_books: 도서 정보 리스트
        - parse_success: 파싱 성공 여부
        - parse_error: 에러 메시지 (성공 시 None)
    """
    if not ChatOpenAI:
        return [], False, "ChatOpenAI 라이브러리를 사용할 수 없습니다"
    
    try:
        # LLM 인스턴스 생성
        llm = ChatOpenAI(model="gpt-4o-mini", timeout=30.0)
        
        # 프롬프트 작성
        prompt = f"""
다음 HTML은 도서관 검색 결과 페이지입니다.
검색한 책: "{search_title}"
도서관 지역: "{place}"

**임무**: 이 HTML에서 "{search_title}"과 **정확히 일치**하거나 **매우 유사한** 책의 정보만 추출하세요.

**추출 조건**:
1. 제목이 "{search_title}"과 정확히 일치하거나
2. 제목에 "{search_title}"이 포함되고, 연도/부제만 다른 경우

**제외 조건**:
- 완전히 다른 책 (예: "트렌드 일본", "대한민국 트렌드" 등)
- 관련 없는 추천 도서

**추출 정보** (각 책마다):
- title: 책 제목 (정확한 전체 제목)
- author: 저자
- publisher: 출판사
- year: 출판년도
- library: 소장 도서관명
- room: 자료실/열람실
- call_number: 청구기호
- status: 대출 상태 (예: "대출가능", "대출중", "예약중" 등)
- available: 대출 가능 여부 (true/false)
- reserve_count: 예약 정보 (예: "5/5", "0/5")
- due_date: 반납예정일 (대출중인 경우, 예: "2025.11.04")

**출력 형식**: 반드시 JSON 배열로만 반환하세요. 다른 설명 없이 JSON만 출력하세요.

예시:
[
  {{
    "title": "트렌드 코리아 2026",
    "author": "김난도 [외]",
    "publisher": "미래의창",
    "year": "2025",
    "library": "송파돌마리도서관",
    "room": "돌마리_일반자료실",
    "call_number": "320.911-트294미-2026",
    "status": "대출중",
    "available": false,
    "reserve_count": "5/5",
    "due_date": "2025.11.04"
  }}
]

HTML:
{html_content[:50000]}
"""

        # LLM 호출
        print(f"[search_book_v2] LLM 파싱 시작... (HTML 크기: {len(html_content):,} bytes)")
        
        # HumanMessage로 래핑하여 호출
        if HumanMessage:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
        else:
            # fallback: 문자열 직접 전달 시도
            response = await llm.ainvoke(prompt)
        
        # 응답 추출
        response_text = response.content if hasattr(response, 'content') else str(response)
        
        # JSON 파싱
        # 가능한 경우 코드 블록 제거 (```json ... ```)
        response_text = response_text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        # JSON 파싱
        parsed_books = json.loads(response_text)
        
        # 리스트인지 확인
        if not isinstance(parsed_books, list):
            parsed_books = [parsed_books] if isinstance(parsed_books, dict) else []
        
        print(f"[search_book_v2] ✅ LLM 파싱 완료 (도서 {len(parsed_books)}건 추출)")
        
        return parsed_books, True, None
        
    except json.JSONDecodeError as e:
        error_msg = f"JSON 파싱 실패: {e}"
        print(f"[search_book_v2] ❌ {error_msg}")
        return [], False, error_msg
        
    except Exception as e:
        error_msg = f"LLM 파싱 실패: {e}"
        print(f"[search_book_v2] ❌ {error_msg}")
        import traceback
        traceback.print_exc()
        return [], False, error_msg

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
            
            # SPA 로딩 대기 (여유롭게)
            await asyncio.sleep(5.0)
            print(f"[search_book_v2] SPA 로딩 대기 완료 (5초)")
            
            # CDP endpoint & page_url 추출
            page_url = None
            cdp = None
            
            if browser:
                try:
                    cdp = browser.cdp_url
                    print(f"[search_book_v2] CDP: {cdp}")
                except Exception as e:
                    print(f"[search_book_v2] CDP 추출 실패: {e}")
                
                try:
                    page_url = await browser.get_current_page_url()
                    print(f"[search_book_v2] URL: {page_url}")
                except Exception as e:
                    print(f"[search_book_v2] URL 추출 실패: {e}")
            
            # HTML 추출 및 저장
            saved_html_path = None
            html_size = 0
            html_content = ""
            
            if browser and hasattr(browser, 'cdp_client') and browser.cdp_client:
                try:
                    print(f"[search_book_v2] HTML 추출 시작...")
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
                        saved_html_path = os.path.join(dir_path, filename)
                        
                        # HTML 파일 저장
                        with open(saved_html_path, "w", encoding="utf-8") as f:
                            f.write(html_content)
                        
                        html_size = len(html_content)
                        print(f"[search_book_v2] ✅ HTML 저장 완료: {saved_html_path} ({html_size:,} bytes)")
                    else:
                        print(f"[search_book_v2] ⚠️ HTML 내용이 비어있음")
                        
                except Exception as e:
                    print(f"[search_book_v2] ❌ HTML 추출/저장 실패: {e}")
                    import traceback
                    traceback.print_exc()
            
            # LLM 파싱 및 JSON 저장
            saved_json_path = None
            parsed_books = []
            parse_success = False
            parse_error = None
            
            if html_content:  # HTML이 있을 때만 파싱 시도
                try:
                    # LLM으로 HTML 파싱
                    parsed_books, parse_success, parse_error = await _parse_html_with_llm(
                        html_content, title, place
                    )
                    
                    # JSON 파일 저장 (파싱 성공 여부와 무관하게 저장)
                    if saved_html_path:  # HTML 파일이 저장된 경우
                        json_filename = f"{place}_{timestamp}_parsed.json"
                        saved_json_path = os.path.join(dir_path, json_filename)
                        
                        # 메타데이터 포함한 JSON 구조
                        json_data = {
                            "search_info": {
                                "place": place,
                                "title": title,
                                "timestamp": timestamp,
                                "search_date": today,
                                "html_file": filename,
                                "page_url": page_url or "unknown"
                            },
                            "parse_result": {
                                "success": parse_success,
                                "error": parse_error,
                                "book_count": len(parsed_books)
                            },
                            "books": parsed_books
                        }
                        
                        # JSON 파일 저장
                        with open(saved_json_path, "w", encoding="utf-8") as f:
                            json.dump(json_data, f, ensure_ascii=False, indent=2)
                        
                        print(f"[search_book_v2] ✅ JSON 저장 완료: {saved_json_path} ({len(json.dumps(json_data)):,} bytes)")
                        
                except Exception as e:
                    parse_error = f"JSON 저장 실패: {e}"
                    print(f"[search_book_v2] ❌ {parse_error}")
                    import traceback
                    traceback.print_exc()
            
            return history, page_url, cdp, saved_html_path, html_size, saved_json_path, parsed_books, parse_success, parse_error
        
        # asyncio 실행
        history1, page_url, cdp_endpoint, saved_html_path, html_size, saved_json_path, parsed_books, parse_success, parse_error = asyncio.run(run_and_extract())
        
        return {
            **state, 
            "ok": True, 
            "result_hint": "results_detected", 
            "page_url": page_url, 
            "cdp_endpoint": cdp_endpoint, 
            "saved_html_path": saved_html_path, 
            "html_size": html_size,
            "saved_json_path": saved_json_path,
            "parsed_books": parsed_books,
            "parse_success": parse_success,
            "parse_error": parse_error,
            "used_frame": None, 
            "markers": [], 
            "log": [f"rules_steps={len(history1) if isinstance(history1, list) else 'unknown'}"]
        }
    except Exception as e1:
        # 2단계: 유연 태스크(한 번만), max_steps=15
        task_llm = f"수정된 시도: 위와 동일하지만 다른 경로도 허용. 실패 시 즉시 종료.\n" + _build_browser_use_task(home, title, {}, [30, 60, 90])
        agent_llm = Agent(task=task_llm, llm=llm, browser=browser) if browser else Agent(task=task_llm, llm=llm)
        try:
            # asyncio 내에서 CDP/URL/HTML 추출하는 함수 (LLM 경로)
            async def run_and_extract_llm():
                history = await agent_llm.run(max_steps=int(state.get("max_steps_llm", 15)))
                
                # SPA 로딩 대기 (여유롭게)
                await asyncio.sleep(5.0)
                print(f"[search_book_v2 LLM] SPA 로딩 대기 완료 (5초)")
                
                # CDP endpoint & page_url 추출
                page_url = None
                cdp = None
                
                if browser:
                    try:
                        cdp = browser.cdp_url
                        print(f"[search_book_v2 LLM] CDP: {cdp}")
                    except Exception as e:
                        print(f"[search_book_v2 LLM] CDP 추출 실패: {e}")
                    
                    try:
                        page_url = await browser.get_current_page_url()
                        print(f"[search_book_v2 LLM] URL: {page_url}")
                    except Exception as e:
                        print(f"[search_book_v2 LLM] URL 추출 실패: {e}")
                
                # HTML 추출 및 저장
                saved_html_path = None
                html_size = 0
                html_content = ""
                
                if browser and hasattr(browser, 'cdp_client') and browser.cdp_client:
                    try:
                        print(f"[search_book_v2 LLM] HTML 추출 시작...")
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
                            saved_html_path = os.path.join(dir_path, filename)
                            
                            with open(saved_html_path, "w", encoding="utf-8") as f:
                                f.write(html_content)
                            
                            html_size = len(html_content)
                            print(f"[search_book_v2 LLM] ✅ HTML 저장 완료: {saved_html_path} ({html_size:,} bytes)")
                        else:
                            print(f"[search_book_v2 LLM] ⚠️ HTML 내용이 비어있음")
                            
                    except Exception as e:
                        print(f"[search_book_v2 LLM] ❌ HTML 추출/저장 실패: {e}")
                        import traceback
                        traceback.print_exc()
                
                # LLM 파싱 및 JSON 저장
                saved_json_path = None
                parsed_books = []
                parse_success = False
                parse_error = None
                
                if html_content:
                    try:
                        parsed_books, parse_success, parse_error = await _parse_html_with_llm(
                            html_content, title, place
                        )
                        
                        if saved_html_path:
                            json_filename = f"{place}_{timestamp}_parsed.json"
                            saved_json_path = os.path.join(dir_path, json_filename)
                            
                            json_data = {
                                "search_info": {
                                    "place": place,
                                    "title": title,
                                    "timestamp": timestamp,
                                    "search_date": today,
                                    "html_file": filename,
                                    "page_url": page_url or "unknown"
                                },
                                "parse_result": {
                                    "success": parse_success,
                                    "error": parse_error,
                                    "book_count": len(parsed_books)
                                },
                                "books": parsed_books
                            }
                            
                            with open(saved_json_path, "w", encoding="utf-8") as f:
                                json.dump(json_data, f, ensure_ascii=False, indent=2)
                            
                            print(f"[search_book_v2 LLM] ✅ JSON 저장 완료: {saved_json_path} ({len(json.dumps(json_data)):,} bytes)")
                            
                    except Exception as e:
                        parse_error = f"JSON 저장 실패: {e}"
                        print(f"[search_book_v2 LLM] ❌ {parse_error}")
                        import traceback
                        traceback.print_exc()
                
                return history, page_url, cdp, saved_html_path, html_size, saved_json_path, parsed_books, parse_success, parse_error
            
            history2, page_url, cdp_endpoint, saved_html_path, html_size, saved_json_path, parsed_books, parse_success, parse_error = asyncio.run(run_and_extract_llm())
            
            return {
                **state, 
                "ok": True, 
                "result_hint": "results_detected", 
                "page_url": page_url, 
                "cdp_endpoint": cdp_endpoint, 
                "saved_html_path": saved_html_path, 
                "html_size": html_size,
                "saved_json_path": saved_json_path,
                "parsed_books": parsed_books,
                "parse_success": parse_success,
                "parse_error": parse_error,
                "used_frame": None, 
                "markers": [], 
                "log": [f"llm_steps={len(history2) if isinstance(history2, list) else 'unknown'}", str(e1)]
            }
        except Exception as e2:
            return {
                **state, 
                "ok": False, 
                "result_hint": "execution_error", 
                "page_url": None, 
                "cdp_endpoint": None, 
                "saved_html_path": None, 
                "html_size": 0,
                "saved_json_path": None,
                "parsed_books": [],
                "parse_success": False,
                "parse_error": str(e2),
                "used_frame": None, 
                "markers": [], 
                "log": ["rules_failed", str(e1), "llm_failed", str(e2)]
            }
