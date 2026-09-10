import json
import time
import yaml
import requests

from agent.ltx_engine import LTXEngine

# 等 ComfyUI 就绪（重启后清空了节点缓存）
for _ in range(45):
    try:
        if requests.get("http://127.0.0.1:8188/", timeout=5).status_code == 200:
            break
    except Exception:
        pass
    time.sleep(2)

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
eng = LTXEngine(cfg, agent_root=".")
# 改分辨率/种子以改变图哈希，破除 ComfyUI 节点缓存，逼出真实执行错误
eng.resolution = "960x480"
eng.seed = 777
wf = eng._build_workflow("neon cyberpunk street, slow dolly", 777, None)

r = requests.post(eng.api + "/prompt", json={"prompt": wf}, timeout=60)
print("SUBMIT HTTP", r.status_code)
if r.status_code != 200:
    print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:1500])
    raise SystemExit
pid = r.json()["prompt_id"]
print("PROMPT_ID:", pid, "（改分辨率破缓存）等待真实执行...")
last = None
for _ in range(60):
    h = requests.get(eng.api + "/history/" + pid, timeout=20).json().get(pid)
    if h:
        st = h.get("status", {})
        if st.get("status_str") in ("error", "success"):
            last = st
            break
    time.sleep(2)
if last is None:
    print("（60s 内未结束，可能卡在模型加载）")
else:
    print("FINAL status_str:", last.get("status_str"))
    for m in last.get("messages", [])[:25]:
        print("  ", json.dumps(m, ensure_ascii=False)[:260])
