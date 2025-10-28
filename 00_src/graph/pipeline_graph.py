"""
pipeline_graph.py
-----------------
LangGraph 파이프라인 (parse_html.py 연결 버전)

그래프(기본):
  get_library_portal → search_book → parse_html → END

단축 경로:
  저장된 HTML 경로(html_path 또는 TEST_HTML)가 주어지면 그래프를 건너뛰고
  곧바로 parse_html만 실행하여 결과를 반환한다.

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
from datetime import datetime

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

app = build_graph()


def run_once(place: str, title: str, html_path: str | None = None) -> Dict[str, Any]:
    """
    그래프를 한 번 실행한다.

    입력:
      - place: 'gangnam' | 'songpa' | 'seocho' ... 등
      - title: 책 제목(검색어)
      - html_path: (선택) 이미 저장된 HTML 파일만 파싱하고 싶을 때 지정

    출력: 최종 state(dict)
    """
    initial_state: Dict[str, Any] = {"place": place, "title": title}

    # ---- [ADDED] Set default parsed-output paths so parse_html can save in graph mode ----
    # Environment overrides:
    env_out_jsonl = os.environ.get("TEST_OUT_JSONL")
    env_out_json = os.environ.get("TEST_OUT_JSON")

    # Default path: 00_src/data/parsed/{YYYY-MM-DD}/{place}_results.jsonl
    date_str = datetime.now().strftime("%Y-%m-%d")
    default_parsed_dir = os.path.join("00_src", "data", "parsed", date_str)
    try:
        os.makedirs(default_parsed_dir, exist_ok=True)
    except Exception:
        # If directory creation fails, parse_html will still run without saving.
        pass

    default_jsonl = os.path.join(default_parsed_dir, f"{place}_results.jsonl")
    default_json = os.path.join(default_parsed_dir, f"{place}_results.json")

    # Apply overrides if provided
    if env_out_jsonl:
        initial_state["out_jsonl"] = env_out_jsonl
    else:
        initial_state["out_jsonl"] = default_jsonl

    if env_out_json:
        initial_state["out_json"] = env_out_json
    else:
        initial_state["out_json"] = default_json
    # ---- [ADDED END] ----

    # 저장된 HTML이 있으면 그래프를 건너뛰고 바로 파싱 노드를 호출한다.
    # (검색/탐색을 생략하여 parse_html만 실행)
    if html_path:
        initial_state["saved_html_path"] = html_path
        # Ensure one-off parse also saves to the same configured paths
        initial_state.setdefault("out_jsonl", initial_state.get("out_jsonl"))
        initial_state.setdefault("out_json", initial_state.get("out_json"))
        # parse_html은 상태 dict를 입력받아 동일 dict를 반환하도록 설계됨
        return parse_html(initial_state)

    # 저장된 HTML이 없으면 정상 그래프 실행 (get_library_portal → search_book → parse_html)
    result_state = app.invoke(initial_state)
    return result_state


if __name__ == "__main__":
    # 터미널 실행:
    #   PYTHONPATH=00_src python -m graph.pipeline_graph

    # 테스트 파라미터
    test_place = os.environ.get("TEST_PLACE", "seocho")
    test_title = os.environ.get("TEST_TITLE", "세이노의 가르침")
    # 저장된 HTML만 파싱하고 싶다면 아래 경로를 환경변수로 전달
    # (이 경우 search_book은 건너뛰고 parse_html만 실행됨)
    test_html = os.environ.get("TEST_HTML")

    print(f"[pipeline_graph] run_once() with place={test_place}, title={test_title}, html_override={bool(test_html)}")

    # Show planned save paths for visibility
    planned_date = datetime.now().strftime("%Y-%m-%d")
    planned_dir = os.path.join("00_src", "data", "parsed", planned_date)
    planned_jsonl = os.environ.get("TEST_OUT_JSONL", os.path.join(planned_dir, f"{test_place}_results.jsonl"))
    planned_json = os.environ.get("TEST_OUT_JSON", os.path.join(planned_dir, f"{test_place}_results.json"))
    print(f"[pipeline_graph] planned out_jsonl={planned_jsonl}")
    print(f"[pipeline_graph] planned out_json={planned_json}")

    out = run_once(test_place, test_title, html_path=test_html)

    print("\n[pipeline_graph] RESULT STATE")
    pprint.pprint(out)

    # 아래 출력은 확인용임. 나중에 삭제할 계획
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

    # Print saved artifact locations if any
    saved_artifacts = out.get("saved")
    if saved_artifacts:
        print("\n✓ 저장된 산출물:")
        for kind, path in saved_artifacts:
            print(f"   - {kind}: {path}")
    else:
        # Fallback: show planned paths (may be used if parse_html handled saving silently)
        if out.get("out_jsonl") or out.get("out_json"):
            print("\n✓ (참고) 저장 경로(계획):")
            if out.get("out_jsonl"):
                print(f"   - jsonl: {out['out_jsonl']}")
            if out.get("out_json"):
                print(f"   - json:  {out['out_json']}")

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
