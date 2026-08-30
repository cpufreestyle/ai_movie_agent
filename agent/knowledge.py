"""B 阶段 · 知识沉淀。

后端可切换：
- local：本地朴素检索（关键词命中），零依赖，开箱即用；
- ragflow：对接开源 [RAGFlow](https://github.com/infiniflow/ragflow)（中文文档理解强），
  通过官方 HTTP API（/api/v1）上传文档并检索。

Skill 方法论见 skills/B_knowledge.md。
"""
from __future__ import annotations

import json
import os
import re


class Knowledge:
    def __init__(self, config: dict, workdir: str):
        self.cfg = config.get("knowledge", {}) or {}
        self.dir = os.path.join(workdir, "kb")
        os.makedirs(self.dir, exist_ok=True)
        self.store_path = os.path.join(self.dir, "chunks.jsonl")
        rc = self.cfg.get("ragflow", {}) or {}
        self._ragflow = {
            "base": (rc.get("api") or "").rstrip("/"),
            "dataset": rc.get("dataset", "ai_movie"),
            "key": rc.get("api_key", ""),
        }

    def ingest(self, items: list[dict]) -> "Knowledge":
        chunks = []
        for it in items:
            text = it.get("text", "")
            for chunk in re.split(r"\n{2,}", text):
                chunk = chunk.strip()
                if len(chunk) > 30:
                    chunks.append({"src": it.get("url", ""), "text": chunk[:1500]})
        with open(self.store_path, "w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        if self.cfg.get("backend") == "ragflow":
            self._push_ragflow(items)
        print(f"  [B 知识沉淀] 入库 {len(chunks)} 个片段 -> {self.store_path}")
        return self

    def retrieve(self, query: str, k: int = 5) -> list[str]:
        if self.cfg.get("backend") == "ragflow":
            r = self._retrieve_ragflow(query, k)
            if r is not None:
                return r
        if not os.path.exists(self.store_path):
            return []
        with open(self.store_path, encoding="utf-8") as f:
            chunks = [json.loads(line) for line in f]
        q_words = set(query.lower().split())
        scored = [(sum(1 for w in q_words if w in c["text"].lower()), c)
                  for c in chunks]
        scored.sort(key=lambda x: -x[0])
        top = [c["text"] for _, c in scored[:k]]
        return top

    # ---- RAGFlow 后端（官方 HTTP API /api/v1）----
    def _ragflow_cfg(self):
        return self._ragflow

    def _ragflow_headers(self):
        return {"Authorization": f"Bearer {self._ragflow['key']}"}

    def _resolve_dataset(self, base, headers, name):
        # 先查是否已存在
        try:
            import requests
            r = requests.get(f"{base}/api/v1/datasets", headers=headers,
                             params={"name": name}, timeout=15)
            if r.ok:
                data = r.json().get("data") or []
                if data:
                    return data[0]["id"]
            # 不存在则创建
            r = requests.post(f"{base}/api/v1/datasets", headers=headers,
                              json={"name": name}, timeout=15)
            if r.ok:
                return (r.json().get("data") or {}).get("id")
        except Exception as e:
            print(f"  [B] RAGFlow 解析数据集失败: {e}")
        return None

    def _push_ragflow(self, items):
        cfg = self._ragflow_cfg()
        if not cfg["base"] or not cfg["key"]:
            print("  [B] RAGFlow 未配置 api/api_key，跳过云端入库（仍保留本地 store）。")
            return
        try:
            import requests
            base, headers = cfg["base"], self._ragflow_headers()
            ds_id = self._resolve_dataset(base, headers, cfg["dataset"])
            if not ds_id:
                print("  [B] RAGFlow 无法获取数据集，跳过云端入库。")
                return
            doc_ids = []
            for i, it in enumerate(items):
                path = os.path.join(self.dir, f"_rag_{i+1}.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"{it.get('url','')}\n\n{it.get('text','')}")
                with open(path, "rb") as fh:
                    r = requests.post(f"{base}/api/v1/datasets/{ds_id}/documents",
                                      headers=headers, files={"file": fh}, timeout=60)
                if r.ok:
                    for d in (r.json().get("data") or []):
                        doc_ids.append(d["id"])
                os.remove(path)
            if doc_ids:
                requests.post(f"{base}/api/v1/datasets/{ds_id}/chunks",
                              headers=headers, json={"document_ids": doc_ids}, timeout=60)
                print(f"  [B] RAGFlow 已上传并触发解析 {len(doc_ids)} 篇文档 (dataset={ds_id})")
        except Exception as e:
            print(f"  [B] RAGFlow 推送失败（已忽略，保留本地 store）: {e}")

    def _retrieve_ragflow(self, query, k):
        cfg = self._ragflow_cfg()
        if not cfg["base"] or not cfg["key"]:
            return None
        try:
            import requests
            base, headers = cfg["base"], self._ragflow_headers()
            ds_id = self._resolve_dataset(base, headers, cfg["dataset"])
            if not ds_id:
                return None
            r = requests.post(f"{base}/api/v1/retrieval", headers=headers,
                              json={"question": query, "dataset_ids": [ds_id],
                                    "page_size": k, "top_k": k}, timeout=30)
            if r.ok:
                chunks = (r.json().get("data") or {}).get("chunks") or []
                out = [c["content"] for c in chunks if c.get("content")]
                return out or None
        except Exception as e:
            print(f"  [B] RAGFlow 检索失败（降级本地）: {e}")
        return None
