"""
Setup:
1. Get your API key from https://cloud.browser-use.com/dashboard/api
2. Set environment variable: export BROWSER_USE_API_KEY="your-key"
"""

# from dotenv import load_dotenv

from browser_use import Agent, ChatOpenAI
# from browser_use.llm import ChatBrowserUse

llm = ChatOpenAI(
    model="o3",
)


# load_dotenv()

agent = Agent(
	task='나는 현재 서울시 자곡로 11길 28에 살고 있다. 내 근처에 있는 도서관에서 숨결이 바람 될 때 라는 책이 대출 가능한지 확인 하려고 한다. 니가 알아서 검색해서 알려 줘',
	llm=llm,
)
agent.run_sync()
