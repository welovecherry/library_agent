
from __future__ import annotations
from textwrap import dedent

def build_task_two_stage(
    district_display: str,
    book_title: str,
    json_save_path: str,
    fallback_url: str | None,
    adapter_hints: dict | None = None,
) -> str:
    """
    Build a *slim* two-stage browser-use task prompt.

    목적
    - 1단계: naver로 해당 지자체(구) 통합 도서관 카탈로그에 진입 (구글/빙 사용 금지).
    - 2단계: 내부 검색창에 `book_title`을 입력해 결과 1페이지만 추출 → JSON 배열로 저장.

    설계 원칙 (성공률/속도 최적화)
    - 스크린샷 지시를 *하지 않는다* (라이브러리가 필요 시 자체 캡처 가능). 
    - 액션 최소화: 검색 → 입력/제출 → extract(1회) → write_file → done.
    - 클릭 실패 폴백:
      1) 제출 버튼 클릭 실패 시, 입력창 포커스 후 Enter 전송
      2) 그래도 안 되면 evaluate(JS)로 버튼 클릭 시도
    - 재시도/대기 제한:
      - 동일 액션 재시도 최대 1회
      - 빈 화면/미렌더 시: refresh → wait 2s → 그래도 실패 시 fallback URL로 navigate
      - 장문 사고 방지: 모델 응답은 간결하게 유지 (지시형 문장만)

    어댑터 힌트
    - 페이지마다 검색창/버튼 셀렉터가 다를 수 있으므로, 기본 우선순위 셀렉터에
      adapter_hints(search_box_hints, submit_hints)을 *뒤에* 병합해서 시도한다.

    Args:
        district_display: 예) "강남구"
        book_title: 예) "숨결이 바람 될 때"
        json_save_path: write_file로 저장할 절대 경로
        fallback_url: SERP 실패 시 직접 진입할 카탈로그 루트 URL (예: index.do)
        adapter_hints: {"search_box_hints": [...], "submit_hints": [...]} 형태의 선택 힌트

    Returns:
        행동지시형 태스크 문자열(str)
    """

    # ----- 기본 셀렉터 우선순위 (랜딩 헤더/결과페이지 모두 커버) -----
    base_search_hints = [
        "#mainSearchKeyword",   # 랜딩 헤더 검색창
        "#searchKeyword",       # 결과 페이지 검색창
        "#totalSearch",
        "input[name='keyword']",
    ]
    base_submit_hints = [
        "#mainSearchBtn",       # 랜딩 헤더 검색 버튼
        "#searchBtn",           # 결과 페이지 검색 버튼
    ]

    # 어댑터 힌트를 뒤에 병합 (기본 우선순위를 해치지 않도록)
    search_box_hints = list(base_search_hints)
    submit_hints = list(base_submit_hints)
    if adapter_hints:
        search_box_hints += adapter_hints.get("search_box_hints", [])
        submit_hints += adapter_hints.get("submit_hints", [])

    task = dedent(f"""
    You are a precise, deterministic web agent. Follow the steps exactly. Keep thoughts short.
    Use only DuckDuckGo for external search. Do not use Google or Bing. Do not take screenshots unless the system requires them.

    ## Goal
    Reach the official integrated catalog for "{district_display}" and search for "{book_title}".
    Extract ALL visible results on the first page (NO filters, NO extra pages) and save a normalized JSON array to the given path.

    ## Stage A — Reach the catalog (DDG only)
    1) Use `search` with query: "{district_display} 공공도서관 통합검색".
    2) On the SERP, prefer domains with `.go.kr`, `lib`, `library`, `splib`. Open the most relevant result in the SAME tab.
    3) If the page appears blank or fails to render: `go_back` → `refresh` → `wait 2s`.
       If still failing and a fallback URL is provided, `navigate` to: {fallback_url or 'NO_FALLBACK'}

    ## Stage B — Internal search & one-shot extraction (NO filters)
    4) Find a search input from this PRIORITY list (use the first that exists & is visible):
       {search_box_hints}
    5) Type the exact title: "{book_title}".
    6) Try to submit in this order:
       - Click the first existing button among: {submit_hints}
       - If clicking fails, focus the input and send_keys "Enter".
       - If that still fails, run evaluate JS to click any of the buttons:
         evaluate code:
           document.querySelector('{submit_hints[0]}')?.click();
           document.querySelector('{submit_hints[-1]}')?.click();

    7) When results are visible, DO NOT apply any branch/library filters. Scroll at most 1 page ONLY if needed to reveal all visible results.

    8) Use `extract` and return EXACTLY this JSON array (no prose, no code block):
       [
         {{
           "meta": {{
             "checked_at": "<Asia/Seoul ISO8601 like 2025-10-11T110250+09:00>",
             "source_district": "{district_display}",
             "source_url": "<current page URL>",
             "search_query": "{book_title}"
           }},
           "book": {{
             "title": "<string>",
             "author": "<string|null>",
             "isbn": "<string|null>",
             "cover_url": "<string|null>"
           }},
           "library": {{
             "name": "<string>",
             "room": "<string|null>",
             "call_number": "<string|null>",
             "detail_url": "<string|null>"
           }},
           "status": {{
             "status_raw": "<string>",
             "available": <true|false>,
             "reserve_count": <int|null>,
             "due_date": "<YYYY-MM-DD|null>"
           }}
         }}
       ]
       - Set available=true if status_raw contains '대출가능' or '비치중'; otherwise false.
       - Keep status_raw as shown in Korean. Use null for missing fields.

    9) Save the JSON array using `write_file`:
       - file_name: "{json_save_path}"
       - content: "<the exact JSON array>"

    10) Immediately `done` with one line:
        Saved N results to {json_save_path} (available: X).

    ## Failure handling / budget
    - Retry the SAME action at most once. If it still fails, use the next fallback and proceed.
    - Do NOT paginate, do NOT filter, do NOT take screenshots.
    - Keep reasoning/output short to avoid timeouts.
    """
    ).strip()

    return task