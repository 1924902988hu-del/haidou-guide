"""按 build_site_data.py 生成的清单下载缺失图片到 site/(图片本地化,页面秒开)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import fetch, load_json

ROOT = os.path.join(os.path.dirname(__file__), "..")
SITE = os.path.join(ROOT, "site")
RATE = 0.12


def main():
    manifest = load_json(os.path.join(ROOT, "data", "raw", "image_manifest.json"))
    missing = {rel: url for rel, url in manifest.items()
               if not os.path.exists(os.path.join(SITE, rel))}
    print(f"清单 {len(manifest)} 张,缺失 {len(missing)} 张")
    fail = []
    for n, (rel, url) in enumerate(sorted(missing.items()), 1):
        path = os.path.join(SITE, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            data = fetch(url, min_interval=RATE, binary=True)
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:
            fail.append((rel, str(e)[:80]))
        if n % 100 == 0 or n == len(missing):
            print(f"{n}/{len(missing)}")
    if fail:
        print("失败:", fail[:20], f"共 {len(fail)}")


if __name__ == "__main__":
    main()
