"""抓取并解析 op.gg 每英雄两页:
- /lol/modes/aram-mayhem/{slug}/build → 强化推荐、召唤师技能、技能加点、出装(RSC 渲染树)
- /lol/modes/aram/{slug}/build      → 天赋页(flight 内嵌原始 JSON;海克斯页无天赋区块)

低频限速(0.8s/请求)、可断点续跑(已存在的输出默认跳过)。
用法:python3 fetch_opgg.py [--force] [--only lux,missfortune]
"""
import os
import re
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
from common import fetch, decode_flight, extract_json_object, save_json, load_json

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(RAW, "opgg")
RATE = 0.8

# ddragon id 小写与 op.gg slug 不一致的已知例外(404 时也会自动试变体)
SLUG_FIX = {
    "monkeyking": "wukong",
}


def meta_ids(text, start=0, end=None):
    """按文档序返回 (pos, id, type)。"""
    end = end if end is not None else len(text)
    seg = text[start:end]
    out = []
    for m in re.finditer(r'"metaId":(\d+),"metaType":"([a-z-]+)"', seg):
        out.append((start + m.start(), int(m.group(1)), m.group(2)))
    for m in re.finditer(r'"metaType":"([a-z-]+)","metaId":(\d+)', seg):
        out.append((start + m.start(), int(m.group(2)), m.group(1)))
    return sorted(out)


def section_pos(text, title):
    m = re.search(r'"children":"' + re.escape(title) + '"', text)
    return m.start() if m else None


def parse_mayhem(text):
    p_start = section_pos(text, "Starter items")
    p_boots = None
    if p_start is not None:
        m = re.search(r'"children":"Boots"', text[p_start:])
        p_boots = p_start + m.start() if m else None
    p_core = section_pos(text, "Core builds")
    p_spells = section_pos(text, "Summoner spells")
    p_skills = section_pos(text, "Skill order")

    all_meta = meta_ids(text)
    items = [(pos, i) for pos, i, t in all_meta if t == "item"]
    augments = [i for _, i, t in all_meta if t == "aram-augment"]
    spells_flat = [i for _, i, t in all_meta if t == "spell"]

    def items_between(a, b):
        if a is None:
            return []
        return [i for pos, i in items if pos > a and (b is None or pos < b)]

    starter = items_between(p_start, p_boots)
    boots = items_between(p_boots, p_core)
    core_flat = items_between(p_core, None)
    cores = [core_flat[i:i + 3] for i in range(0, len(core_flat) - len(core_flat) % 3, 3)]

    letters = re.findall(r'"children":"([QWER])"', text)
    priority, sequence = [], []
    if len(letters) >= 18:
        priority, sequence = letters[:3], letters[3:18]
    elif letters:
        priority = letters[:3]

    # 去重保序
    seen = set()
    aug_order = [a for a in augments if not (a in seen or seen.add(a))]

    return {
        "augments": aug_order,
        "spells": [spells_flat[i:i + 2] for i in range(0, len(spells_flat) - len(spells_flat) % 2, 2)],
        "skills": {"priority": priority, "sequence": sequence},
        "items": {"starter": starter, "boots": boots, "cores": cores},
        "_anchors_ok": all(p is not None for p in [p_start, p_core, p_spells, p_skills]),
    }


def parse_aram_runes(text):
    pages = []
    for m in re.finditer(r'"stat_mod_ids"', text):
        obj = extract_json_object(text, m.start())
        if not obj or "primary_page_id" not in obj:
            continue
        pages.append(obj)
    # 按锚点提取可能重复(嵌套对象命中同一 blob),按 (primary, ids) 去重
    uniq = {}
    for p in pages:
        key = (p.get("primary_page_id"), tuple(p.get("primary_rune_ids") or []), tuple(p.get("secondary_rune_ids") or []))
        if key not in uniq or (p.get("play") or 0) > (uniq[key].get("play") or 0):
            uniq[key] = p
    pages = sorted(uniq.values(), key=lambda p: -(p.get("pick_rate") or 0))
    keep = []
    for p in pages[:3]:
        keep.append({
            "primaryStyle": p.get("primary_page_id"),
            "subStyle": p.get("secondary_page_id"),
            "primaryRunes": p.get("primary_rune_ids") or [],
            "subRunes": p.get("secondary_rune_ids") or [],
            "statMods": p.get("stat_mod_ids") or [],
            "play": p.get("play"),
            "win": p.get("win"),
            "pickRate": p.get("pick_rate"),
        })
    return keep


def fetch_champion(slug):
    html_m = fetch(f"https://op.gg/lol/modes/aram-mayhem/{slug}/build", min_interval=RATE)
    mayhem = parse_mayhem(decode_flight(html_m))
    html_a = fetch(f"https://op.gg/lol/modes/aram/{slug}/build", min_interval=RATE)
    runes = parse_aram_runes(decode_flight(html_a))
    return {"slug": slug, "mayhem": mayhem, "runePages": runes}


def candidates(alias, name_en=None):
    base = alias.lower()
    cands = [SLUG_FIX.get(base, base), base]
    if name_en:
        cands.append(re.sub(r"[^a-z]", "", name_en.lower()))
    seen = set()
    return [c for c in cands if not (c in seen or seen.add(c))]


def main():
    force = "--force" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))

    champs = load_json(os.path.join(RAW, "ddragon", "champion.json"))["data"]
    todo = []  # (key, alias, name_en)
    for cid, c in sorted(champs.items()):
        todo.append((c["key"], cid, c["title"]))  # zh_CN: title=音译名对搜索无用,只作日志

    failed = []
    for n, (key, alias, _) in enumerate(todo, 1):
        slugs = candidates(alias)
        if only and not (set(slugs) & only):
            continue
        out_path = os.path.join(OUT, f"{key}.json")
        if not force and os.path.exists(out_path) and not only:
            continue
        data = None
        for slug in slugs:
            try:
                data = fetch_champion(slug)
                break
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
        if data is None:
            failed.append(alias)
            print(f"FAIL {alias}: 所有 slug 404: {slugs}")
            continue
        data["heroKey"] = key
        data["alias"] = alias
        save_json(out_path, data)
        ok = "ok " if data["mayhem"]["_anchors_ok"] else "WARN(锚点缺失) "
        print(f"{ok}{n}/{len(todo)} {alias} aug={len(data['mayhem']['augments'])} runes={len(data['runePages'])}")

    if failed:
        print("失败列表:", failed)


if __name__ == "__main__":
    main()
