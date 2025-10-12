# 00_src/nodes/search_and_capture.py
"""
search_and_capture.py
---------------------
목적:
  - LLM 없이 Playwright로만 동작
  - 1) 카탈로그 홈페이지로 이동
  - 2) 검색 인풋과 버튼을 "최소 셀렉터 배열"로 1회 시도(+재시도 1회, 2s→4s 대기)
  - 3) 결과가 보이면 결과 페이지 HTML/TEXT 저장
  - 4) 실패해도 현재 페이지 HTML/TEXT 저장(원인 태그 포함)

입력:
  - place(str): "songpa", "gangnam" 등 (catalog_index.yaml의 키)
  - title(str): 검색어 (예: "숨결이 바람 될 때")

설정파일:
  - 00_src/configs/catalog_index.yaml
      place별 homepage 정의 (필수)
      (옵션) search_box, submit_btn 배열로 덮어쓰기 가능
  - 00_src/configs/selectors.yaml (없으면 defaults 사용)
      defaults:
        search_box: [...]
        submit_btn: [...]
      result_markers: [".searchList", "#content", ".result-list", ".board-list"]

출력(파일):
  - 00_src/data/raw/YYYY-MM-DD/{place}_{ts}_results.html|.txt         # 성공
  - 00_src/data/raw/YYYY-MM-DD/{place}_{ts}_home_fallback_{reason}.html|.txt  # 실패

실행 예시:
  PYTHONPATH=00_src python -m nodes.search_and_capture --place songpa --title "숨결이 바람 될 때"

사전 준비:
  pip install playwright
  python -m playwright install chromium
"""

from __future__ import annotations
import os, sys, json, time, argparse, datetime, yaml
from typing import List, Dict, Any, Optional

# Playwright (sync API가 가장 간단)
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CATALOG_INDEX_PATH = "00_src/configs/catalog_index.yaml"
SELECTORS_PATH = "00_src/configs/selectors.yaml" # 없으면 기본값 사용
RAW_DIR_ROOT = "00_src/data/raw"

# --------------------------
# 유틸
# --------------------------
def _today_dir() -> str:
    """YYYY-MM-DD 디렉토리 경로를 반환하고 생성한다."""
    d = datetime.date.today().isoformat()
    path = os.path.join(RAW_DIR_ROOT, d)
    os.makedirs(path, exist_ok=True)
    return path

def _ts() -> str:
    """정렬/중복방지용 타임스탬프(초 단위)."""
    return str(int(time.time()))

def _safe_write(path: str, content: str) -> None:
    """텍스트 파일 저장 (UTF-8)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content or "")

def _load_yaml(path: str) -> Dict[str, Any]:
    """YAML 로드(없으면 {})."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _merge_selectors(place_entry: Dict[str, Any], default_cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    전역 defaults + 개별 place override 병합.
    없는 경우 내부 기본값으로 대체.
    """
    defaults = default_cfg.get("defaults", {}) if default_cfg else {}
    # [NEW] 더 넓은 기본 마커 집합 (송파/강남 등 호환)
    result_markers = default_cfg.get("result_markers", [
        ".searchList", ".search-list", ".result-list", ".result_list",
        ".board-list", "ul.board-list", ".book-list", "#resultList",
        "#content", "section.result", "div.result"
    ])

    search_box = place_entry.get("search_box") or defaults.get("search_box") or [
        "#searchKeyword", "#q", "input[name='keyword']", "input[name='searchKey']", "input[type='text']"
    ]
    submit_btn = place_entry.get("submit_btn") or defaults.get("submit_btn") or [
        "#searchBtn", "button.searchBtn", ".btn_search"
    ]

    return {
        "search_box": search_box,
        "submit_btn": submit_btn,
        "result_markers": result_markers,
    }


def _find_first_selector(page, selectors: List[str]) -> Optional[str]:
    """
    셀렉터 배열에서 DOM에 첫 번째로 존재하는 셀렉터 문자열을 반환.
    하나도 없으면 None.
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
# [NEW] Helper: wait for any selector to appear
# --------------------------
def _wait_for_any_selector(page, selectors: List[str], timeout_ms: int = 30000) -> Optional[str]:
    """
    selectors 중 하나라도 DOM에 뜨면 그 셀렉터를 반환. timeout_ms까지 polling (250ms 간격).
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
# [NEW] Helper: progressive homepage navigation
# --------------------------
def _goto_home(page, url: str, expect_selectors: List[str]) -> str:
    """
    Progressive navigation: domcontentloaded → load → commit, then wait for any expect_selectors up to 30s.
    Returns which stage was used: 'domcontentloaded', 'load', or 'commit'.
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
                # Fire-and-forget, ignore errors
                page.goto(url, wait_until="commit", timeout=10000)
            except Exception:
                pass
            stage = "commit"
    # After navigation, wait for any selector (ignore errors)
    try:
        _wait_for_any_selector(page, expect_selectors, timeout_ms=30000)
    except Exception:
        pass
    return stage

