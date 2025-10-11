<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./static/browser-use-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="./static/browser-use.png">
  <img alt="Shows a black Browser Use Logo in light color mode and a white one in dark color mode." src="./static/browser-use.png"  width="full">
</picture>

<h1 align="center">Enable AI to control your browser</h1>

[![Docs](https://img.shields.io/badge/Docs-📕-blue?style=for-the-badge)](https://docs.browser-use.com)
[![Browser-use cloud](https://img.shields.io/badge/Browser_Use_Cloud-☁️-blue?style=for-the-badge&logo=rocket&logoColor=white)](https://cloud.browser-use.com)

[![Discord](https://img.shields.io/discord/1303749220842340412?color=7289DA&label=Discord&logo=discord&logoColor=white)](https://link.browser-use.com/discord)
[![Twitter Follow](https://img.shields.io/twitter/follow/Gregor?style=social)](https://x.com/intent/user?screen_name=gregpr07)
[![Twitter Follow](https://img.shields.io/twitter/follow/Magnus?style=social)](https://x.com/intent/user?screen_name=mamagnus00)
[![Merch store](https://img.shields.io/badge/Merch_store-👕-blue)](https://browsermerch.com)
[![Weave Badge](https://img.shields.io/endpoint?url=https%3A%2F%2Fapp.workweave.ai%2Fapi%2Frepository%2Fbadge%2Forg_T5Pvn3UBswTHIsN1dWS3voPg%2F881458615&labelColor=#EC6341)](https://app.workweave.ai/reports/repository/org_T5Pvn3UBswTHIsN1dWS3voPg/881458615)





<!-- Keep these links. Translations will automatically update with the README. -->
[Deutsch](https://www.readme-i18n.com/browser-use/browser-use?lang=de) |
[Español](https://www.readme-i18n.com/browser-use/browser-use?lang=es) |
[français](https://www.readme-i18n.com/browser-use/browser-use?lang=fr) |
[日本語](https://www.readme-i18n.com/browser-use/browser-use?lang=ja) |
[한국어](https://www.readme-i18n.com/browser-use/browser-use?lang=ko) |
[Português](https://www.readme-i18n.com/browser-use/browser-use?lang=pt) |
[Русский](https://www.readme-i18n.com/browser-use/browser-use?lang=ru) |
[中文](https://www.readme-i18n.com/browser-use/browser-use?lang=zh)


# 🤖 Quickstart

With uv (Python>=3.11):

```bash
#  We ship every day - use the latest version!
uv pip install browser-use
```

Download chromium using playwright's shortcut:

```bash
uvx playwright install chromium --with-deps --no-shell
```

Get your API key from [Browser Use Cloud](https://cloud.browser-use.com/dashboard/api) and add it to your `.env` file:

```bash
BROWSER_USE_API_KEY=your-key
```

Run your first agent:

```python
from browser_use import Agent
from browser_use.llm import ChatBrowserUse

agent = Agent(
    task="나는 현재 서울시 자곡로 11길 28에 살고 있다. 내 근처에 있는 도서관에서  '숨결이 바람 될 때' 라는 책이 대출 가능한지 확인 하려고 한다. 니가 알아서 검색해서 알려 줘.",
    llm=ChatBrowserUse(),
)
agent.run_sync()
```

Check out the [library docs](https://docs.browser-use.com) and [cloud docs](https://docs.cloud.browser-use.com) for more settings.


## Stealth Browser Infrastructure

Want to bypass Cloudflare, or any other anti-bot protection?

Simply go to [Browser Use Cloud](https://docs.cloud.browser-use.com) grab a `BROWSER_USE_API_KEY` and use the `use_cloud` parameter.

```python
from browser_use import Agent, Browser
from browser_use.llm import ChatBrowserUse

# Use Browser-Use cloud browser service
browser = Browser(
    use_cloud=True,  # Automatically provisions a cloud browser
)

agent = Agent(
    task="Your task here",
    llm=ChatBrowserUse(),
    browser=browser,
)
```



# Demos

[Task](https://github.com/browser-use/browser-use/blob/main/examples/use-cases/shopping.py): Add grocery items to cart, and checkout.

[![AI Did My Groceries](https://github.com/user-attachments/assets/a0ffd23d-9a11-4368-8893-b092703abc14)](https://www.youtube.com/watch?v=L2Ya9PYNns8)

<br/><br/>


[Task](https://github.com/browser-use/browser-use/blob/main/examples/use-cases/find_and_apply_to_jobs.py): Read my CV & find ML jobs, save them to a file, and then start applying for them in new tabs, if you need help, ask me.

https://github.com/user-attachments/assets/171fb4d6-0355-46f2-863e-edb04a828d04

<br/><br/>

See [more examples](https://docs.browser-use.com/examples) and give us a star!


<br/><br/>
## MCP Integration

This gives Claude Desktop access to browser automation tools for web scraping, form filling, and more. See the [MCP docs](https://docs.browser-use.com/customize/mcp-server).
```json
{
  "mcpServers": {
    "browser-use": {
      "command": "uvx",
      "args": ["browser-use[cli]", "--mcp"],
      "env": {
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

<div align="center">
  
**Tell your computer what to do, and it gets it done.**

<img src="https://github.com/user-attachments/assets/06fa3078-8461-4560-b434-445510c1766f" width="400"/>

[![Twitter Follow](https://img.shields.io/twitter/follow/Magnus?style=social)](https://x.com/intent/user?screen_name=mamagnus00)
[![Twitter Follow](https://img.shields.io/twitter/follow/Gregor?style=social)](https://x.com/intent/user?screen_name=gregpr07)

</div>

<div align="center">
Made with ❤️ in Zurich and San Francisco
 </div>
