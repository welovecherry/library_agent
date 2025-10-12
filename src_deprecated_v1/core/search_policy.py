# LLM이 “도서관 홈페이지를 스스로 찾아 들어가 책을 검색”할 수 있게 한다.

# 00_src/core/search_policy.py
# 목적:
# - 사용자가 입력한 장소(주소/구/도시) + 책 제목으로 검색 쿼리들을 만들고
# - 검색 결과 URL 후보들에 대해 "도서관/공공성" 점수를 매겨 최적 URL/도메인을 고른다.
# - 도메인별 어댑터(adapters/*.yaml)가 있으면 가산점을 주고, 힌트를 함께 반환한다.
#
# 이 파일은 "네트워크 요청"을 직접 하지 않는다. (검색/탐색은 browser-use가 수행)
# 여기선 '정책/스코어링/힌트 로딩'만 담당한다.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse
import re
import json

try:
    import yaml  # PyYAML (requirements.txt에 추가 권장)
except Exception:  # yaml이 없으면 어댑터 로딩만 비활성
    yaml = None


# ====== 경로 기본값 ======
ROOT = Path(__file__).resolve().parents[1]  # 00_src/
ADAPTER_DIR = ROOT / "configs" / "adapters"

# ====== 스코어링 상수 ======
# 도메인/경로/텍스트 힌트에 따른 가산점
SCORES = {
    "domain_suffix": {
        ".go.kr": 8.0,
        ".lib": 6.0,            # 예: something.lib.seoul.kr (부분 포함 판정)
        "library": 4.0,         # 예: library.* 도메인
        "splib": 6.0,           # 예: *.splib.or.kr
    },
    "path_keywords": {
        "search": 3.0,
        "catalog": 3.0,
        "integrated": 2.0,
        "plusSearchResultList": 2.5,  # 강남 예시
        "자료": 2.0,
        "검색": 2.0,
        "소장": 2.0,
    },
    "bad_indicators": {
        "naver": -3.0,
        "blog": -4.0,
        "youtube": -6.0,
        "twitter": -4.0,
        "x.com": -4.0,
        "tistory": -3.0,
        "news": -2.0,
        "map": -2.0,
        "ad": -5.0,
    },
    "adapter_bonus": 5.0,       # 어댑터가 있으면 가산
    "title_bonus_if_contains": 1.0,  # URL 문자열에 책 키워드가 일부 보이면 소폭 가산
}

# 검색 키워드 템플릿
QUERY_TEMPLATES = [
    "{place} 공공도서관 통합검색 {title}",
    "{place} 도서관 자료검색 {title}",
    "{place} 구립도서관 소장 {title}",
    "{place} library catalog {title}",
    # 스코프 좁히기 (공공 도메인 우선)
    "{place} 도서관 {title} site:go.kr",
    "{place} 도서관 {title} site:or.kr",
    "{place} 도서관 {title} site:lib",
]

# 결과 구조체
@dataclass
class DomainChoice:
    url: str
    domain: str
    score: float
    reason: List[str]
    adapter: Optional[Dict] = None  # 로드된 어댑터 힌트


# ========== 유틸 ==========

def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _slugify_for_filename(text: str) -> str:
    # 파일명에 쓸 수 있게 간단 정규화 (공백->_, 한글/영문/숫자/._-만 허용)
    text = _normalize_spaces(text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^0-9a-zA-Z._\-\uac00-\ud7a3]", "", text)
    return text


def _domain_contains(domain: str, token: str) -> bool:
    return token in domain.lower()


def _path_contains(path: str, token: str) -> bool:
    return token.lower() in path.lower()


def _load_adapter_for_domain(domain: str) -> Optional[Dict]:
    """
    configs/adapters/{domain}.yaml 파일이 있으면 로드.
    ex) library.gangnam.go.kr.yaml
    """
    if yaml is None:
        return None
    candidate = ADAPTER_DIR / f"{domain}.yaml"
    if candidate.exists():
        try:
            return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
    return None


