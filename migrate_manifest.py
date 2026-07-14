# -*- coding: utf-8 -*-
"""一次性脚本: 把 worlds_test 的 10 个世界按 12fps->24fps 帧名映射迁入 worlds_24fps。"""
import json
import shutil
import sys
from pathlib import Path

for s in (sys.stdout, sys.stderr):
    if hasattr(s, "reconfigure"):
        s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
SRC = ROOT / "worlds_test"
DST = ROOT / "worlds_24fps"

MAPPING = {  # 旧帧名(12fps 网格) -> 新帧名(24fps 网格)
    "frame_0000": "frame_0000", "frame_0006": "frame_0012",
    "frame_0012": "frame_0024", "frame_0018": "frame_0036",
    "frame_0024": "frame_0048", "frame_0030": "frame_0060",
    "frame_0036": "frame_0072", "frame_0042": "frame_0084",
    "frame_0048": "frame_0096", "frame_0054": "frame_0108",
}

src_manifest = json.loads((SRC / "manifest.json").read_text(encoding="utf-8-sig"))
assert src_manifest["config"] == {"model": "marble-1.1", "seed": 12345}, "测试批参数与预期不符"

dst_manifest_path = DST / "manifest.json"
if dst_manifest_path.exists():
    dst_manifest = json.loads(dst_manifest_path.read_text(encoding="utf-8-sig"))
else:
    dst_manifest = {"config": dict(src_manifest["config"]), "frames": {}}

for old, new in sorted(MAPPING.items()):
    entry = src_manifest["frames"][old]
    assert entry["status"] == "downloaded", f"{old} 状态异常: {entry['status']}"
    if new in dst_manifest["frames"]:
        print(f"[{new}] 已在目标 manifest,跳过")
        continue
    dst_dir = DST / new
    dst_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("world.spz", "world_500k.spz", "metadata.json"):
        src_file = SRC / old / fname
        assert src_file.exists(), f"缺文件: {src_file}"
        shutil.copy2(src_file, dst_dir / fname)
    migrated = dict(entry)
    migrated["reused_from"] = f"worlds_test/{old}"
    dst_manifest["frames"][new] = migrated
    print(f"[{old}] -> [{new}] 迁移完成 (world_id={entry['world_id']})")

dst_manifest_path.write_text(json.dumps(dst_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nmanifest 写入 {dst_manifest_path},共登记 {len(dst_manifest['frames'])} 帧")
