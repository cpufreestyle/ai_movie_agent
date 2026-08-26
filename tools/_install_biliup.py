import io
import os
import urllib.request
import zipfile

URL = "https://github.com/biliup/biliup-rs/releases/download/v0.2.4/biliupR-v0.2.4-x86_64-windows.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
dest = os.path.join(HERE, "biliup")
os.makedirs(dest, exist_ok=True)

print("downloading biliup-rs windows zip ...")
req = urllib.request.Request(URL, headers={"User-Agent": "curl"})
data = urllib.request.urlopen(req, timeout=180).read()
print("downloaded bytes:", len(data))

with zipfile.ZipFile(io.BytesIO(data)) as z:
    z.extractall(dest)

exes = []
for root, _, files in os.walk(dest):
    for f in files:
        if f.lower().endswith(".exe"):
            exes.append(os.path.join(root, f))
print("extracted exes:", exes)
if exes:
    print("BILIUP_EXE=" + exes[0])
