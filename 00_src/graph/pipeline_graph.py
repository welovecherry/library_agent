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


def build_graph():
    """
    아주 단순한 그래프를 만들어 반환한다.
    - 엔트리포인트: get_library_portal
    - 다음 엣지: get_library_portal → END
    """
    graph = StateGraph(dict)  # 상태는 단순히 dict로 사용
    graph.add_node("get_library_portal", get_library_portal)
    graph.set_entry_point("get_library_portal")
    graph.add_edge("get_library_portal", END)
    return graph.compile()


def run_once(place: str) -> Dict[str, Any]:
    """
    그래프를 한 번 실행한다.
    입력: place(예: '송파구', '강남구')
    출력: get_library_portal 결과를 포함한 state(dict)
    """
    app = build_graph()
    initial_state = {"place": place}
    result_state = app.invoke(initial_state)
    return result_state


if __name__ == "__main__":
    # 간단 테스트 실행부
    # 터미널에서:
    #   PYTHONPATH=00_src python -m graph.pipeline_graph
    #
    # 원하는 지역으로 바꿔보세요: "송파구", "강남구", "서초구" 등
    test_place = "gangnam"
    # test_place = "songpa"
    # test_place = "seocho"

    print("[pipeline_graph] run_once() with place =", test_place)
    out = run_once(test_place)

    print("\n[pipeline_graph] RESULT STATE")
    pprint.pprint(out)