# [OLD] 간단한 불리언 체크
# def _results_visible(page, markers: List[str]) -> bool:
#     """
#     결과가 보이는지를 간단히 판단: 마커 중 하나라도 텍스트가 있으면 True.
#     """
#     for sel in markers:
#         try:
#             el = page.query_selector(sel)
#             if el:
#                 txt = (el.inner_text() or "").strip()
#                 if len(txt) > 0:
#                     return True
#         except Exception:
#             pass
#     return False
def _results_evidence(page, title: str, markers: List[str]) -> Dict[str, Any]:
    """
    결과 존재 여부를 다각도로 증명하는 근거를 수집한다.
    반환 구조 예:
    {
      "markers_hit": ["#content", ".searchList"],
      "count_match": true,
      "title_match": true,
      "url": "...",
      "ok": true
    }
    """
    hits: List[str] = []
    # 1) 마커 내 텍스트 유무
    for sel in markers:
        try:
            el = page.query_selector(sel)
            if el:
                txt = (el.inner_text() or "").strip()
                if txt:
                    hits.append(sel)
        except Exception:
            continue

    # 2) 본문에 검색어 포함되는지
    try:
        body_text = page.inner_text("body")
    except Exception:
        body_text = ""
    title_match = (title in body_text) if body_text else False

    # 3) "총 N건" 형태 감지
    import re
    count_match = bool(re.search(r"총\s*\d+\s*건", body_text or ""))

    url = page.url
    ok = bool(hits or title_match or count_match)
    return {"markers_hit": hits, "count_match": count_match, "title_match": title_match, "url": url, "ok": ok}

