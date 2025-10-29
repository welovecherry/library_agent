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
import argparse

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
    # ------------------------------------------------------------------
    # CLI 파서: 환경변수 + CLI 동시 지원 (CLI가 우선)
    #   예)
    #   PYTHONPATH=00_src python -m graph.pipeline_graph \
    #     --place gangnam \
    #     --title "어린 왕자" \
    #     --html-override --html-path 00_src/data/raw/2025-10-28/gangnam_1761660636_results.html \
    #     --out-jsonl 00_src/data/parsed/2025-10-28/gangnam_results.jsonl \
    #     --out-json  00_src/data/parsed/2025-10-28/gangnam_results.json
    # ------------------------------------------------------------------
    parser = argparse.ArgumentParser(description="Library search → HTML save → JSON/JSONL parse pipeline")
    parser.add_argument("--place", type=str, help="예: gangnam | songpa | seocho ...")
    parser.add_argument("--title", type=str, help="검색어(도서명)")
    parser.add_argument("--html-override", action="store_true", help="브라우저 검색 생략, 저장된 HTML만 파싱")
    parser.add_argument("--html-path", type=str, help="저장된 HTML 경로 (override와 함께 사용 권장)")
    parser.add_argument("--out-jsonl", type=str, help="JSONL 저장 경로")
    parser.add_argument("--out-json", type=str, help="JSON 저장 경로")
    args = parser.parse_args()

    # 1) place/title 우선순위: CLI > ENV > 기본값
    test_place = args.place or os.environ.get("TEST_PLACE", "seocho")
    test_title = args.title or os.environ.get("TEST_TITLE", "세이노의 가르침")

    # 2) 출력 경로: CLI가 오면 ENV에 주입해서 run_once 내부 로직과 정합
    if args.out_jsonl:
        os.environ["TEST_OUT_JSONL"] = args.out_jsonl
    if args.out_json:
        os.environ["TEST_OUT_JSON"] = args.out_json

    # 3) override 모드: CLI --html-override가 있으면 --html-path 사용
    #    (없으면 ENV TEST_HTML 사용; 둘 다 없으면 브라우저 검색)
    test_html = None
    if args.html_override and args.html_path:
        test_html = args.html_path
    else:
        test_html = os.environ.get("TEST_HTML")

    print(f"[pipeline_graph] run_once() with place={test_place}, title={test_title}, html_override={bool(test_html)}")

    # Show planned save paths for visibility (환경변수 반영 후 경로 계산)
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
    
    # 다중 페이지 출력
    total_pages = out.get('total_pages', 1)
    saved_html_paths = out.get('saved_html_paths', [out.get('saved_html_path')])
    saved_html_paths = [p for p in saved_html_paths if p]
    
    if total_pages > 1:
        print(f"✓ 총 페이지 수: {total_pages}개")
        for idx, path in enumerate(saved_html_paths, 1):
            print(f"  [{idx}] {path}")
    else:
        print(f"✓ HTML 저장 경로: {out.get('saved_html_path')}")

    # HTML 크기 표시: 상태에 없으면 파일 크기 직접 계산 시도
    html_size = out.get("html_size", 0)
    if (not html_size) and out.get("saved_html_path"):
        try:
            html_size = os.path.getsize(out["saved_html_path"])
        except Exception:
            html_size = 0
    print(f"✓ HTML 크기: {html_size:,} bytes")

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
