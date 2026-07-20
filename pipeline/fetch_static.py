"""拉取官方/社区静态数据:ddragon zh_CN、腾讯 hero_list.js、CDragon 强化表。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import fetch, fetch_json, save_json

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "data", "raw")


def main():
    version = fetch_json("https://ddragon.leagueoflegends.com/api/versions.json")[0]
    print("ddragon version:", version)

    dd = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN"
    for name in ["champion", "item", "runesReforged", "summoner"]:
        save_json(os.path.join(RAW, "ddragon", f"{name}.json"), fetch_json(f"{dd}/{name}.json", min_interval=0.3))
        print("ok ddragon", name)

    hero_list = json.loads(fetch("https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js"))
    save_json(os.path.join(RAW, "hero_list.json"), hero_list)
    print("ok hero_list:", len(hero_list["hero"]), "heroes, version", hero_list.get("version"))

    cherry = fetch_json("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/zh_cn/v1/cherry-augments.json")
    aram_augs = [a for a in cherry if str(a.get("augmentNameId", "")).startswith("ARAM_")]
    save_json(os.path.join(RAW, "cherry_augments_zh.json"), cherry)
    print("ok cherry-augments:", len(cherry), "total,", len(aram_augs), "ARAM_")

    save_json(os.path.join(RAW, "static_meta.json"), {"ddragonVersion": version, "heroListVersion": hero_list.get("version")})


if __name__ == "__main__":
    main()