# --------------------------
# 핵심 로직
# --------------------------
def run_search_and_capture(place: str, title: str) -> Dict[str, Any]:
    """
    LLM 없이 Playwright로 동작:
      1) place의 homepage로 이동
      2) 최소 셀렉터 배열로 검색 시도 (1회 + 재시도 1회: 2s → 4s)
      3) 결과 보이면 결과 페이지 저장, 아니면 현재 페이지 저장(원인 태그 포함)
    반환: {"ok": bool, "saved_html": str, "saved_txt": str, "reason": str, "used": {"input": str|None, "button": str|None}}
    """
    # 1) 설정 로딩
    index = _load_yaml(CATALOG_INDEX_PATH)
    entry = index.get(place) or {}
    home = entry.get("homepage")
    if not home:
        return {"ok": False, "reason": f"no_homepage_for_{place}", "saved_html": "", "saved_txt": "", "used": {"input": None, "button": None}}

    sel_cfg = _merge_selectors(entry, _load_yaml(SELECTORS_PATH))
    search_box = sel_cfg["search_box"]
    submit_btn = sel_cfg["submit_btn"]
    result_markers = sel_cfg["result_markers"]

    # 2) 브라우저 시작
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
        headless=not args.headed,   # --headed 주면 창 표시
        slow_mo=args.slow           # 밀리초 단위 지연 (예: 250이면 동작이 천천히 보여짐)
        )
        # ctx = browser.new_context()
        # page = ctx.new_page()
        ctx = browser.new_context()
        ctx.set_default_navigation_timeout(90_000)  # 90초
        ctx.set_default_timeout(60_000)             # 일반 대기 60초
        page = ctx.new_page()

        # 파일 경로 준비
        out_dir = _today_dir()
        ts = _ts()

        # [OLD] 단일/이중 시도: load → (실패 시) networkidle
        # try:
        #     page.goto(home, wait_until="load", timeout=90_000)
        # except PWTimeout:
        #     page.goto(home, wait_until="networkidle", timeout=90_000)

        # [NEW] 점진 네비게이션: domcontentloaded → load → commit, 이후 검색박스가 뜰 때까지 최대 30s 대기
        nav_stage = _goto_home(page, home, search_box)

        # 4) 검색 1회 시도
        used_input = _find_first_selector(page, search_box)
        # [NEW] 네비게이션은 되었지만 아직 입력박스가 늦게 뜨는 경우를 대비해 추가 대기
        if not used_input:
            used_input = _wait_for_any_selector(page, search_box, timeout_ms=15000)
        used_button = None

        if used_input:
            try:
                inp = page.query_selector(used_input)
                if inp:
                    inp.fill("")          # 기존 값 지우기
                    inp.type(title, delay=10)
            except Exception:
                pass

            # 버튼 클릭 시도
            used_button = _find_first_selector(page, submit_btn)
            if used_button:
                try:
                    page.click(used_button)
                except Exception:
                    used_button = None

            # 버튼이 없으면 Enter
            if not used_button:
                try:
                    page.press(used_input, "Enter")
                    used_button = "ENTER"
                except Exception:
                    used_button = None

            # [NEW] 네트워크 정지까지 잠깐 대기 후 근거 수집
            try:
                page.wait_for_load_state("networkidle", timeout=6_000)
            except Exception:
                pass
            evidence = _results_evidence(page, title, result_markers)

            if not evidence["ok"]:
                # 5) 재시도 1회 (Enter 위주), 4s 대기 + 다시 근거 수집
                if used_input:
                    try:
                        page.press(used_input, "Enter")
                        used_button = used_button or "ENTER"
                    except Exception:
                        pass
                try:
                    page.wait_for_load_state("networkidle", timeout=6_000)
                except Exception:
                    pass
                page.wait_for_timeout(4000)
                evidence = _results_evidence(page, title, result_markers)

        # 6) 저장: 성공/실패에 따라 파일명 태깅 (evidence 기반)
        evidence = locals().get("evidence") or _results_evidence(page, title, result_markers)
        success = bool(evidence.get("ok"))
        if success:
            base = f"{place}_{ts}_results"
            reason = "results_detected"
        else:
            base = f"{place}_{ts}_home_fallback_no_results"
            reason = "no_visible_results"

        html_path = os.path.join(out_dir, f"{base}.html")
        txt_path = os.path.join(out_dir, f"{base}.txt")

        # 현재 페이지 전체 저장
        try:
            _safe_write(html_path, page.content())
        except Exception:
            _safe_write(html_path, "<html><!-- failed to capture HTML --></html>")
        try:
            body_text = page.inner_text("body") if page.query_selector("body") else ""
        except Exception:
            body_text = ""
        _safe_write(txt_path, body_text)

        # 7) 정리
        ctx.close()
        browser.close()

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
            "used": {"input": used_input, "button": used_button},
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

    result = run_search_and_capture(args.place, args.title)
    print(json.dumps(result, ensure_ascii=False, indent=2))

# PYTHONPATH=00_src python -m nodes.search_and_capture --place songpa --title "숨결이 바람 될 때"