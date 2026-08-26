# A 阶段 · 资料采集 — Skill 方法论

## 职责
从互联网采集与主题相关的素材（文章、资料页），作为后续知识沉淀与企划的输入。

## 方法论（固化自 Crawl4AI）
- 以"主题"为种子，配置若干权威起始 URL（避免无目标全网爬）。
- 优先用 Crawl4AI 的 `AsyncWebCrawler` 直接产出**干净 markdown**（适合 LLM 消费）。
- 未装 Crawl4AI / 无 URL 时降级：`requests` 抓取 + 简单去标签，或跳过。

## 工具
`agent/collector.py :: Collector.collect(topic)`
- 配置：`config.collector.{method, urls, save_dir}`
- 产出：`list[{"url","text"}]`，落盘到 `outputs/material/*.md`

## 兜底
`urls: []` → 打印提示并跳过，流水线继续（后续阶段用主题本身）。
