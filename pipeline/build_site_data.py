"""合成站点数据:把 ddragon/hero_list/cherry/hexdata/opgg 按 heroId join。

输出(自包含,前端每页只需一个请求):
- site/data/index.json                首页索引(搜索昵称、定位、梯度)
- site/data/heroes/{alias}.json       英雄详情(天赋/加点/召唤师技能/海克斯/出装)

合规:海克斯强化一律不输出胜率数字(只有 hexScore 推荐度与档位);英雄胜率保留。
"""
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from common import save_json, load_json

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "data", "raw")
SITE_DATA = os.path.join(ROOT, "site", "data")
VIDEO_CATALOG = os.path.join(ROOT, "data", "videos", "catalog.json")

DD_IMG = "https://ddragon.leagueoflegends.com/cdn"
CD_BASE = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default"

# 图片本地化:站点只引用本地路径,fetch_images.py 按清单补齐缺失文件
IMG_MANIFEST = {}


def img(rel, url):
    if url:
        IMG_MANIFEST[rel] = url
    return rel

STAT_MODS = {
    5001: "成长生命值", 5005: "攻击速度", 5007: "技能急速", 5008: "自适应之力",
    5010: "移动速度", 5011: "生命值", 5013: "韧性和减速抵抗",
}
RARITY_NORM = {"白银": "白银", "银": "白银", "黄金": "黄金", "金": "黄金", "棱彩": "棱彩"}
ROLE_ZH = {"fighter": "战士", "mage": "法师", "assassin": "刺客",
           "marksman": "射手", "tank": "坦克", "support": "辅助"}


def cd_icon(path):
    if not path:
        return None
    p = path.lower()
    p = p[len("/lol-game-data/assets/"):] if p.startswith("/lol-game-data/assets/") else p.lstrip("/")
    return f"{CD_BASE}/{p}"


def clean_augment_desc(value):
    """上游有意隐藏动态数值时保留语义,但不猜测或伪造具体数字。"""
    desc = re.sub(r"%i:[A-Za-z0-9_]+%\s*", "", value or "")
    needs_game_check = "?" in desc
    replacements = [
        (r"【\?】技能", "指定技能"),
        (r"获得\?", "获得额外"),
        (r"每\?秒", "周期性"),
        (r"\?%", "一定比例的"),
        (r"\?个", "一定数量的"),
        (r"\?次", "若干次"),
        (r"至\?", "至一定数值"),
        (r"\?", "一定数值的"),
    ]
    for pattern, replacement in replacements:
        desc = re.sub(pattern, replacement, desc)
    return desc, needs_game_check


