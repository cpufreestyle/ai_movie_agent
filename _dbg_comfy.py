import requests
s = requests.Session(); s.trust_env = False
for p in (8188, 8200):
    try:
        r = s.get("http://127.0.0.1:%d/" % p, timeout=5)
        print(p, "STATUS", r.status_code)
    except Exception as e:
        print(p, "ERR", repr(e))
