# """
# capture_html.py
# ---------------
# 검색 결과 페이지의 HTML을 추출하고 저장하는 노드.

# 주요 역할:
# - search_book이 도달한 검색 결과 페이지의 HTML 추출
# - 파일로 저장: 00_src/data/raw/{YYYY-MM-DD}/{place}_{timestamp}_results.html
# - Phase 1: Main frame HTML만 추출 (iframe은 나중에)

# 다음 단계:
# - 저장된 HTML을 LLM/파서로 분석하여 도서 정보 추출
# """

# from __future__ import annotations
# import os
# import time
# from datetime import datetime
# from typing import Any, Dict
# from pathlib import Path
# import asyncio

# # CDP 클라이언트
# try:
#     from cdp_use import CDPClient  # type: ignore
# except Exception:
#     CDPClient = None  # type: ignore

# # browser-use 라이브러리 (fallback용)
# try:
#     from browser_use import Agent, ChatOpenAI, Browser  # type: ignore
# except Exception:
#     Agent = None  # type: ignore
#     ChatOpenAI = None  # type: ignore
#     Browser = None  # type: ignore

# # .env 자동 로드
# try:
#     from dotenv import load_dotenv  # type: ignore
#     load_dotenv()
# except Exception:
#     pass


# async def _extract_html_via_cdp(cdp_endpoint: str) -> str:
#     """
#     CDP를 통해 현재 페이지의 HTML을 추출한다.
    
#     Args:
#         cdp_endpoint: CDP WebSocket URL
    
#     Returns:
#         HTML 문자열
#     """
#     if not CDPClient:
#         raise ImportError("cdp-use 라이브러리가 설치되지 않았습니다")
    
#     client = CDPClient(cdp_endpoint)
#     await client.start()
    
#     try:
#         # 현재 페이지의 HTML 추출
#         result = await client.send.Runtime.evaluate(
#             params={
#                 'expression': 'document.documentElement.outerHTML',
#                 'returnByValue': True
#             }
#         )
        
#         html = result['result']['value']
#         return html
        
#     finally:
#         await client.stop()


# def capture_html(state: Dict[str, Any]) -> Dict[str, Any]:
#     """
#     검색 결과 페이지의 HTML을 추출하고 저장한다.
    
#     Args:
#         state: LangGraph state
#             - cdp_endpoint: CDP WebSocket URL (search_book에서 전달)
#             - page_url: 검색 결과 페이지 URL (참고용)
#             - place: 도서관 지역명 (파일명용)
#             - title: 검색한 책 제목 (메타데이터용)
    
#     Returns:
#         state 업데이트:
#             - saved_html_path: 저장된 HTML 파일 경로
#             - capture_success: HTML 캡처 성공 여부
#             - html_size: HTML 파일 크기 (bytes)
#             - capture_method: 사용한 캡처 방법
    
#     Note:
#         - CDP 재연결 방식으로 기존 브라우저에서 HTML 추출
#         - Phase 1: Main frame HTML만 추출
#         - iframe 추출은 나중에 추가 예정
#     """
#     print("\n" + "=" * 80)
#     print("[🔍 capture_html 노드 진입]")
#     print("=" * 80)
    
#     # 입력 검증
#     cdp_endpoint = str(state.get("cdp_endpoint", "")).strip()
#     page_url = str(state.get("page_url", "")).strip()
#     place = str(state.get("place", "unknown")).strip()
#     title = str(state.get("title", "")).strip()
#     ok_status = state.get("ok", False)
    
#     print(f"[1단계] State 확인:")
#     print(f"  ✓ 도서관: {place}")
#     print(f"  ✓ 책 제목: {title}")
#     print(f"  ✓ 검색 성공 여부 (ok): {ok_status}")
#     print(f"  ✓ 페이지 URL: {page_url if page_url else '❌ None'}")
#     print(f"  ✓ CDP Endpoint: {cdp_endpoint if cdp_endpoint else '❌ None'}")
#     print()
    
#     # search_book이 실패한 경우
#     if not ok_status:
#         print("❌ [에러] search_book 노드에서 검색 실패")
#         print(f"   result_hint: {state.get('result_hint', 'unknown')}")
#         return {
#             **state,
#             "capture_success": False,
#             "saved_html_path": None,
#             "html_size": 0,
#             "error": "search_book 실패로 인해 HTML 캡처 스킵"
#         }
    
