# -*- coding: utf-8 -*-
"""
Marble API 批量生成脚本:全景帧 -> 3DGS 世界 (spz)

流程(每帧):
  1. POST /media-assets:prepare_upload  -> media_asset_id + 预签名 URL
  2. PUT 图片到预签名 URL
  3. POST /worlds:generate (marble-1.1, 固定 seed, is_pano=true) -> operation_id
  4. GET  /operations/{id} 轮询直到 done
  5. 下载 assets.splats 中的 spz + 保存 metadata.json

状态由 manifest.json 记录,可中断重跑:已提交的帧不会重复提交(不会重复扣费),
只补轮询和下载。

用法:
  python marble_generate.py --frames frame_0000            # 只跑 1 帧(先验证全链路)
  python marble_generate.py                                # 跑 frames_test10 下全部帧
  python marble_generate.py --input-dir frames_12fps      # 之后 120 帧正式批量复用
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# Windows 控制台默认 cp1252/gbk,强制 UTF-8 输出避免中文打印报错
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://api.worldlabs.ai/marble/v1"
MODEL = "marble-1.1"          # 固定价 1500 credits/次,不用 1.1-plus
SEED = 12345                  # 全部帧统一
EXPECTED_COST = 1500
POLL_INTERVAL = 15            # 秒
HTTP_TIMEOUT = 60

SCRIPT_DIR = Path(__file__).parent


def load_api_key() -> str:
    key = os.environ.get("WLT_API_KEY")
    if not key:
        env_file = SCRIPT_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("WLT_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        sys.exit("错误: 未找到 API key。请设置环境变量 WLT_API_KEY 或在脚本目录创建 .env 文件。")
    return key


class MarbleClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers["WLT-Api-Key"] = api_key

    def _request(self, method: str, url: str, **kwargs):
        # 429(限流)/5xx 指数退避重试;批量提交时限流是常态,不重试会白丢任务
        delay = 10
        for attempt in range(6):
            resp = self.session.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
            if resp.status_code == 402:
                sys.exit("错误 402: API credits 不足,请到 platform.worldlabs.ai 充值。")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == 5:
                    break
                print(f"  HTTP {resp.status_code},{delay}s 后重试 ({attempt + 1}/5) ...")
                time.sleep(delay)
                delay *= 2
                continue
            if not resp.ok:
                raise RuntimeError(f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise RuntimeError(f"{method} {url} -> HTTP {resp.status_code} 重试 5 次后仍失败: {resp.text[:300]}")

    def prepare_upload(self, file_path: Path) -> dict:
        return self._request("POST", f"{BASE_URL}/media-assets:prepare_upload", json={
            "file_name": file_path.name,
            "extension": file_path.suffix.lstrip("."),
            "kind": "image",
        })

    def upload_file(self, upload_info: dict, file_path: Path):
        headers = upload_info.get("required_headers") or {}
        method = upload_info.get("upload_method", "PUT")
        with open(file_path, "rb") as f:
            resp = requests.request(method, upload_info["upload_url"],
                                    data=f, headers=headers, timeout=600)
        resp.raise_for_status()

    def generate_world(self, media_asset_id: str, display_name: str) -> dict:
        return self._request("POST", f"{BASE_URL}/worlds:generate", json={
            "display_name": display_name,
            "model": MODEL,
            "seed": SEED,
            "world_prompt": {
                "type": "image",
                "image_prompt": {"source": "media_asset", "media_asset_id": media_asset_id},
                "is_pano": True,  # JSON 布尔;API 只接受 'auto'/true/false,字符串 "true" 会 422
            },
        })

    def get_operation(self, operation_id: str) -> dict:
        return self._request("GET", f"{BASE_URL}/operations/{operation_id}")

    def get_world(self, world_id: str) -> dict:
        return self._request("GET", f"{BASE_URL}/worlds/{world_id}")

    def download(self, url: str, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(url, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            tmp.replace(dest)


class Manifest:
    """manifest.json 状态机: pending -> uploaded -> submitted -> done -> downloaded / failed"""

    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8-sig"))
        else:
            self.data = {"config": {"model": MODEL, "seed": SEED}, "frames": {}}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def frame(self, name: str) -> dict:
        return self.data["frames"].setdefault(name, {"status": "pending"})


def pick_best_spz(spz_urls: dict) -> tuple[str, str]:
    """从多分辨率 spz_urls 里挑最高分辨率。实测 key 形如 ['100k','150k','500k','full_res'],
    full_res 最高;其余按数字比大小。"""
    if "full_res" in spz_urls:
        return "full_res", spz_urls["full_res"]
    def key_num(k):
        nums = re.findall(r"\d+", k)
        return int(nums[0]) if nums else -1
    best = max(spz_urls, key=key_num)
    return best, spz_urls[best]


def extract_world(op: dict) -> dict:
    resp = op.get("response") or {}
    return resp.get("world") or resp


def process_downloads(client: MarbleClient, name: str, entry: dict, world: dict, out_dir: Path):
    frame_dir = out_dir / name
    frame_dir.mkdir(parents=True, exist_ok=True)
    assets = world.get("assets") or {}
    splats = assets.get("splats") or {}
    spz_urls = splats.get("spz_urls") or {}

    if not spz_urls:
        raise RuntimeError(f"{name}: 完成的世界里没有 spz_urls, assets keys = {list(assets)}")

    res_key, url = pick_best_spz(spz_urls)
    print(f"  [{name}] 可用分辨率 {list(spz_urls)},下载 {res_key} ...")
    client.download(url, frame_dir / "world.spz")

    meta = {
        "frame": name,
        "world_id": world.get("world_id"),
        "operation_id": entry.get("operation_id"),
        "cost_credits": entry.get("cost"),
        "model": MODEL,
        "seed": SEED,
        "downloaded_spz_resolution": res_key,
        "spz_urls": spz_urls,
        "semantics_metadata": splats.get("semantics_metadata"),
        "world_marble_url": world.get("world_marble_url"),
        "assets": assets,
    }
    (frame_dir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    # semantics_metadata 若是 URL 字符串,连内容一并存下(配准阶段要用)
    sem = splats.get("semantics_metadata")
    if isinstance(sem, str) and sem.startswith("http"):
        try:
            client.download(sem, frame_dir / "semantics_metadata.json")
        except Exception as e:
            print(f"  [{name}] 警告: semantics_metadata 下载失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="Marble API 批量生成 3DGS")
    parser.add_argument("--input-dir", default="frames_test10", help="输入帧目录")
    parser.add_argument("--output-dir", default="worlds_test", help="输出目录")
    parser.add_argument("--frames", nargs="*", default=None,
                        help="只处理指定帧(如 frame_0000),缺省处理目录下全部 png")
    args = parser.parse_args()

    input_dir = SCRIPT_DIR / args.input_dir
    out_dir = SCRIPT_DIR / args.output_dir
    client = MarbleClient(load_api_key())
    manifest = Manifest(out_dir / "manifest.json")

    if manifest.data["config"].get("seed") != SEED or manifest.data["config"].get("model") != MODEL:
        sys.exit("错误: manifest 中记录的 seed/model 与脚本不一致,跨帧参数必须统一。"
                 "如确要更换参数,请换一个 output-dir。")

    all_frames = sorted(p.stem for p in input_dir.glob("*.png"))
    targets = args.frames if args.frames else all_frames
    missing = [f for f in targets if f not in all_frames]
    if missing:
        sys.exit(f"错误: 输入目录中找不到帧: {missing}")

    print(f"处理 {len(targets)} 帧: {targets[0]} ... {targets[-1]}")
    print(f"model={MODEL} seed={SEED} 预计费用 {len(targets) * EXPECTED_COST} credits\n")

    # ---- 阶段 1: 上传 + 提交(跳过已提交的,避免重复扣费) ----
    for name in targets:
        entry = manifest.frame(name)
        if entry["status"] in ("submitted", "done", "downloaded"):
            print(f"[{name}] 已提交过 (status={entry['status']}),跳过提交")
            continue
        try:
            if not entry.get("media_asset_id"):
                print(f"[{name}] 上传图片 ...")
                prep = client.prepare_upload(input_dir / f"{name}.png")
                client.upload_file(prep["upload_info"], input_dir / f"{name}.png")
                entry["media_asset_id"] = prep["media_asset"]["media_asset_id"]
                entry["status"] = "uploaded"
                manifest.save()

            print(f"[{name}] 提交生成任务 ...")
            op = client.generate_world(entry["media_asset_id"], name)
            entry["operation_id"] = op["operation_id"]
            entry["status"] = "submitted"
            entry.pop("error", None)
            manifest.save()
            time.sleep(3)  # 提交间隔,降低触发限流的概率
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            manifest.save()
            print(f"[{name}] 失败: {e}")

    # ---- 阶段 2: 统一轮询 ----
    pending = [n for n in targets if manifest.frame(n)["status"] == "submitted"]
    while pending:
        for name in list(pending):
            entry = manifest.frame(name)
            try:
                op = client.get_operation(entry["operation_id"])
            except Exception as e:
                print(f"[{name}] 轮询出错(下轮重试): {e}")
                continue
            if op.get("done"):
                pending.remove(name)
                if op.get("error"):
                    entry["status"] = "failed"
                    entry["error"] = json.dumps(op["error"], ensure_ascii=False)
                    print(f"[{name}] 生成失败: {entry['error']}")
                else:
                    cost = (op.get("cost") or {}).get("total_credits")
                    entry["cost"] = cost
                    entry["world"] = extract_world(op)
                    entry["world_id"] = entry["world"].get("world_id")
                    entry["status"] = "done"
                    flag = "" if cost == EXPECTED_COST else f"  ⚠ 预期 {EXPECTED_COST}!"
                    print(f"[{name}] 完成, 扣费 {cost} credits{flag}")
                manifest.save()
            else:
                pct = (op.get("metadata") or {}).get("progress_percentage")
                print(f"[{name}] 进行中 {pct if pct is not None else '?'}%")
        if pending:
            time.sleep(POLL_INTERVAL)

    # ---- 阶段 3: 下载 ----
    for name in targets:
        entry = manifest.frame(name)
        if entry["status"] != "done":
            continue
        try:
            world = entry.get("world") or {}
            if not (world.get("assets") or {}).get("splats"):
                world = client.get_world(entry["world_id"])  # 资产 URL 有时效,过期就重取
            print(f"[{name}] 下载资产 ...")
            process_downloads(client, name, entry, world, out_dir)
            entry["status"] = "downloaded"
            entry.pop("world", None)  # world 详情已落盘 metadata.json,manifest 里不留大对象
            manifest.save()
        except Exception as e:
            print(f"[{name}] 下载失败(重跑脚本可重试): {e}")

    # ---- 汇总 ----
    print("\n===== 汇总 =====")
    total_cost = 0
    for name in targets:
        entry = manifest.frame(name)
        cost = entry.get("cost") or 0
        total_cost += cost
        print(f"  {name}: {entry['status']}"
              + (f" ({cost} credits)" if cost else "")
              + (f"  error: {entry.get('error')}" if entry.get("error") else ""))
    print(f"本批次总扣费: {total_cost} credits")


if __name__ == "__main__":
    main()
