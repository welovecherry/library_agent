# # 00_src/core/adapters_manager.py
# from __future__ import annotations
# from pathlib import Path
# from typing import Optional, Dict
# from urllib.parse import urlparse
# import yaml

# ROOT = Path(__file__).resolve().parents[1]  # 00_src/
# ADAPTER_DIR = ROOT / "configs" / "adapters"
# WHITELIST = ROOT / "configs" / "catalog_whitelist.yaml"

# def load_adapter(domain: str) -> Optional[Dict]:
#     path = ADAPTER_DIR / f"{domain}".lower() / "noop"  # 도메인 디렉터리 방식도 고려했다가 파일로 고정
#     # 실제는 파일명.yaml 구조
#     file_path = ADAPTER_DIR / f"{domain.lower()}.yaml"
#     if file_path.exists():
#         return yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
#     return None

# def load_whitelist() -> Dict[str, str]:
#     if WHITELIST.exists():
#         return yaml.safe_load(WHITELIST.read_text(encoding="utf-8")) or {}
#     return {}

# def pick_fallback_url(district_slug: str) -> Optional[str]:
#     wl = load_whitelist()
#     return wl.get(district_slug, None)

# def domain_from_url(url: str) -> str:
#     return urlparse(url).netloc.lower()

"""
Adapter & whitelist utility helpers.

역할:
- 도메인별 어댑터 YAML 로드 (00_src/configs/adapters/{domain}.yaml)
- 구/지역별 fallback 카탈로그 URL 로드 (00_src/configs/catalog_whitelist.yaml)
- URL에서 도메인 추출

주의:
- 이 모듈은 네트워크 호출을 하지 않는다. 오로지 파일 로드/파싱만 담당한다.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional, Dict
from urllib.parse import urlparse
import yaml

# 00_src/
ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIR = ROOT / "configs" / "adapters"
WHITELIST = ROOT / "configs" / "catalog_whitelist.yaml"


def load_adapter(domain: str) -> Optional[Dict]:
    """
    특정 도메인에 대한 어댑터 YAML을 로드한다.

    Args:
        domain: 예) "library.gangnam.go.kr"

    Returns:
        dict | None: YAML 파싱 결과(없으면 None)
    """
    file_path = ADAPTER_DIR / f"{domain.lower()}.yaml"
    if file_path.exists():
        return yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    return None


def load_whitelist() -> Dict[str, str]:
    """
    카탈로그 진입용 화이트리스트 맵을 로드한다.

    Returns:
        dict: {"gangnam": "https://.../index.do", ...}
    """
    if WHITELIST.exists():
        return yaml.safe_load(WHITELIST.read_text(encoding="utf-8")) or {}
    return {}


def pick_fallback_url(district_slug: str) -> Optional[str]:
    """
    슬러그(예: 'gangnam')로 fallback 카탈로그 URL을 가져온다.
    """
    wl = load_whitelist()
    return wl.get(district_slug, None)


def domain_from_url(url: str) -> str:
    """
    URL에서 도메인(netloc)을 소문자로 추출한다.
    """
    return urlparse(url).netloc.lower()