#     # CDP endpoint가 없는 경우
#     if not cdp_endpoint:
#         print("❌ [에러] CDP endpoint가 없습니다")
#         print("   search_book에서 CDP endpoint 추출에 실패했을 가능성이 높습니다")
#         print(f"   State 전체 키: {list(state.keys())}")
#         return {
#             **state,
#             "capture_success": False,
#             "saved_html_path": None,
#             "html_size": 0,
#             "error": "cdp_endpoint가 없습니다 (search_book에서 추출 실패)"
#         }
    
#     print(f"[2단계] CDP endpoint 확인 완료")
#     print(f"  ✓ CDP URL: {cdp_endpoint}")
#     print()
    
#     # 텔레메트리 비활성화
#     os.environ["ANONYMIZED_TELEMETRY"] = "false"
#     os.environ["POSTHOG_DISABLED"] = "1"
#     os.environ["TELEMETRY_DISABLED"] = "1"
    
#     # CDP를 통해 HTML 추출
#     html_content = None
    
#     print(f"[3단계] CDP로 HTML 추출 시작")
#     print(f"  ✓ CDP endpoint: {cdp_endpoint}")
#     print(f"  ✓ CDPClient 사용 가능: {CDPClient is not None}")
    
#     try:
#         if not CDPClient:
#             raise ImportError("cdp-use 라이브러리가 설치되지 않았습니다")
        
#         print(f"  → CDP 재연결 시도 중...")
        
#         # CDP 재연결 및 HTML 추출
#         html_content = asyncio.run(_extract_html_via_cdp(cdp_endpoint))
        
#         if not html_content:
#             print("  ❌ HTML이 비어있습니다")
#             return {
#                 **state,
#                 "capture_success": False,
#                 "saved_html_path": None,
#                 "html_size": 0,
#                 "error": "HTML이 비어있음"
#             }
        
#         print(f"  ✅ HTML 추출 성공: {len(html_content):,} bytes")
#         print()
        
#     except ImportError as e:
#         print(f"  ❌ Import 에러: {e}")
#         print()
#         return {
#             **state,
#             "capture_success": False,
#             "saved_html_path": None,
#             "html_size": 0,
#             "error": f"CDP 라이브러리 없음: {e}"
#         }
#     except Exception as e:
#         print(f"  ❌ CDP HTML 추출 실패: {e}")
#         print(f"     에러 타입: {type(e).__name__}")
#         import traceback
#         print("     스택 트레이스:")
#         traceback.print_exc()
#         print()
#         return {
#             **state,
#             "capture_success": False,
#             "saved_html_path": None,
#             "html_size": 0,
#             "error": f"CDP HTML 추출 실패: {e}"
#         }
    
#     # 파일 저장 경로 생성
#     today = datetime.now().strftime("%Y-%m-%d")
#     timestamp = int(time.time())
    
#     base_dir = Path("00_src/data/raw")
#     date_dir = base_dir / today
    
#     print(f"[4단계] HTML 파일 저장")
#     print(f"  ✓ 저장 디렉토리: {date_dir}")
#     print(f"  ✓ 파일명: {place}_{timestamp}_results.html")
    
#     try:
#         date_dir.mkdir(parents=True, exist_ok=True)
#         print(f"  ✓ 디렉토리 생성/확인 완료")
#     except Exception as e:
#         print(f"  ❌ 디렉토리 생성 실패: {e}")
#         return {
#             **state,
#             "capture_success": False,
#             "saved_html_path": None,
#             "html_size": 0,
#             "error": f"디렉토리 생성 실패: {e}"
#         }
    
#     filename = f"{place}_{timestamp}_results.html"
#     file_path = date_dir / filename
    
#     # HTML 저장
#     try:
#         print(f"  → 파일 쓰기 시작...")
#         with open(file_path, 'w', encoding='utf-8') as f:
#             f.write(html_content)
        
#         html_size = len(html_content)
        
#         print(f"  ✅ HTML 저장 완료!")
#         print(f"     경로: {file_path}")
#         print(f"     크기: {html_size:,} bytes")
#         print("=" * 80 + "\n")
        
