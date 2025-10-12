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
    with open(path, "w", encoding="utf-8") as f:
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
# 핵심 로직: 검색 수행 및 결과 저장
# --------------------------
def run_search_and_capture(place: str, title: str) -> Dict[str, Any]:
    """
    Playwright를 사용하여 LLM 없이 다음 작업을 수행한다:
      1) place에 해당하는 홈페이지로 점진적 네비게이션 수행
      2) 검색 입력란과 버튼에 대해 셀렉터 배열에서 첫 번째 유효한 요소를 찾아 검색 1회 수행
      3) 검색 결과가 감지되지 않으면 재시도 1회 수행 (첫 시도 후 2초 대기, 재시도 후 4초 대기)
      4) 결과가 감지되면 결과 페이지 HTML과 텍스트 저장
      5) 실패 시 현재 페이지 HTML과 텍스트 저장 (실패 원인 태그 포함)
    반환값: 딕셔너리 형태로 성공 여부, 저장 경로, 사용된 셀렉터 정보 등 포함
    """
    # 1) 설정 로드 및 place 유효성 확인
    index = _load_yaml(CATALOG_INDEX_PATH)
    entry = index.get(place) or {}
    home = entry.get("homepage")
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
            headless=not args.headed,   # --headed 옵션으로 창 표시 여부 결정
            slow_mo=args.slow           # slow_mo 옵션으로 동작 속도 조절 (ms 단위)
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

        # 6) 검색 입력란 셀렉터 찾기 및 대기 (최초 시도)
        used_input = _find_first_selector(page, search_box)
        if not used_input:
            # 입력란이 늦게 뜰 수 있으므로 최대 15초 대기 후 재탐색
            used_input = _wait_for_any_selector(page, search_box, timeout_ms=15000)
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

            # 10) 네트워크 안정 상태까지 잠시 대기 후 결과 근거 수집
            try:
                page.wait_for_load_state("networkidle", timeout=6_000)
            except Exception:
                pass
            evidence = _results_evidence(page, title, result_markers)

            # 11) 결과 미감지 시 재시도 1회 (Enter 위주), 4초 대기 후 근거 재수집
            if not evidence["ok"]:
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

        # 12) 결과 감지 여부에 따라 저장 경로 및 상태 결정
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

        # 13) 현재 페이지 전체 HTML 저장
        try:
            _safe_write(html_path, page.content())
        except Exception:
            _safe_write(html_path, "<html><!-- failed to capture HTML --></html>")
        # 14) 페이지 본문 텍스트 저장 (body 태그가 있으면)
        try:
            body_text = page.inner_text("body") if page.query_selector("body") else ""
        except Exception:
            body_text = ""
        _safe_write(txt_path, body_text)

        # 15) 브라우저 자원 정리
        ctx.close()
        browser.close()

        # 16) 최종 결과 반환
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