# 00_src/core/llm_factory.py
from __future__ import annotations
"""
LLM 팩토리: 설정/환경변수로 ChatOpenAI 생성
- LangChain 없이도 모델 교체를 간단하게.
- 환경변수 우선: LLM_MODEL, LLM_API_KEY, LLM_BASE_URL
- 설정파일(TOML) 보조: configs/settings.toml 의 [llm] 섹션
"""

import os
from typing import Optional
from browser_use import ChatOpenAI

try:
    import tomllib  # py>=3.11
except Exception:
    import tomli as tomllib  # py<3.11 대응 (설치되어 있으면)

def _read_settings_model(default: str = "gpt-5-mini") -> str:
    """settings.toml에서 [llm].model 읽기. 없으면 default."""
    here = os.path.dirname(os.path.dirname(__file__))  # 00_src/
    cfg = os.path.join(here, "configs", "settings.toml")
    if not os.path.exists(cfg):
        return default
    try:
        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        return data.get("llm", {}).get("model", default)
    except Exception:
        return default

def llm_from_settings() -> ChatOpenAI:
    """
    환경변수/설정으로 ChatOpenAI 인스턴스 생성.
    - LLM_MODEL: 모델 이름 (예: 'gpt-4o-mini', 'gpt-4o', 'gpt-5' 등)
    - LLM_API_KEY: OpenAI 호환 키 (없으면 기존 방식)
    - LLM_BASE_URL: OpenAI 호환 엔드포인트 (예: 프록시/로컬 등)
    """
    model = os.getenv("LLM_MODEL") or _read_settings_model()
    api_key: Optional[str] = os.getenv("LLM_API_KEY")
    base_url: Optional[str] = os.getenv("LLM_BASE_URL")

    # browser_use.ChatOpenAI 는 OpenAI 호환 스펙을 따르므로
    # base_url / api_key 를 전달하면 대체 프로바이더도 사용 가능.
    if api_key or base_url:
        return ChatOpenAI(model=model, api_key=api_key, base_url=base_url)
    return ChatOpenAI(model=model)