# B 阶段 · 知识沉淀 — Skill 方法论

## 职责
把 A 阶段采集的素材切分、入库，提供按主题/查询的**检索**，供 C 企划取用。

## 方法论（固化自 RAGFlow）
- 文档按语义/版面切块（RAGFlow 擅长中文 PDF/网页的"智能切块"）。
- 检索阶段用混合检索（向量 + 关键词），返回最相关片段喂给 LLM。
- 未自建 RAGFlow 服务时降级：本地 `chunks.jsonl` + 朴素关键词命中（够用）。

## 工具
`agent/knowledge.py :: Knowledge.ingest(items)` / `retrieve(query, k)`
- 配置：`config.knowledge.{backend, local_dir, ragflow.*}`
- `backend: ragflow` 时调用官方 HTTP API（`/api/v1`）：
  - 上传：`POST /api/v1/datasets` 建库 → `POST /datasets/{id}/documents`（multipart）
    → `POST /datasets/{id}/chunks` 触发解析；
  - 检索：`POST /api/v1/retrieval` 取 `data.chunks[].content`。

## 配置
`config.knowledge.ragflow.{api, dataset, api_key}`：api 指向 RAGFlow 服务
（如 `http://localhost:80/api`），api_key 从 RAGFlow 网页复制。

## 兜底
- `backend: local` → 关键词检索，零依赖；
- RAGFlow 未配置/不可达/报错 → 自动降级本地 store，不中断管线。
