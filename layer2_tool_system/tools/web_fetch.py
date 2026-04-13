import urllib.request
import urllib.error
from .base import Tool

MAX_BYTES = 50_000  # 截断超长页面


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "获取 URL 的文本内容（HTML/JSON/纯文本），截断超过 50KB 的响应。"
    is_read_only = True
    is_concurrency_safe = True

    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的 URL"},
        },
        "required": ["url"],
    }

    async def call(self, input: dict) -> str:
        import asyncio
        url = input["url"]
        try:
            # urllib 是同步的，放 executor 里跑
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._fetch, url)
            return result
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "WhaleFin/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read(MAX_BYTES)
                text = raw.decode(resp.headers.get_content_charset("utf-8"), errors="replace")
                if resp.length and resp.length > MAX_BYTES:
                    text += f"\n... (truncated, fetched {MAX_BYTES} of {resp.length} bytes)"
                return text
        except urllib.error.HTTPError as e:
            return f"ERROR: HTTP {e.code} {e.reason}"
        except urllib.error.URLError as e:
            return f"ERROR: URLError: {e.reason}"