def main():
    version = load_json(os.path.join(RAW, "static_meta.json"))["ddragonVersion"]
    game_patch = "26." + version.split(".")[1]  # 16.14.1 -> 26.14(对外补丁号)

    champs = load_json(os.path.join(RAW, "ddragon", "champion.json"))["data"]
    items_dd = load_json(os.path.join(RAW, "ddragon", "item.json"))["data"]
    runes_dd = load_json(os.path.join(RAW, "ddragon", "runesReforged.json"))
    spells_dd = load_json(os.path.join(RAW, "ddragon", "summoner.json"))["data"]
    hero_list = {h["heroId"]: h for h in load_json(os.path.join(RAW, "hero_list.json"))["hero"]}
    cherry = {str(a["id"]): a for a in load_json(os.path.join(RAW, "cherry_augments_zh.json"))}
    hx_heroes = {h["id"]: h for h in load_json(os.path.join(RAW, "hexdata", "heroes.json"))}
    hx_augs = {str(a["id"]): a for a in load_json(os.path.join(RAW, "hexdata", "augments.json"))}
    hx_formula = load_json(os.path.join(RAW, "hexdata", "hero_formula_items.json"))["byHeroId"]
    hx_build = load_json(os.path.join(RAW, "hexdata", "_buildId.json"))
    stats_patch_raw = str(hx_build.get("reportPatch") or "")
    stats_patch = ("26." + stats_patch_raw.split(".", 1)[1]
                   if stats_patch_raw.startswith("16.") else stats_patch_raw)
    valid_ids = set(hero_list) & set(hx_heroes)
    video_rows = load_json(VIDEO_CATALOG).get("videos", []) if os.path.exists(VIDEO_CATALOG) else []
    videos_by_hero = {}
    for video in video_rows:
        for video_alias in video.get("heroes", []):
            videos_by_hero.setdefault(video_alias, []).append(video)

    def rune_icon(icon_path):
        rel = "assets/img/rune/" + icon_path.replace("perk-images/", "").replace("/", "_")
        return img(rel, f"{DD_IMG}/img/{icon_path}")

    rune_map, style_map = {}, {}
    for style in runes_dd:
        style_map[style["id"]] = {"name": style["name"], "icon": rune_icon(style["icon"])}
        for slot in style["slots"]:
            for r in slot["runes"]:
                rune_map[r["id"]] = {"name": r["name"], "icon": rune_icon(r["icon"])}
    spell_map = {}
    for s in spells_dd.values():
        f = s["image"]["full"]
        if "_Jade" in f:
            continue
        spell_map[int(s["key"])] = {"name": s["name"],
                                    "icon": img(f"assets/img/spell/{f}", f"{DD_IMG}/{version}/img/spell/{f}")}

    def item_ref(iid, name_hint=None):
        iid = str(iid)
        d = items_dd.get(iid)
        return {"id": int(iid), "name": (d["name"] if d else name_hint) or f"#{iid}",
                "icon": img(f"assets/img/item/{iid}.png", f"{DD_IMG}/{version}/img/item/{iid}.png")}

    def aug_ref(aid, extra=None):
        aid = str(aid)
        hx, ch = hx_augs.get(aid), cherry.get(aid)
        if not hx and not ch:
            return None
        desc, needs_game_check = clean_augment_desc((hx or {}).get("description", ""))
        out = {
            "id": int(aid),
            "name": (hx and hx.get("name")) or (ch and ch.get("nameTRA")) or f"#{aid}",
            "rarity": RARITY_NORM.get((hx or {}).get("rarity") or "", "") or {"kSilver": "白银", "kGold": "黄金", "kPrismatic": "棱彩"}.get((ch or {}).get("rarity"), ""),
            "desc": desc,
            "needsGameCheck": needs_game_check,
        }
        remote = cd_icon((ch or {}).get("augmentSmallIconPath")) or \
            ("https://hexdata.com.cn" + hx["iconUrl"] if hx and hx.get("iconUrl") else None)
        out["icon"] = img(f"assets/img/augment/{aid}.png", remote) if remote else None
        if extra:
            out.update(extra)
        return out

    index_rows = []
    written = 0
    for alias, c in sorted(champs.items()):
        key = str(c["key"])
        if alias.startswith("Jade_") or key not in valid_ids:
            continue
        hl = hero_list.get(key)
        hx = hx_heroes.get(key)
        opgg_path = os.path.join(RAW, "opgg", f"{key}.json")
        opgg = load_json(opgg_path) if os.path.exists(opgg_path) else None

        # zh_CN 语义:ddragon name=称号,title=音译名;hero_list 同向
        display = hl["title"]      # 安妮
        epithet = hl["name"]       # 黑暗之女
        roles = [r for r in hl.get("roles", []) if r in ROLE_ZH]
        search_terms = set()
        for kw in (hl.get("keywords") or "").split(","):
            if kw.strip():
                search_terms.add(kw.strip().lower())
        for t in hx.get("searchTerms") or []:
            search_terms.add(t.strip().lower())
        search_terms.update({display.lower(), epithet.lower(), alias.lower()})

        icon = img(f"assets/img/champion/{alias}.png", f"{DD_IMG}/{version}/img/champion/{alias}.png")
        stats = {"tier": hx.get("tier"), "winRate": hx.get("winRate"), "pickRate": hx.get("pickRate"),
                 "games": hx.get("games"), "kda": hx.get("kda")}
        index_rows.append({
            "id": int(key), "alias": alias, "name": display, "epithet": epithet,
            "roles": roles, "icon": icon, **stats,
            "search": ",".join(sorted(search_terms)),
        })

        # ---- 详情 ----
        detail_path = os.path.join(RAW, "hexdata", "heroes", f"{key}.json")
        hx_detail = load_json(detail_path) if os.path.exists(detail_path) else {}

        rune_pages = []
        for p in (opgg or {}).get("runePages", [])[:2]:
            rune_pages.append({
                "primaryStyle": style_map.get(p["primaryStyle"]),
                "subStyle": style_map.get(p["subStyle"]),
                "primary": [rune_map.get(r) for r in p["primaryRunes"] if rune_map.get(r)],
                "sub": [rune_map.get(r) for r in p["subRunes"] if rune_map.get(r)],
                "shards": [STAT_MODS.get(s, str(s)) for s in p["statMods"]],
                "pickRate": p.get("pickRate"),
            })

        m = (opgg or {}).get("mayhem", {})
        opgg_aug_ids = set(m.get("augments", []))
        hx_aug_rows = sorted(hx_detail.get("augments", []), key=lambda a: -(a.get("hexScore") or 0))
        augments = []
        for row in hx_aug_rows[:12]:
            ref = aug_ref(row["augmentId"], {
                "hexScore": row.get("hexScore"), "hexLabel": row.get("hexLabel"),
                "opgg": int(row["augmentId"]) in opgg_aug_ids,
            })
            if ref:
                augments.append(ref)
        # op.gg 推荐但 hexdata 榜单没排进前列的,补在后面
        listed = {a["id"] for a in augments}
        for aid in m.get("augments", []):
            if aid not in listed:
                ref = aug_ref(aid, {"opgg": True})
                if ref:
                    augments.append(ref)

        trios = sorted(hx_detail.get("trios", []),
                       key=lambda t: (t.get("winRateTier") or 9, -(t.get("games") or 0)))[:6]
        combos = [{
            "augments": [aug_ref(a) for a in t.get("augmentIds", []) if aug_ref(a)],
            "tier": t.get("winRateTier"), "games": t.get("games"),
        } for t in trios]

        formula = hx_formula.get(key) or {}
        hx_items = sorted(hx_detail.get("items", []), key=lambda i: -(i.get("hexScore") or 0))

        def uniq(seq):
            seen = set()
            return [x for x in seq if not (x in seen or seen.add(x))]

        detail = {
            "id": int(key), "alias": alias, "name": display, "epithet": epithet,
            "roles": roles, "icon": icon,
            "splash": f"{DD_IMG.replace('/cdn', '')}/img/champion/splash/{alias}_0.jpg",
            "patch": {"game": game_patch, "statsGame": stats_patch,
                      "ddragon": version, "hexdataDate": hx_build.get("reportDate")},
            "stats": stats,
            "runes": {"pages": rune_pages, "source": "op.gg 极地大乱斗(海克斯模式无独立天赋统计,思路通用)"},
            "skills": {"priority": m.get("skills", {}).get("priority", []),
                       "sequence": m.get("skills", {}).get("sequence", [])},
            "spells": [[spell_map.get(s) for s in pair if spell_map.get(s)] for pair in m.get("spells", [])[:2]],
            "augments": augments,
            "combos": combos,
            "items": {
                "starter": [item_ref(i["id"], i.get("name")) for i in formula.get("starterItems", [])] or
                           [item_ref(i) for i in uniq(m.get("items", {}).get("starter", []))[:4]],
                "core": [item_ref(i["id"], i.get("name")) for i in formula.get("coreItems", [])],
                "boots": [item_ref(i) for i in uniq(m.get("items", {}).get("boots", []))[:3]],
                "opggCores": [[item_ref(i) for i in row] for row in m.get("items", {}).get("cores", [])[:3]],
                "hexTop": [{"item": item_ref(r["itemId"], r.get("itemName")), "hexLabel": r.get("hexLabel"),
                            "hexScore": r.get("hexScore")} for r in hx_items[:8]],
            },
            "douyinUrl": "https://www.douyin.com/search/" + urllib.parse.quote(f"{display} 海克斯大乱斗"),
            "videos": videos_by_hero.get(alias, []),
            "sources": {"opgg": bool(opgg), "hexdata": True},
        }
        save_json(os.path.join(SITE_DATA, "heroes", f"{alias}.json"), detail)
        written += 1

    expected_files = {f"{r['alias']}.json" for r in index_rows}
    hero_dir = os.path.join(SITE_DATA, "heroes")
    if os.path.isdir(hero_dir):
        for filename in os.listdir(hero_dir):
            if filename.endswith(".json") and filename not in expected_files:
                os.remove(os.path.join(hero_dir, filename))

    index_rows.sort(key=lambda r: ((r["tier"] or 9), -(r["winRate"] or 0)))
    save_json(os.path.join(SITE_DATA, "index.json"), {
        "patch": {"game": game_patch, "statsGame": stats_patch, "ddragon": version,
                  "hexdataDate": hx_build.get("reportDate")},
        "heroes": index_rows,
    })
    save_json(os.path.join(RAW, "image_manifest.json"), IMG_MANIFEST)
    print(f"index: {len(index_rows)} heroes, details written: {written}, images in manifest: {len(IMG_MANIFEST)}")

    # 自检:昵称索引必须包含"女枪"
    blob = ",".join(r["search"] for r in index_rows)
    assert "女枪" in blob, "昵称索引缺失『女枪』"
    print("昵称自检通过(女枪→", [r["name"] for r in index_rows if "女枪" in r["search"]], ")")


if __name__ == "__main__":
    main()
