import sys, os, json
sys.path.insert(0, r'c:\Users\michael\CodeBuddy\ai_movie_agent')
from agent.publisher import Publisher

cfg = {"publish": {
    "binary": "tools/biliup/biliupR-v0.2.4-x86_64-windows/biliup.exe",
    "proxy": "http://127.0.0.1:7897",
}}
pub = Publisher(cfg, r'c:\Users\michael\CodeBuddy\ai_movie_agent\outputs')
res = pub.list_my_videos(page_size=8, max_pages=1)
if not res.get("ok"):
    print("ERR", res)
else:
    for v in res["videos"]:
        print(v["bvid"], "|", v.get("title"), "|", v.get("length"))
