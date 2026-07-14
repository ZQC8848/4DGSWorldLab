# -*- coding: utf-8 -*-
"""为输出目录里每帧补下 500k 档 spz (播放档位)。URL 过期则经 world_id 刷新。
用法: python download_500k.py [worlds_dir]  (缺省 worlds_test)"""
import json
import sys
from pathlib import Path

from marble_generate import MarbleClient, load_api_key, SCRIPT_DIR

out_dir = SCRIPT_DIR / (sys.argv[1] if len(sys.argv) > 1 else "worlds_test")
client = MarbleClient(load_api_key())
manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8-sig"))

for name, entry in sorted(manifest["frames"].items()):
    dest = out_dir / name / "world_500k.spz"
    if dest.exists():
        print(f"[{name}] 已存在,跳过")
        continue
    world = client.get_world(entry["world_id"])
    url = world["assets"]["splats"]["spz_urls"]["500k"]
    print(f"[{name}] 下载 500k ...")
    client.download(url, dest)
    print(f"[{name}] 完成 ({dest.stat().st_size / 1e6:.1f} MB)")
print("全部完成")
