# # 00_src/agent/run_once.py
# from __future__ import annotations
# import asyncio
# from pathlib import Path
# from dotenv import load_dotenv

# from browser_use import Agent, ChatOpenAI

# # 경로 주의: 실제 프로젝트에서는 상대 import 조정
# from 00_src.core.paths import result_json_path  
# from 00_src.core.adapters_manager import load_adapter, pick_fallback_url, domain_from_url
# from 00_src.agent.prompts import build_task_two_stage

# # 위 import 경로는 환경에 따라 다음처럼 바꿔야 할 수 있어요:
# # from core.paths import result_json_path
# # from core.adapters_manager import load_adapter, pick_fallback_url, domain_from_url
# # from agent.prompts import build_task_two_stage

# load_dotenv()

# async def main(
#     district_display: str = "강남구",
#     book_title: str = "숨결이 바람 될 때",
# ):
#     # 결과 JSON 저장 경로 생성
#     json_path = result_json_path(district=district_display.replace("구",""), query=book_title)
#     json_path.parent.mkdir(parents=True, exist_ok=True)

#     # fallback URL: district 슬러그는 단순 영문/한글 섞지 말고 한글 소문자/영문 혼용 없이 'gangnam' 같은 걸 추천하지만
#     # 우선 간단히 한글 key로 맞춤 (whitelist에서 gangnam/seocho 키를 쓰면 여긴 변환 필요)
#     district_slug = "gangnam" if "강남" in district_display else None
#     fallback_url = pick_fallback_url(district_slug) if district_slug else None

#     # adapter 힌트: fallback_url에서 도메인을 뽑아보고 시도
#     adapter_hints = None
#     if fallback_url:
#         adapter_hints = load_adapter(domain_from_url(fallback_url))

#     # 태스크 문자열 만들기
#     task = build_task_two_stage(
#         district_display=district_display,
#         book_title=book_title,
#         json_save_path=str(json_path),
#         fallback_url=fallback_url,
#         adapter_hints=adapter_hints,
#     )

#     # LLM/Agent 실행
#     llm = ChatOpenAI(model="gpt-5")
#     agent = Agent(task=task, llm=llm)
#     await agent.run()

# if __name__ == "__main__":
#     asyncio.run(main())


"""
One-shot runner to execute the two-stage search + extract + JSON save.

역할:
- 입력(구 이름, 책 제목)을 받아
- DDG로 카탈로그 진입(필요 시 fallback URL)
- 내부 검색(책 제목만) → 필터 없이 결과 일괄 추출
- 우리 확정 스키마의 JSON 배열을 규칙 경로에 저장

실행 방법:
    # 반드시 저장소 루트에서 모듈 실행 형태로!
    python -m 00_src.agent.run_once

참고:
- 패키지 임포트가 깨지는 경우는 대부분 script 실행(상대 경로) 때문이므로
  항상 `python -m` 형태를 사용한다.
"""

from __future__ import annotations
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from browser_use import Agent, ChatOpenAI

# ✅ 패키지 경로 기준 임포트 (00_src를 패키지로 인식해야 동작)
# from core.paths import result_json_path
# from core.adapters_manager import load_adapter, pick_fallback_url, domain_from_url
# from agent.prompts import build_task_two_stage

from ..core.paths import result_json_path
from ..core.adapters_manager import load_adapter, pick_fallback_url, domain_from_url
from .prompts import build_task_two_stage

load_dotenv()


async def main(district_display: str = "강남구", book_title: str = "숨결이 바람 될 때") -> None:
    """
    두 단계 검색을 수행하고 결과를 JSON으로 저장한다.

    Args:
        district_display: 예) "강남구"
        book_title: 예) "숨결이 바람 될 때"
    """
    # 결과 JSON 저장 경로 생성
    json_path = result_json_path(district=district_display.replace("구", ""), query=book_title)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    # 간단한 슬러그 변환 (화이트리스트 키 맞추기)
    district_slug = "gangnam" if "강남" in district_display else None
    fallback_url = pick_fallback_url(district_slug) if district_slug else None

    # adapter 힌트: fallback_url 기준 도메인에서 로드
    adapter_hints = None
    if fallback_url:
        adapter_hints = load_adapter(domain_from_url(fallback_url))

    # 태스크 문자열 생성
    task = build_task_two_stage(
        district_display=district_display,
        book_title=book_title,
        json_save_path=str(json_path),
        fallback_url=fallback_url,
        adapter_hints=adapter_hints,
    )

    # LLM/Agent 실행
    llm = ChatOpenAI(model="gpt-5")
    agent = Agent(task=task, llm=llm)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())