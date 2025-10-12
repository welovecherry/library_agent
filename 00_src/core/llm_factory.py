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

def llm_from_settings() -> ChatOpenAI:
    """
    환경변수/설정으로 ChatOpenAI 인스턴스 생성.
    - LLM_MODEL: 모델 이름 (예: 'gpt-4o-mini', 'gpt-4o', 'gpt-5' 등)
    - LLM_API_KEY: OpenAI 호환 키 (없으면 기존 방식)
    """
    model = os.getenv("LLM_MODEL", "gpt-5-mini")
    api_key: Optional[str] = os.getenv("LLM_API_KEY")

    # if api_key:
    return ChatOpenAI(model=model, api_key=api_key)
    # return ChatOpenAI(model=model)