"""
search_and_capture.py
---------------------
목적:
  - LLM 없이 Playwright를 사용하여 웹 자동화 수행
  - 1) place에 해당하는 카탈로그 홈페이지로 이동
  - 2) 검색 입력란과 버튼에 대해 셀렉터 배열에서 첫 번째 유효한 요소를 찾아 검색 1회 수행
  - 3) 검색 결과가 감지되지 않으면 재시도 1회 수행 (첫 시도 대기 2초, 재시도 대기 4초)
  - 4) 결과가 보이면 결과 페이지 HTML 및 텍스트 저장
  - 5) 실패 시 현재 페이지 HTML 및 텍스트 저장 (실패 원인 태그 포함)

입력:
  - place(str): "songpa", "gangnam" 등 catalog_index.yaml의 키
  - title(str): 검색어 (예: "숨결이 바람 될 때")
  - headed(bool): 브라우저 창 표시 여부 (기본 False)
  - slow(int): 동작 지연(ms), 사람이 보기 좋게 천천히 실행하고 싶을 때 사용 (기본 0)

설정파일:
  - 00_src/configs/catalog_index.yaml
      place별 홈페이지 URL 정의 (필수)
      (옵션) search_box, submit_btn 셀렉터 배열로 덮어쓰기 가능
  - 00_src/configs/selectors.yaml (없으면 내부 기본값 사용)
      defaults:
        search_box: [...]
        submit_btn: [...]
      result_markers: [".searchList", "#content", ".result-list", ".board-list"]

출력(파일):
  - 00_src/data/raw/YYYY-MM-DD/{place}_{ts}_results.html|.txt           # 검색 결과 감지 시 저장
  - 00_src/data/raw/YYYY-MM-DD/{place}_{ts}_home_fallback_no_results.html|.txt  # 결과 미감지 시 저장

실행 예시:
  PYTHONPATH=00_src python -m nodes.search_and_capture --place songpa --title "숨결이 바람 될 때"
  PYTHONPATH=00_src python -m nodes.search_and_capture --place songpa --title "숨결이 바람 될 때" --headed --slow 250

사전 준비:
  pip install playwright
  python -m playwright install chromium
"""

from __future__ import annotations
import os, sys, json, time, argparse, datetime, yaml
from typing import List, Dict, Any, Optional

# Playwright (sync API가 가장 간단)
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# For URL joining when using navigate_to_search
from urllib.parse import urljoin

CATALOG_INDEX_PATH = "00_src/configs/catalog_index.yaml"
SELECTORS_PATH = "00_src/configs/selectors.yaml" # 없으면 기본값 사용
RAW_DIR_ROOT = "00_src/data/raw"

# --------------------------
# 유틸 함수들
# --------------------------
def _today_dir() -> str:
    """YYYY-MM-DD 형식의 오늘 날짜 폴더 경로를 생성하고 반환한다."""
    d = datetime.date.today().isoformat()
    path = os.path.join(RAW_DIR_ROOT, d)
    os.makedirs(path, exist_ok=True)
    return path

def _ts() -> str:
    """현재 시간을 초 단위 정수로 반환하여 파일명 중복 방지에 사용한다."""
    return str(int(time.time()))

def _safe_write(path: str, content: str) -> None:
    """UTF-8 인코딩으로 텍스트 파일을 안전하게 저장한다."""
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content or "")

def _load_yaml(path: str) -> Dict[str, Any]:
    """지정된 경로에서 YAML 파일을 로드한다. 파일이 없으면 빈 딕셔너리를 반환한다."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _merge_selectors(place_entry: Dict[str, Any], default_cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    place 별 개별 설정과 전역 defaults를 병합하여 최종 셀렉터 배열을 반환한다.
    result_markers는 더 넓은 범위의 기본 마커 배열로 설정한다.
    """
    defaults = default_cfg.get("defaults", {}) if default_cfg else {}
    result_markers = default_cfg.get("result_markers", [
        ".searchList", ".search-list", ".result-list", ".result_list",
        ".board-list", "ul.board-list", ".book-list", "#resultList",
        "#content", "section.result", "div.result",
        "#searchResult", ".search-result", ".list_search", ".resultWrap"
    ])

    search_box = place_entry.get("search_box") or defaults.get("search_box") or [
        "#searchKeyword", "#q", "input[name='keyword']", "input[name='searchKey']", "input[type='text']",
        "input[name*='keyword' i]", "input[name*='search' i]", "input[type='search']", "input[placeholder*='검색']"
    ]
    submit_btn = place_entry.get("submit_btn") or defaults.get("submit_btn") or [
        "#searchBtn", "button.searchBtn", ".btn_search",
        "button:has-text('검색')", "input[type='submit']", "[class*='search']"
    ]

    return {
        "search_box": search_box,
        "submit_btn": submit_btn,
        "result_markers": result_markers,
    }


