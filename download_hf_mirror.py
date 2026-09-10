#!/usr/bin/env python
"""hf-mirror.com 预设入口（download_hf_mt.py 的薄封装）。

保留本文件名是为了兼容既有脚本的 `import download_hf_mirror as dl; dl.download(...)`，
实现只有一份，避免两个 130 行脚本各自漂移。

等价用法：
    HF_MIRROR=https://hf-mirror.com python download_hf_mt.py <repo> <file> <dest>
    python download_hf_mt.py --mirror https://hf-mirror.com <repo> <file> <dest>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import download_hf_mt as _impl  # noqa: E402

MIRROR = "https://hf-mirror.com"
_impl.MIRROR = MIRROR

# 重新导出，供 `import download_hf_mirror as dl` 的既有脚本继续使用
download = _impl.download
get_url_and_size = _impl.get_url_and_size
log = _impl.log
TASKS = _impl.TASKS


if __name__ == "__main__":
    _impl.MIRROR = MIRROR
    raise SystemExit(_impl.main(sys.argv[1:]))