#         return {
#             **state,
#             "capture_success": True,
#             "saved_html_path": str(file_path),
#             "html_size": html_size,
#             "capture_method": "cdp-reconnect"
#         }
        
#     except Exception as e:
#         print(f"  ❌ 파일 저장 실패: {e}")
#         print(f"     에러 타입: {type(e).__name__}")
#         import traceback
#         print("     스택 트레이스:")
#         traceback.print_exc()
#         print()
#         return {
#             **state,
#             "capture_success": False,
#             "saved_html_path": None,
#             "html_size": 0,
#             "error": f"파일 저장 실패: {e}"
#         }


# if __name__ == "__main__":
#     """
#     capture_html 단독 테스트 (CDP 재연결 방식)
    
#     주의:
#         - CDP endpoint가 필요하므로 실제로는 pipeline에서 실행해야 함
#         - 단독 테스트는 CDP endpoint를 수동으로 지정해야 함
    
#     실행 방법:
#         1. search_book을 먼저 실행해서 CDP endpoint 얻기
#         2. 아래 test_state의 cdp_endpoint를 복사해서 넣기
#         3. PYTHONPATH=00_src python -m nodes.capture_html
#     """
#     import sys
    
#     print("=" * 60)
#     print("[capture_html 단독 테스트]")
#     print("=" * 60)
#     print("\n⚠️ 주의: CDP endpoint가 필요합니다!")
#     print("   1단계: search_book을 먼저 실행")
#     print("   2단계: CDP endpoint를 복사")
#     print("   3단계: 아래 test_state에 넣고 실행")
#     print("=" * 60)
    
#     # 테스트 state (CDP endpoint 필요!)
#     test_state = {
#         "place": "songpa",
#         "title": "트렌드 코리아 2026",
#         "page_url": "https://www.splib.or.kr/intro/program/plusSearchResultList.do",
#         "cdp_endpoint": "",  # ← 여기에 CDP endpoint 붙여넣기
#         # 예: "ws://127.0.0.1:9222/devtools/browser/xxxxx"
#     }
    
#     if not test_state["cdp_endpoint"]:
#         print("\n❌ CDP endpoint가 없습니다!")
#         print("   실제 테스트는 pipeline_graph.py를 통해 실행하세요:")
#         print("   PYTHONPATH=00_src python 00_src/graph/pipeline_graph.py")
#         sys.exit(1)
    
#     print("\n[입력 state]")
#     print(f"✓ 도서관: {test_state['place']}")
#     print(f"✓ 책 제목: {test_state['title']}")
#     print(f"✓ 페이지 URL: {test_state['page_url']}")
#     print(f"✓ CDP Endpoint: {test_state['cdp_endpoint'][:50]}...")
    
#     # 텔레메트리 비활성화
#     os.environ["ANONYMIZED_TELEMETRY"] = "false"
#     os.environ["POSTHOG_DISABLED"] = "1"
#     os.environ["TELEMETRY_DISABLED"] = "1"
    
#     try:
#         result = capture_html(test_state)
        
#         print("\n" + "=" * 60)
#         print("[결과]")
#         print("=" * 60)
#         print(f"✓ 캡처 성공: {result.get('capture_success')}")
#         print(f"✓ 저장 경로: {result.get('saved_html_path')}")
#         print(f"✓ 파일 크기: {result.get('html_size', 0):,} bytes")
#         print(f"✓ 캡처 방법: {result.get('capture_method')}")
        
#         if result.get('error'):
#             print(f"❌ 에러: {result.get('error')}")
        
#         print("=" * 60)
        
#         if result.get('capture_success'):
#             print("\n✅ 테스트 성공!")
#         else:
#             print("\n❌ 테스트 실패")
        
#         # 백그라운드 작업 종료 대기
#         print("\n⏳ 백그라운드 작업 종료 대기 중...")
#         time.sleep(1)
        
#     except KeyboardInterrupt:
#         print("\n\n⚠️ 사용자가 중단했습니다.")
#     except Exception as e:
#         print(f"\n\n❌ 예상치 못한 에러: {e}")
#         import traceback
#         traceback.print_exc()
#     finally:
#         print("\n✅ 프로세스 종료")
#         sys.exit(0)