def _find_first_selector(page, selectors: List[str]) -> Optional[str]:
    """
    주어진 셀렉터 배열에서 페이지 내 존재하는 첫 번째 유효한 셀렉터 문자열을 반환한다.
    없으면 None 반환.
    """
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                return sel
        except Exception:
            pass
    return None

# --------------------------
# [NEW] 여러 셀렉터 중 하나라도 등장할 때까지 대기하는 헬퍼
# --------------------------
def _wait_for_any_selector(page, selectors: List[str], timeout_ms: int = 30000) -> Optional[str]:
    """
    selectors 중 하나라도 DOM에 나타나면 해당 셀렉터를 반환한다.
    timeout_ms까지 250ms 간격으로 폴링하며 대기한다.
    없으면 None 반환.
    """
    import time
    start = time.time()
    elapsed = 0
    while elapsed * 1000 < timeout_ms:
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    return sel
            except Exception:
                continue
        page.wait_for_timeout(250)
        elapsed = time.time() - start
    return None

# --------------------------
# [NEW] 결과 영역이 실제로 나타날 때까지 대기하는 헬퍼
# --------------------------
#
# def _wait_for_results(page, selectors: List[str], timeout_ms: int = 10000) -> bool:
#     """결과 영역이 실제로 나타날 때까지 대기한다."""
#     start = time.time()
#     while (time.time() - start) * 1000 < timeout_ms:
#         for sel in selectors:
#             try:
#                 el = page.query_selector(sel)
#                 if el and (el.inner_text() or "").strip():
#                     return True
#             except Exception:
#                 continue
#         page.wait_for_timeout(250)
#     return False

def _wait_for_results(page, selectors: List[str], timeout_ms: int = 60000) -> bool:
    """
    [UPDATED] 결과 리스트가 실제로 DOM에 렌더링될 때까지 **아주 여유롭게** 기다린다.

    - 기본 `selectors`에 범용 후보(`.list.row li`, `.book` 등)를 합쳐 감지율을 높인다.
    - 300ms 간격으로 폴링하면서, 주기적으로 `networkidle` 및 DOM 안정화도 짧게 유도한다.
    - 다음 중 하나라도 만족하면 True:
      1) 후보 셀렉터 중 하나의 **개수(count) > 0**
      2) 후보 셀렉터 중 하나의 **텍스트가 비어있지 않음**
      3) 페이지 본문 텍스트에 **'총 N건' 패턴**이 감지됨
    - 최대 `timeout_ms`(기본 60초)까지 대기하고, 조건을 만족하지 못하면 False.
    """
    import re, time as _t

    # 1) 후보 셀렉터 확장 (중복 제거 유지)
    extra_candidates = [
        ".list.row li", ".list .row li", ".book", ".book-item", ".result-item",
        ".search-result li", ".searchList li", ".board-list li", "ul.board-list li",
        "#searchResult li", "#resultList li"
    ]
    # dict.fromkeys 로 순서 유지하며 중복 제거
    candidates = list(dict.fromkeys(list(selectors or []) + extra_candidates))

    start = _t.time()
    last_dom_len = -1
    last_change = start

    def _body_text() -> str:
        try:
            if page.query_selector("body"):
                return page.inner_text("body") or ""
        except Exception:
            pass
        return ""

    while (_t.time() - start) * 1000 < timeout_ms:
        # 2) 빠른 폴링: 후보 셀렉터들 점검
        for sel in candidates:
            try:
                loc = page.locator(sel)
                cnt = loc.count()
                if cnt and cnt > 0:
                    # 개수만 확인하고 바로 성공 처리 (일부 템플릿일 수 있으므로 텍스트도 한번 더 확인)
                    try:
                        first = loc.nth(0)
                        txt = (first.inner_text() or "").strip()
                        if txt:
                            return True
                    except Exception:
                        pass
                    # 텍스트가 비어있더라도 개수>0이면 일단 결과가 떴다고 간주
                    return True
            except Exception:
                continue

        # 3) 본문에 "총 N건" 패턴이 있으면 성공 처리
        try:
            body = _body_text()
            if body and re.search(r"총\s*\d+\s*건", body):
                return True
        except Exception:
            pass

        # 4) DOM 안정 유도: content 길이 변화 추적 + networkidle 짧게 대기
        try:
            html = page.content() or ""
            cur_len = len(html)
        except Exception:
            cur_len = 0

        # 의미 있는 변화(50바이트 이상)만 변화로 간주
        if cur_len > last_dom_len + 50:
            last_dom_len = cur_len
            last_change = _t.time()

        # 네트워크가 잠잠해질 때까지 잠깐 대기 (오류는 무시)
        try:
            page.wait_for_load_state("networkidle", timeout=400)
        except Exception:
            pass

        # 짧은 슬립 (폴링 간격)
        try:
            page.wait_for_timeout(300)
        except Exception:
            _t.sleep(0.3)

        # 5) 너무 오래 변화가 없으면 한 번 더 content 길이로 안정 확인
        if (_t.time() - last_change) * 1000 > 1500:
            # 안정된 것으로 보고 한 사이클 더 검사하고 계속 진행
            pass

    return False

