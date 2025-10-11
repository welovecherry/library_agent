from browser_use import Agent, ChatOpenAI
from dotenv import load_dotenv
import asyncio

load_dotenv()

async def main():
    llm = ChatOpenAI(model="gpt-5")
    task = "나는 현재 서울시 자곡로 11길 28에 살고 있다. 내 근처에 있는 도서관에서  '숨결이 바람 될 때' 라는 책이 대출 가능한지 확인 하려고 한다. 니가 알아서 검색해서 알려 줘."
    agent = Agent(task=task, llm=llm)
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())