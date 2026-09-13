"""B 站投稿脚本：扫码登录 + 上传视频（支持多集）。

用法：
  python upload_bili.py login           # 生成 outputs/bili_qr.png 并用 B 站 App 扫码，凭据存 outputs/bili_cred.json
  python upload_bili.py upload 2 3      # 上传第 2、3 集
  python upload_bili.py upload --all    # 上传 1、2、3 集

注意：凭据仅存于本地 outputs/bili_cred.json，不上传、不记忆。
"""
import sys
import os
import json
import time
import asyncio

CRED_PATH = "outputs/bili_cred.json"
QR_PATH = "outputs/bili_qr.png"
TAGS = ["科幻", "AI短片", "赛博朋克", "动画", "看见未来之前"]
TID = 174  # 影视-短片·短片（投稿后如需改分区告诉我）
BASE_DESC = (
    "AI 短片《看见未来之前》。\n"
    "（本片为 AI 生成画面 + 英文旁白 + 中英双语字幕）"
)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

EPISODES = {
    1: {"video": "outputs/ep1_vo_mmh3.mp4"},
    2: {"video": "outputs/ep2_vo_mmh3.mp4"},
    3: {"video": "outputs/ep3_vo_mmh3.mp4"},
}


def ep_title(ep: int) -> str:
    try:
        d = json.load(open("outputs/series_script.json", encoding="utf-8"))
        sub = d.get(f"ep{ep}", {})
        name = sub.get("title", "")
        if name:
            return f"看见未来之前 EP0{ep} · {name}"
    except Exception:
        pass
    return f"看见未来之前 EP0{ep}"


def gen_cover(ep: int, video: str):
    import imageio_ffmpeg
    import subprocess
    cover = f"outputs/bili_cover_ep{ep}.png"
    if not os.path.exists(cover):
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [ff, "-y", "-ss", "00:00:05", "-i", video,
             "-frames:v", "1", "-vf", "scale=1146:-1", cover],
            capture_output=True, text=True, timeout=60,
        )
    return cover


def do_login():
    import requests
    import qrcode
    sess = requests.Session()
    sess.proxies.clear()  # B 站为国内站点，强制直连，避免代理注入 404
    headers = {"User-Agent": UA, "Referer": "https://passport.bilibili.com/"}
    sess.get("https://www.bilibili.com/", headers=headers)  # 拿基础 cookie
    r = sess.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                 headers=headers, timeout=20)
    j = r.json()
    if j.get("code") != 0:
        print("GEN_FAIL", j); return
    data = j["data"]
    qrcode.make(data["url"]).save(QR_PATH)
    print("QR_SAVED", QR_PATH)
    key = data["qrcode_key"]
    dbg = 0
    for _ in range(90):  # 最多等 ~3 分钟
        try:
            r = sess.get("https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                         params={"qrcode_key": key}, headers=headers, timeout=20)
            if dbg < 3:
                print("POLL", r.status_code, r.text[:200]); dbg += 1
        except Exception as e:
            print("REQ_ERR", str(e)[:120]); time.sleep(2); continue
        # 登录成功后 B 站会在 Set-Cookie 下发 SESSDATA
        if sess.cookies.get("SESSDATA"):
            cookies = sess.cookies.get_dict()
            with open(CRED_PATH, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            print("CRED_SAVED", CRED_PATH)
            return
        time.sleep(2)
    print("QR_TIMEOUT")


def do_upload(ep: int):
    from bilibili_api import Credential
    from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage
    cfg = EPISODES[ep]
    video = cfg["video"]
    if not os.path.exists(video):
        print("NO_VIDEO", video); return
    with open(CRED_PATH, encoding="utf-8") as f:
        cookies = json.load(f)
    cred = Credential(cookies=cookies)
    title = ep_title(ep)
    cover = gen_cover(ep, video)
    page = VideoUploaderPage(path=video, title=title)
    meta = {
        "title": title,
        "desc": BASE_DESC,
        "tag": TAGS,
        "tid": TID,
        "source": "自己创作的小视频",
        "copyright": 1,
        "dynamic": "",
        "interactive": 0,
        "no_reprint": 0,
        "subtitle": {"open": 0, "lan": ""},
    }
    up = VideoUploader(pages=[page], meta=meta, credential=cred, cover_path=cover)
    result = asyncio.run(up.start())
    bvid = result.get("bvid") if isinstance(result, dict) else result
    print("UPLOAD_DONE", ep, bvid)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python upload_bili.py [login|upload [--all|ep...]]")
    elif sys.argv[1] == "login":
        do_login()
    elif sys.argv[1] == "upload":
        rest = sys.argv[2:]
        if rest and rest[0] == "--all":
            eps = list(EPISODES.keys())
        else:
            eps = [int(x) for x in rest if x.isdigit()]
        for ep in eps:
            do_upload(ep)
    else:
        print("usage: python upload_bili.py [login|upload [--all|ep...]]")