# ========== 핵심 로직 ==========

def build_search_queries(place: str, title: str) -> List[str]:
    """장소+제목으로 다양한 쿼리 문자열 생성."""
    place = _normalize_spaces(place)
    title = _normalize_spaces(title)
    queries = [tpl.format(place=place, title=title) for tpl in QUERY_TEMPLATES]
    # 중복 제거 유지순서
    seen = set()
    uniq = []
    for q in queries:
        if q not in seen:
            uniq.append(q)
            seen.add(q)
    return uniq


def score_candidate_url(url: str, title: str) -> Tuple[float, List[str]]:
    """
    URL 문자열만으로 공공/도서관/카탈로그 가능성을 점수화.
    (실제 페이지 내용은 browser-use가 본다. 여기는 1차 필터)
    """
    reasons = []
    score = 0.0
    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower()
        path_q = (parsed.path + " " + (parsed.query or "")).lower()

        # 도메인 가산점
        for suffix, s in SCORES["domain_suffix"].items():
            if suffix in domain:
                score += s
                reasons.append(f"+{s} domain has '{suffix}'")

        # 경로/쿼리 키워드 가산점
        for kw, s in SCORES["path_keywords"].items():
            if _path_contains(path_q, kw):
                score += s
                reasons.append(f"+{s} path contains '{kw}'")

        # 불량 지표 감점
        for bad, s in SCORES["bad_indicators"].items():
            if bad in domain or _path_contains(path_q, bad):
                score += s
                reasons.append(f"{s} bad indicator '{bad}'")

        # 제목 일부가 URL에 보이면 소폭 가산
        if any(tok for tok in title.split() if tok and tok.lower() in url.lower()):
            score += SCORES["title_bonus_if_contains"]
            reasons.append(f"+{SCORES['title_bonus_if_contains']} title token appears in URL")

    except Exception as e:
        reasons.append(f"parse_error: {e}")

    return score, reasons


def choose_best_domain(
    candidates: List[str],
    title: str,
) -> Optional[DomainChoice]:
    """
    검색엔진에서 수집된 URL 후보들 중 최적 후보 선택.
    - 도메인 스코어
    - 경로 키워드
    - 어댑터 보유 시 가산
    """
    best: Optional[DomainChoice] = None
    for url in candidates:
        base_score, reasons = score_candidate_url(url, title)
        domain = urlparse(url).netloc.lower()
        adapter = _load_adapter_for_domain(domain)
        if adapter:
            base_score += SCORES["adapter_bonus"]
            reasons.append(f"+{SCORES['adapter_bonus']} adapter exists for {domain}")

        choice = DomainChoice(url=url, domain=domain, score=base_score, reason=reasons, adapter=adapter)
        if (best is None) or (choice.score > best.score):
            best = choice

    return best


def render_agent_hint(choice: DomainChoice) -> Dict:
    """
    browser-use가 바로 활용할 수 있는 힌트 번들 생성.
    - adapter가 있으면 그 내용을 우선 반영
    - 없으면 빈 힌트 (자율 탐색)
    """
    hint = {
        "start_url": choice.url,
        "domain": choice.domain,
        "search_hints": {
            "box": [],
            "submit": [],
        },
        "result_hints": {
            "list_candidates": [],
        },
        "labels": {},
        "reason": choice.reason,
        "score": choice.score,
        "adapter_used": bool(choice.adapter),
    }
    if choice.adapter:
        # 어댑터 스키마 예:
        # {
        #   domain: "library.gangnam.go.kr",
        #   search_box_hints: ["#totalSearch", "input[name='keyword']"],
        #   submit_hints: ["#searchBtn"],
        #   result_item_hints: [".result_list li"],
        #   labels: {...}
        # }
        hint["search_hints"]["box"] = choice.adapter.get("search_box_hints", [])
        hint["search_hints"]["submit"] = choice.adapter.get("submit_hints", [])
        hint["result_hints"]["list_candidates"] = choice.adapter.get("result_item_hints", [])
        hint["labels"] = choice.adapter.get("labels", {})

    return hint


