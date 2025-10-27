"""
pipeline_graph.py
-----------------
LangGraph 파이프라인 (parse_html.py 연결 버전)

그래프:
  get_library_portal → search_book → parse_html → END

역할:
- get_library_portal: catalog_index.yaml에서 해당 구(place)의 포털/카탈로그 홈 URL을 찾음
- search_book: 제목으로 검색하고, 결과 페이지를 HTML로 저장(saved_html_path 등)
- parse_html: 저장된 HTML을 BeautifulSoup으로 DOM 직독 파싱 → 최소 필수 필드(title, library, status_raw, available)

CLI 예시:
  PYTHONPATH=00_src python -m graph.pipeline_graph
"""

from __future__ import annotations
from typing import Dict, Any
import pprint
import os

# LangGraph 기본 컴포넌트
from langgraph.graph import StateGraph, END

# 우리가 만든 노드 함수
from nodes.get_library_portal import get_library_portal
from nodes.search_book import search_book
from nodes.parse_html import parse_html


def build_graph():
    """
    그래프: get_library_portal → search_book → parse_html → END
    """
    graph = StateGraph(dict)  # 상태는 단순히 dict로 사용

    graph.add_node("get_library_portal", get_library_portal)
    graph.add_node("search_book", search_book)
    graph.add_node("parse_html", parse_html)

    graph.set_entry_point("get_library_portal")
    graph.add_edge("get_library_portal", "search_book")
    graph.add_edge("search_book", "parse_html")
    graph.add_edge("parse_html", END)

    return graph.compile()


def run_once(place: str, title: str, html_path: str | None = None) -> Dict[str, Any]:
    """
    그래프를 한 번 실행한다.

    입력:
      - place: 'gangnam' | 'songpa' | 'seocho' ... 등
      - title: 책 제목(검색어)
      - html_path: (선택) 이미 저장된 HTML 파일만 파싱하고 싶을 때 지정

    출력: 최종 state(dict)
    """
    app = build_graph()
    initial_state: Dict[str, Any] = {"place": place, "title": title}

    # 저장된 HTML만 사용하고 싶으면 상태에 주입
    if html_path:
        initial_state["saved_html_path"] = html_path

    result_state = app.invoke(initial_state)
    return result_state


if __name__ == "__main__":
    # 터미널 실행:
    #   PYTHONPATH=00_src python -m graph.pipeline_graph

    # 테스트 파라미터
    test_place = os.environ.get("TEST_PLACE", "seocho")
    test_title = os.environ.get("TEST_TITLE", "파이썬 프로그래밍")
    # 저장된 HTML만 파싱하고 싶다면 아래 경로를 환경변수로 전달
    test_html = os.environ.get("TEST_HTML")

    print(f"[pipeline_graph] run_once() with place={test_place}, title={test_title}, html_override={bool(test_html)}")
    out = run_once(test_place, test_title, html_path=test_html)

    print("\n[pipeline_graph] RESULT STATE")
    pprint.pprint(out)

    print("\n" + "="*80)
    print("[핵심 결과]")
    print("="*80)
    print(f"✓ 검색 성공: {out.get('ok')}")
    print(f"✓ 페이지 URL: {out.get('page_url')}")
    print(f"✓ HTML 저장 경로: {out.get('saved_html_path')}")
    print(f"✓ HTML 크기: {out.get('html_size', 0):,} bytes")
    print(f"\n✓ 파싱 성공: {out.get('parse_success')}")
    if out.get('parse_error'):
        print(f"✓ 파싱 에러: {out.get('parse_error')}")

    parsed_books = out.get('parsed_books', [])
    if parsed_books:
        print(f"\n📚 발견된 도서: {len(parsed_books)}건")
        print("-"*80)
        for idx, book in enumerate(parsed_books[:10], 1):
            title = book.get('title') or 'N/A'
            publisher = book.get('publisher') or 'N/A'
            year = book.get('year') or 'N/A'
            library = book.get('library') or 'N/A'
            room = book.get('room') or 'N/A'
            callno = book.get('call_number') or 'N/A'
            status_raw = book.get('status_raw') or 'N/A'
            available = book.get('available', False)

            print(f"\n[{idx}] {title}")
            print(f"    출판사: {publisher} ({year})")
            print(f"    도서관: {library}")
            print(f"    자료실: {room}")
            print(f"    청구기호: {callno}")
            print(f"    상태: {'✅ 대출가능' if available else '❌ 대출불가'}  | raw='{status_raw}'")

            if book.get('reserve_count') is not None:
                print(f"    예약: {book['reserve_count']}")
            if book.get('due_date'):
                print(f"    반납예정일: {book['due_date']}")
    else:
        print("\n📚 BeautifulSoup 파싱 결과가 없습니다 (DOM 모드).")
        print("    → 저장된 HTML이 렌더링 전 스냅샷이거나, DOM 구조가 비표준일 수 있어요.")

    print("\n" + "="*80)
