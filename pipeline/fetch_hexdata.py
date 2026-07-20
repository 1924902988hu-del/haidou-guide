"""拉取 hexdata.com.cn 的静态 JSON(中文英雄/强化统计、出装、三强化组合)。

按 buildId 缓存:构建号没变就跳过全量下载。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import fetch_json, save_json, load_json

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "raw", "hexdata")
BASE = "https://hexdata.com.cn/data"
RATE = 0.35  # 秒/请求


def main():
    archive = fetch_json(f"{BASE}/archive.json")
    latest = archive["entries"][0]
    print("hexdata build:", latest["buildId"], "patch", latest["reportPatch"])

    stamp_path = os.path.join(OUT, "_buildId.json")
    if os.path.exists(stamp_path) and load_json(stamp_path).get("buildId") == latest["buildId"]:
        print("build 未变化,跳过")
        return

    for name in ["meta", "heroes", "augments", "hero_formula_items"]:
        save_json(os.path.join(OUT, f"{name}.json"), fetch_json(f"{BASE}/{name}.json", min_interval=RATE))
        print("ok", name)

    heroes = load_json(os.path.join(OUT, "heroes.json"))
    ids = [h["id"] for h in heroes]
    for n, hid in enumerate(ids, 1):
        save_json(os.path.join(OUT, "heroes", f"{hid}.json"), fetch_json(f"{BASE}/heroes/{hid}.json", min_interval=RATE))
        if n % 30 == 0 or n == len(ids):
            print(f"heroes {n}/{len(ids)}")

    save_json(stamp_path, {"buildId": latest["buildId"], "reportPatch": latest["reportPatch"],
                           "reportDate": latest["reportDate"]})
    print("done:", len(ids), "hero files")


if __name__ == "__main__":
    main()