# def build_policy(place: str, title: str, engine_order: Optional[List[str]] = None) -> Dict:
#     """
#     browser-use 상위 레이어가 호출하는 진입점:
#     - 검색 쿼리 리스트
#     - 엔진 우선순위
#     - URL 후보 선택을 위한 스코어링 규칙 안내(문서용)
#     """
#     queries = build_search_queries(place, title)
#     engines = engine_order or ["duckduckgo", "google", "bing"]
#     return {
#         "queries": queries,
#         "engines": engines,
#         "scoring_rules": {
#             "domain_suffix": SCORES["domain_suffix"],
#             "path_keywords": SCORES["path_keywords"],
#             "bad_indicators": SCORES["bad_indicators"],
#             "adapter_bonus": SCORES["adapter_bonus"],
#         },
#     }

def build_policy(place: str, title: str, engine_order: Optional[List[str]] = None) -> Dict:
    """
    browser-use 상위 레이어가 호출하는 진입점:
    - 검색 쿼리 리스트
    - 엔진 우선순위 (우리 정책: DDG only)
    - URL 후보 선택을 위한 스코어링 규칙 안내(문서용)
    """
    queries = build_search_queries(place, title)
    # 리캡차/레이아웃 변동 최소화를 위해 DuckDuckGo만 사용
    engines = ["duckduckgo"]
    return {
        "queries": queries,
        "engines": engines,
        "scoring_rules": {
            "domain_suffix": SCORES["domain_suffix"],
            "path_keywords": SCORES["path_keywords"],
            "bad_indicators": SCORES["bad_indicators"],
            "adapter_bonus": SCORES["adapter_bonus"],
        },
    }

print("ADAPTER_DIR =", ADAPTER_DIR.resolve())
print("EXPECT_FILE =", (ADAPTER_DIR / "library.gangnam.go.kr.yaml").resolve())
print("EXISTS?     =", (ADAPTER_DIR / "library.gangnam.go.kr.yaml").exists())

# ====== (선택) 간단한 수동 테스트용 ======
# ====== (선택) 간단한 수동 테스트용 ======
if __name__ == "__main__":
    """
    Manual smoke test for search policy.

    목적:
      - 1단계(카탈로그 진입)를 가정한 '장소 전용' 검색 쿼리들이 잘 생성되는지 확인.
      - URL 후보 스코어링이 '루트/카탈로그 진입용' URL을 우선 선택하는지 확인.

    주의:
      - 결과페이지 전용 URL(예: plusSearchResultList.do?q=...)은 직접 호출하면 실패할 수 있으므로
        테스트 후보에서도 루트/카탈로그 진입용 URL을 사용한다.
    """
    # 예시: place/title을 넣되, place만으로 SERP를 친다는 가정
    sample = build_policy(place="강남구", title="숨결이 바람 될 때")
    print(json.dumps(sample, ensure_ascii=False, indent=2))

    # URL 후보를 가정하고 스코어링 테스트 (✅ 루트/카탈로그 진입 URL 위주)
    fake_candidates = [
        # 🟢 강남구 통합도서관 루트(진입용, 안전)
        "https://library.gangnam.go.kr/intro/index.do",
        # 🟢 송파(예: splib) 검색/카탈로그로 보이는 경로
        "https://www.splib.or.kr/search",
        # 🔴 일반 포털/블로그/뉴스(감점 대상)
        "https://www.naver.com/search?q=강남구립도서관",
        "https://example.com/blog/post",
    ]

    best = choose_best_domain(fake_candidates, title="숨결이 바람 될 때")
    if best:
        hint = render_agent_hint(best)
        print("\n[Best Choice]")
        print(json.dumps(hint, ensure_ascii=False, indent=2))