# --------------------------
# [NEW] DOM 안정(정착)까지 여유롭게 기다리는 헬퍼
# --------------------------
def _wait_for_dom_settled(page, max_wait_ms: int = 20000, stable_period_ms: int = 1200, poll_ms: int = 200) -> bool:
    """
    DOM이 '충분히 안정'될 때까지 기다린다.
    - 전체 HTML 길이의 증가가 일정 시간(stable_period_ms) 동안 멈추면 DOM이 안정된 것으로 간주한다.
    - 페이지의 content() 길이를 poll_ms 간격으로 측정하며, 50바이트 이상의 증가가 없고 stable_period_ms 이상 유지되면 True 반환.
    - networkidle도 짧게 대기하여 비동기 로딩 완료를 유도한다.
    - max_wait_ms 이내에 안정되지 않으면 False 반환.
    Args:
        page: Playwright Page 객체
        max_wait_ms: 최대 대기 시간(ms)
        stable_period_ms: HTML 길이 변화 없이 안정이라 간주하는 기간(ms)
        poll_ms: 폴링 간격(ms)
    Returns:
        bool: 안정됨(True) 또는 시간 초과(False)
    """
    import time
    start = time.time()
    last_len = -1
    last_change = start
    while (time.time() - start) * 1000 < max_wait_ms:
        # 현재 전체 HTML 길이 측정
        cur_len = 0
        try:
            html = page.content()
            cur_len = len(html) if html else 0
        except Exception:
            cur_len = 0
        # 노이즈 완화: 50바이트 이상 증가만 의미 있는 변화로 간주
        if cur_len > last_len + 50:
            last_len = cur_len
            last_change = time.time()
        # 네트워크가 잠잠해질 때까지도 잠깐씩 기다림
        try:
            page.wait_for_load_state("networkidle", timeout=poll_ms)
        except Exception:
            pass
        # 안정 기간 유지되면 종료
        if (time.time() - last_change) * 1000 >= stable_period_ms:
            return True
        # 다음 폴링까지 잠깐 쉼
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            time.sleep(poll_ms / 1000.0)
    return False

