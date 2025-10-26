"""
pipeline_graph.py
-----------------
가장 단순한 LangGraph 실행 틀(껍데기).
현재는 단 1개의 노드(get_library_portal)만 등록하고 실행한다.

역할:
- State(dict) 입력: {"place": "<예: 송파구>"}
- 노드 실행: get_library_portal(state)  → catalog_index.yaml에서 homepage URL 찾기
- 출력: {"place": ..., "catalog_home_url": "<str|None>", "found": <bool>, "index_key": "<str|None>", "reason": "<optional>"}

주의:
- 여기서는 "브라우저로 접속" 같은 행동은 하지 않는다.
- 다음 단계(ingest 노드 등)에서 catalog_home_url을 읽어 실제 네비게이션을 수행한다.
"""

from __future__ import annotations
from typing import Dict, Any
import pprint

# LangGraph 기본 컴포넌트
from langgraph.graph import StateGraph, END

# 우리가 만든 노드 함수
from nodes.get_library_portal import get_library_portal  # PYTHONPATH=00_src 로 실행 권장
from nodes.search_book import search_book


def build_graph():
    """
    그래프: get_library_portal → search_book → END
    """
    graph = StateGraph(dict)  # 상태는 단순히 dict로 사용
    graph.add_node("get_library_portal", get_library_portal)
    graph.add_node("search_book", search_book)
    graph.set_entry_point("get_library_portal")
    graph.add_edge("get_library_portal", "search_book")
    graph.add_edge("search_book", END)
    return graph.compile()


def run_once(place: str, title: str) -> Dict[str, Any]:
    """
    그래프를 한 번 실행한다.
    입력: place(예: 'gangnam', 'songpa'), title(책 제목)
    출력: 최종 state(dict)
    """
    app = build_graph()
    initial_state = {"place": place, "title": title}
    result_state = app.invoke(initial_state)
    return result_state


if __name__ == "__main__":
    # 간단 테스트 실행부
    # 터미널에서:
    #   PYTHONPATH=00_src python -m graph.pipeline_graph
    
    test_place = "songpa"  # gangnam, songpa, seocho 등
    test_title = "트렌드 코리아 2026"

    print(f"[pipeline_graph] run_once() with place={test_place}, title={test_title}")
    out = run_once(test_place, test_title)

    print("\n[pipeline_graph] RESULT STATE")
    pprint.pprint(out)
    
    print("\n[핵심 결과]")
    print(f"✓ 검색 성공: {out.get('ok')}")
    print(f"✓ 페이지 URL: {out.get('page_url')}")
    print(f"✓ CDP Endpoint: {out.get('cdp_endpoint')}")
    print(f"✓ HTML 저장 경로: {out.get('saved_html_path')}")
    print(f"✓ HTML 크기: {out.get('html_size', 0):,} bytes")
