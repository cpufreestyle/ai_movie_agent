"""A 阶段 · 资料采集。

封装开源 [Crawl4AI](https://github.com/unclecode/crawl4ai)（LLM 友好爬虫）。
未安装 / 未配置采集源时降级为 requests 抓取给定 URL，或返回空（管线继续）。

Skill 方法论见 skills/A_collector.md。
"""
from __future__ import annotations

import os
import re

from .llmutil import log


class Collector:
    def __init__(self, config: dict, workdir: str):
        self.cfg = config.get("collector", {}) or {}
        self.dir = os.path.join(workdir, "material")
        os.makedirs(self.dir, exist_ok=True)

    def collect(self, topic: str) -> list[dict]:
        urls = self.cfg.get("urls") or []
        items: list[dict] = []
        if self.cfg.get("method") == "crawl4ai" and urls:
            items = self._crawl(urls)
        if not items and urls:
            items = self._fetch(urls)
        if not items:
            log("  [A 资料采集] 未配置采集源(config.collector.urls)，跳过。"
                  "可在 config 填入起始链接列表。")
        for i, it in enumerate(items):
            with open(os.path.join(self.dir, f"doc_{i+1}.md"), "w", encoding="utf-8") as f:
                f.write(f"# {it.get('url', '')}\n\n{it.get('text', '')}\n")
        return items

    def _crawl(self, urls: list[str]) -> list[dict]:
        try:
            import asyncio
            import crawl4ai
        except Exception as e:
            log(f"  [A] crawl4ai 未安装，降级 requests: {e}")
            return self._fetch(urls)
        async def run():
            out = []
            async with crawl4ai.AsyncWebCrawler() as c:
                for u in urls:
                    r = await c.arun(u)
                    out.append({"url": u, "text": getattr(r, "markdown", str(r))})
            return out
        try:
            return asyncio.run(run())
        except Exception as e:
            log(f"  [A] crawl4ai 运行失败: {e}")
            return self._fetch(urls)

    def _fetch(self, urls: list[str]) -> list[dict]:
        try:
            import requests
        except Exception:
            return []
        out = []
        for u in urls:
            try:
                r = requests.get(u, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                txt = re.sub(r"<script.*?</script>", "", r.text, flags=re.S)
                txt = re.sub(r"<style.*?</style>", "", txt, flags=re.S)
                txt = re.sub(r"<[^>]+>", " ", txt)
                txt = re.sub(r"\s+", " ", txt).strip()
                out.append({"url": u, "text": txt[:8000]})
            except Exception as e:
                log(f"  [A] 抓取失败 {u}: {e}")
        return out