# --------------------------
# [NEW] 점진적 홈페이지 네비게이션 헬퍼
# --------------------------
def _goto_home(page, url: str, expect_selectors: List[str]) -> str:
    """
    순차적으로 domcontentloaded → load → commit 이벤트를 시도하며 페이지 이동을 수행한다.
    이동 후 expect_selectors 중 하나라도 최대 30초 대기.
    반환값: 실제 사용된 네비게이션 단계 문자열 ('domcontentloaded', 'load', 'commit')
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        stage = "domcontentloaded"
    except Exception:
        try:
            page.goto(url, wait_until="load", timeout=45000)
            stage = "load"
        except Exception:
            try:
                # Fire-and-forget, 에러 무시
                page.goto(url, wait_until="commit", timeout=10000)
            except Exception:
                pass
            stage = "commit"
    # 네비게이션 후 expect_selectors 중 하나라도 뜰 때까지 대기 (예외 무시)
    try:
        _wait_for_any_selector(page, expect_selectors, timeout_ms=30000)
    except Exception:
        pass
    return stage

def _results_evidence(page, title: str, markers: List[str]) -> Dict[str, Any]:
    """
    검색 결과 존재 여부를 다각도로 판단하기 위한 근거를 수집한다.
    - markers_hit: 결과 마커 셀렉터 중 텍스트가 존재하는 셀렉터 목록
    - title_match: 페이지 본문에 검색어가 포함되는지 여부
    - count_match: "총 N건" 형태의 텍스트가 포함되는지 여부
    - url: 현재 페이지 URL
    - ok: 위 조건 중 하나라도 충족하면 True
    """
    hits: List[str] = []
    # 1) 결과 마커 내 텍스트 존재 여부 확인
    for sel in markers:
        try:
            el = page.query_selector(sel)
            if el:
                txt = (el.inner_text() or "").strip()
                if txt:
                    hits.append(sel)
        except Exception:
            continue

    # 2) 본문에 검색어 포함 여부 확인
    try:
        body_text = page.inner_text("body")
    except Exception:
        body_text = ""
    title_match = (title in body_text) if body_text else False

    # 3) "총 N건" 형태의 결과 건수 텍스트 감지
    import re
    count_match = bool(re.search(r"총\s*\d+\s*건", body_text or ""))

    url = page.url
    ok = bool(hits or title_match or count_match)
    return {"markers_hit": hits, "count_match": count_match, "title_match": title_match, "url": url, "ok": ok}

# --------------------------
# [NEW] iframe 지원 유틸
# --------------------------
def _results_evidence_in_frame(frame, title: str, markers: List[str]) -> Dict[str, Any]:
    """
    프레임(iframe 포함) 내부에서 결과 존재 여부를 판단한다.
    - markers_hit: 결과 마커 셀렉터 중 텍스트가 존재하는 셀렉터 목록
    - title_match: 프레임 본문에 검색어 포함 여부
    - count_match: '총 N건' 텍스트 포함 여부
    - url: 프레임의 URL
    - ok: 위 조건 중 하나라도 True
    """
    hits: List[str] = []
    for sel in markers:
        try:
            el = frame.query_selector(sel)
            if el:
                txt = (el.inner_text() or "").strip()
                if txt:
                    hits.append(sel)
        except Exception:
            continue

    try:
        body_text = frame.inner_text("body")
    except Exception:
        body_text = ""

    import re
    title_match = (title in body_text) if body_text else False
    count_match = bool(re.search(r"총\s*\d+\s*건", body_text or ""))

    f_url = ""
    try:
        f_url = frame.url
    except Exception:
        f_url = ""

    ok = bool(hits or title_match or count_match)
    return {"markers_hit": hits, "count_match": count_match, "title_match": title_match, "url": f_url, "ok": ok}

def _collect_frame_text(frame, first_hit: Optional[str]) -> str:
    """프레임에서 우선 컨테이너(first_hit)가 있으면 그 텍스트를, 없으면 body 텍스트를 반환한다."""
    if first_hit:
        try:
            el = frame.query_selector(first_hit)
            if el:
                txt = (el.inner_text() or "").strip()
                if txt:
                    return txt
        except Exception:
            pass
    try:
        return frame.inner_text("body") or ""
    except Exception:
        return ""

def _collect_all_frames_text(page) -> str:
    """
    모든 프레임의 body 텍스트를 합쳐서 반환한다.
    LLM 보정용 백업 데이터로 사용한다.
    """
    chunks: List[str] = []
    try:
        # 우선 페이지 본문
        if page.query_selector("body"):
            bt = page.inner_text("body") or ""
            if bt.strip():
                chunks.append(bt.strip())
    except Exception:
        pass
    # 각 프레임 순회
    try:
        for fr in page.frames:
            try:
                if fr.query_selector("body"):
                    t = fr.inner_text("body") or ""
                    if t.strip():
                        chunks.append(t.strip())
            except Exception:
                continue
    except Exception:
        pass
    return "\n\n--- FRAME SPLIT ---\n\n".join(chunks)

# --------------------------
# [NEW] Shadow DOM 포함 전체 텍스트 추출
# --------------------------
def _extract_visible_text_with_shadow(page_or_frame) -> str:
    """Shadow DOM 포함 전체 텍스트 추출"""
    JS = r"""
    (function(){
      function getTextFromNode(node){
        let text = "";
        if(node.nodeType === Node.TEXT_NODE){
          const t = node.nodeValue ? node.nodeValue.trim() : "";
          return t ? (t + "\n") : "";
        }
        if(node.nodeType === Node.ELEMENT_NODE){
          const root = node.shadowRoot || node;
          const children = root.childNodes || [];
          for(const child of children){
            text += getTextFromNode(child);
          }
        }
        return text;
      }
      try{
        return getTextFromNode(document.documentElement) || "";
      }catch(e){
        return "";
      }
    })();
    """
    try:
        return page_or_frame.evaluate(JS) or ""
    except Exception:
        return ""

# --------------------------
# 핵심 로직: 검색 수행 및 결과 저장
# --------------------------
def run_search_and_capture(place: str, title: str, headed: bool = False, slow: int = 0) -> Dict[str, Any]:
    """
    Playwright를 사용하여 LLM 없이 다음 작업을 수행한다:
      1) place에 해당하는 홈페이지로 점진적 네비게이션 수행
      2) 검색 입력란과 버튼에 대해 셀렉터 배열에서 첫 번째 유효한 요소를 찾아 검색 1회 수행
      3) 검색 결과가 감지되지 않으면 재시도 1회 수행 (첫 시도 후 2초 대기, 재시도 후 4초 대기)
      4) 결과가 감지되면 결과 페이지 HTML과 텍스트 저장
      5) 실패 시 현재 페이지 HTML과 텍스트 저장 (실패 원인 태그 포함)

    매개변수:
      - place(str): catalog_index.yaml의 키 (예: songpa, gangnam)
      - title(str): 검색어 (예: "숨결이 바람 될 때")
      - headed(bool): True면 브라우저 창을 띄움(사람이 직접 관찰). False면 headless.
      - slow(int): 동작 지연(ms). 0이면 즉시, 250이면 사람이 보기 좋은 속도.

    반환값: 딕셔너리 형태로 성공 여부, 저장 경로, 사용된 셀렉터 정보 등 포함
    """
    # 1) 설정 로드 및 place 유효성 확인
    index = _load_yaml(CATALOG_INDEX_PATH)
    entry = index.get(place) or {}
    home = entry.get("homepage")
    # [NEW] Optional: navigate_to_search path
    nav_search = entry.get("navigate_to_search")
    if not home:
        # homepage 정보 없으면 즉시 실패 반환
        return {
            "ok": False,
            "reason": f"no_homepage_for_{place}",
            "saved_html": "",
            "saved_txt": "",
            "used": {"input": None, "button": None}
        }

    # 2) place별 및 전역 셀렉터 병합
    sel_cfg = _merge_selectors(entry, _load_yaml(SELECTORS_PATH))
    search_box = sel_cfg["search_box"]
    submit_btn = sel_cfg["submit_btn"]
    result_markers = sel_cfg["result_markers"]

    # 3) Playwright 브라우저 실행 및 페이지 생성
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not headed,   # 인자로 받은 headed/slow 사용 (CLI 전역에 의존하지 않음)
            slow_mo=slow
        )
        ctx = browser.new_context()
        # 네비게이션 및 일반 대기 타임아웃 설정
        ctx.set_default_navigation_timeout(90_000)  # 네비게이션 최대 90초
        ctx.set_default_timeout(60_000)             # 일반 대기 최대 60초
        page = ctx.new_page()

        # 4) 저장할 파일 경로 준비 (오늘 날짜 폴더, 타임스탬프 포함)
        out_dir = _today_dir()
        ts = _ts()

        # 5) 점진적 네비게이션 수행 (domcontentloaded → load → commit)
        nav_stage = _goto_home(page, home, search_box)
        # [추가] 홈페이지 진입 후 DOM 안정 대기
        _wait_for_dom_settled(page)
        # [CHANGED] 옵션: 검색 전용 URL이 설정되어 있으면 그쪽으로 한 번 더 이동
        if nav_search:
            try:
                target = nav_search if str(nav_search).startswith("http") else urljoin(home, str(nav_search))
                nav_stage = _goto_home(page, target, search_box)
                # [추가] 검색 전용 URL 이동 후 DOM 안정 대기
                _wait_for_dom_settled(page)
            except Exception:
                pass

        # 6) 검색 입력란 셀렉터 찾기 및 대기 (최초 시도)
        used_input = _find_first_selector(page, search_box)
        if not used_input:
            # 입력란이 늦게 뜰 수 있으므로 최대 15초 대기 후 재탐색
            used_input = _wait_for_any_selector(page, search_box, timeout_ms=15000)
        # [NEW] 프레임 내 검색 시도 (단 1회)
        if not used_input:
            try:
                for frame in page.frames:
                    for sel in search_box:
                        try:
                            el = frame.query_selector(sel)
                            if el:
                                used_input = sel
                                break
                        except Exception:
                            pass
                    if used_input:
                        break
            except Exception:
                pass

        used_button = None

        if used_input:
            # 7) 검색어 입력 및 버튼 클릭 혹은 Enter 키 입력 시도
            try:
                inp = page.query_selector(used_input)
                if inp:
                    inp.fill("")          # 기존 입력값 초기화
                    inp.type(title, delay=10)  # 검색어 입력 (키 입력 지연 포함)
            except Exception:
                pass

            # 8) 제출 버튼 클릭 시도
            used_button = _find_first_selector(page, submit_btn)
            if used_button:
                try:
                    page.click(used_button)
                except Exception:
                    used_button = None

            # 9) 제출 버튼이 없으면 Enter 키 입력으로 제출 시도
            if not used_button:
                try:
                    page.press(used_input, "Enter")
                    used_button = "ENTER"
                except Exception:
                    used_button = None

            # 10) DOM 안정 상태까지 대기 후 결과 근거 수집
            _wait_for_dom_settled(page)
            evidence = _results_evidence(page, title, result_markers)

            # [NEW] URL 변경 여부 확인 후, 변경 없으면 Enter 재입력 및 대기
            try:
                url_before = page.url
                if not evidence["ok"]:
                    if used_input:
                        page.press(used_input, "Enter")
                        used_button = used_button or "ENTER"
                    _wait_for_dom_settled(page)
                    evidence = _results_evidence(page, title, result_markers)
                    url_after = page.url
                    if url_before == url_after and not evidence["ok"]:
                        # URL 변화 없고 결과도 없으면 한번 더 Enter 누르고 대기
                        try:
                            if used_input:
                                page.press(used_input, "Enter")
                            _wait_for_dom_settled(page)
                            evidence = _results_evidence(page, title, result_markers)
                        except Exception:
                            pass
            except Exception:
                pass

        # [OLD] 기존: 메인 페이지에서만 증거 판단
        # evidence = locals().get("evidence") or _results_evidence(page, title, result_markers)
        # success = bool(evidence.get("ok"))
        # if success:
        #     base = f"{place}_{ts}_results"
        #     reason = "results_detected"
        # else:
        #     base = f"{place}_{ts}_home_fallback_no_results"
        #     reason = "no_visible_results"

        # [NEW] 1단계: 메인 페이지에서 증거 판단
        evidence = locals().get("evidence") or _results_evidence(page, title, result_markers)
        # [NEW] 검색 후 결과 DOM을 기다림 (HTML 추출 신뢰성 개선)
        if not evidence["ok"]:
            # [변경] 결과 재확인 전 DOM 안정 대기
            _wait_for_dom_settled(page)
            # 한 번 더 증거 수집
            evidence = _results_evidence(page, title, result_markers)
        success = bool(evidence.get("ok"))
        # [NEW][POST-RENDER GRACE]
        # 결과 감지 이후, 실제 리스트/텍스트가 렌더될 시간을 넉넉히 준다.
        if success:
            # 1) 결과 컨테이너(또는 범용 후보)가 실제로 나타날 때까지 한 번 더 기다림
            try:
                _wait_for_results(page, result_markers, timeout_ms=15000)
            except Exception:
                pass
            # 2) 렌더 완료 스냅샷 딜레이 (10초)
            try:
                page.wait_for_timeout(10_000)
            except Exception:
                pass
            # 3) 한 번 더 DOM 안정화
            _wait_for_dom_settled(page, max_wait_ms=20_000, stable_period_ms=1_500, poll_ms=200)
        used_frame = None  # 결과를 찾은 프레임 (없으면 메인 페이지)
        frame_first_hit = None

        # [NEW] 2단계: 메인 페이지에서 실패했다면, 모든 iframe 순회하며 증거 판단
        if not success:
            try:
                for fr in page.frames:
                    fr_ev = _results_evidence_in_frame(fr, title, result_markers)
                    if fr_ev.get("ok"):
                        evidence = fr_ev
                        success = True
                        used_frame = fr
                        try:
                            frame_first_hit = (fr_ev.get("markers_hit") or [None])[0]
                        except Exception:
                            frame_first_hit = None
                        break
            except Exception:
                pass

        # [NEW] 파일명/이유 결정
        if success:
            base = f"{place}_{ts}_results"
            reason = "results_detected" if used_frame is None else "results_detected_in_iframe"
        else:
            base = f"{place}_{ts}_home_fallback_no_results"
            reason = "no_visible_results"

        html_path = os.path.join(out_dir, f"{base}.html")
        txt_path = os.path.join(out_dir, f"{base}.txt")

        html_abs = os.path.abspath(html_path)
        txt_abs = os.path.abspath(txt_path)

        # [OLD] 메인 페이지 기준 저장
        # try:
        #     _safe_write(html_path, page.content())
        # except Exception:
        #     _safe_write(html_path, "<html><!-- failed to capture HTML --></html>")
        #
        # container_text = ""
        # first_hit = None
        # try:
        #     first_hit = (evidence.get("markers_hit") or [None])[0]
        # except Exception:
        #     first_hit = None
        # if success and first_hit:
        #     try:
        #         el = page.query_selector(first_hit)
        #         if el:
        #             container_text = (el.inner_text() or "").strip()
        #     except Exception:
        #         container_text = ""
        # if not container_text:
        #     try:
        #         container_text = page.inner_text("body") if page.query_selector("body") else ""
        #     except Exception:
        #         container_text = ""
        # _safe_write(txt_path, container_text)

        # [NEW] 프레임 사용 여부에 따라 저장 경로 분기
        if used_frame is None:
            # 메인 페이지에서 결과를 찾았거나 실패한 경우
            try:
                _safe_write(html_path, page.content())
            except Exception:
                _safe_write(html_path, "<html><!-- failed to capture HTML --></html>")

            # 개선된 텍스트 추출 및 저장 로직
            container_text = ""
            first_hit = None
            try:
                first_hit = (evidence.get("markers_hit") or [None])[0]
            except Exception:
                first_hit = None

            text_strategy = "none"

            if success and first_hit:
                try:
                    el = page.query_selector(first_hit)
                    if el:
                        container_text = (el.inner_text() or "").strip()
                        text_strategy = "marker_inner_text"
                except Exception:
                    container_text = ""

            if not container_text:
                container_text = _extract_visible_text_with_shadow(page)
                text_strategy = "shadow_dom_walk" if container_text else text_strategy

            if not container_text:
                combined = []
                try:
                    for fr in page.frames:
                        if fr == page.main_frame:
                            continue
                        t = _extract_visible_text_with_shadow(fr)
                        if t:
                            combined.append(t)
                except Exception:
                    pass
                if combined:
                    container_text = "\n\n---- iframes ----\n\n" + "\n\n".join(combined)
                    text_strategy = "iframes_shadow_dom"

            _safe_write(txt_path, container_text)
        else:
            # 프레임에서 결과를 찾은 경우: 해당 프레임의 HTML/TEXT 저장
            try:
                _safe_write(html_path, used_frame.content())
            except Exception:
                # 프레임 content() 실패 시 페이지 전체라도 저장
                try:
                    _safe_write(html_path, page.content())
                except Exception:
                    _safe_write(html_path, "<html><!-- failed to capture HTML --></html>")

            first_hit = None
            try:
                first_hit = (evidence.get("markers_hit") or [None])[0]
            except Exception:
                first_hit = None
            txt = _collect_frame_text(used_frame, first_hit)
            if not txt:
                # 백업: 모든 프레임/body 텍스트 합본
                txt = _collect_all_frames_text(page)
            _safe_write(txt_path, txt)
            # For iframe case, there is no text_strategy used in fallback logic, so set to "iframe_frame_text"
            text_strategy = "iframe_frame_text"

        # 15) 브라우저 자원 정리
        ctx.close()
        browser.close()

        # [NEW] iframe 디버그 보강
        debug_info = {}
        try:
            debug_info["current_url"] = page.url
        except Exception:
            debug_info["current_url"] = ""
        try:
            debug_info["page_title"] = page.title()
        except Exception:
            debug_info["page_title"] = ""
        try:
            body_text = page.inner_text("body") if page.query_selector("body") else ""
            debug_info["text_length"] = len(body_text) if body_text else 0
        except Exception:
            debug_info["text_length"] = 0
        try:
            debug_info["frames_count"] = len(page.frames)
        except Exception:
            debug_info["frames_count"] = None
        debug_info["used_iframe"] = bool(used_frame is not None)

        # [NEW] page kind classifier for debug clarity
        page_kind = "spa_shell"
        try:
            body_html = page.content()
            if any(k in body_html for k in ["book", "자료", "도서", "검색결과", "총", "건"]):
                page_kind = "result_dom"
        except Exception:
            pass
        debug_info["page_kind"] = page_kind

        return {
            "success": success,
            "status_message": "검색 결과를 감지했습니다." if success else "검색 결과를 감지하지 못했습니다.",
            "reason": reason,
            "place": place,
            "title": title,
            "page_url": evidence.get("url"),
            "evidence": {
                "markers_hit": evidence.get("markers_hit", []),
                "title_match": evidence.get("title_match"),
                "count_match": evidence.get("count_match")
            },
            "saved_html": html_path,
            "saved_txt": txt_path,
            "saved_html_abs": html_abs,
            "saved_txt_abs": txt_abs,
            "used": {"input": used_input, "button": used_button},
            "result_container": frame_first_hit if used_frame is not None else ( (evidence.get("markers_hit") or [None])[0] if evidence else None ),
            "debug": {**debug_info, "page_kind": page_kind, "text_strategy": locals().get("text_strategy", "")},
        }

# --------------------------
# CLI 진입점
# --------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Playwright로 카탈로그 검색 후 결과/실패 스냅샷 저장")
    parser.add_argument("--place", required=True, help="catalog_index.yaml의 키 (예: songpa, gangnam)")
    parser.add_argument("--title", required=True, help="검색어 (예: '숨결이 바람 될 때')")
    # === 새 옵션 ===
    parser.add_argument("--headed", action="store_true", help="브라우저 창 표시 (headful 모드)")
    parser.add_argument("--slow", type=int, default=0, help="동작 지연(ms). 예: 250 이면 사람이 볼 수 있게 천천히 실행")
    args = parser.parse_args()

    result = run_search_and_capture(args.place, args.title, headed=args.headed, slow=args.slow)
    print(json.dumps(result, ensure_ascii=False, indent=2))

# PYTHONPATH=00_src python -m nodes.search_and_capture --place songpa --title "숨결이 바람 될 때"
# PYTHONPATH=00_src python -m nodes.search_and_capture --place songpa --title "숨결이 바람 될 때" --headed --slow 250