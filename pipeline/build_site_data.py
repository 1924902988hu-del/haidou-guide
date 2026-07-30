"""合成站点数据:把 ddragon/hero_list/cherry/hexdata/opgg 按 heroId join。

输出(自包含,前端每页只需一个请求):
- site/data/index.json                首页索引(搜索昵称、定位、梯度)
- site/data/heroes/{alias}.json       英雄详情(天赋/加点/召唤师技能/海克斯/出装)

合规:海克斯强化与模式装备不输出胜率或内部排序分数,只公开推荐标签;英雄胜率保留。
"""
import os
import re
import sys
import unicodedata
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from common import load_json, save_json, valid_item_core_rows
from fetch_hexdata import verify_cache_manifest
from video_intelligence import video_is_currently_publishable

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "data", "raw")
SITE_DATA = os.path.join(ROOT, "site", "data")
VIDEO_CATALOG = os.path.join(ROOT, "data", "videos", "catalog.json")

DD_IMG = "https://ddragon.leagueoflegends.com/cdn"
CD_BASE = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default"

# 图片本地化:站点只引用本地路径,fetch_images.py 按清单补齐缺失文件
IMG_MANIFEST = {}
PUBLIC_VIDEO_FIELDS = {
    "analysisLabel",
    "caveat",
    "creator",
    "evidence",
    "expiresAt",
    "heroes",
    "id",
    "keyPoints",
    "patchImpact",
    "patchMentioned",
    "patchStatus",
    "platform",
    "publishedAt",
    "strategies",
    "strategy",
    "summary",
    "title",
    "url",
}


def img(rel, url):
    if url:
        IMG_MANIFEST[rel] = url
    return rel


def register_preserved_item_assets(rows, version):
    """把同补丁回退保留的 OP.GG 核心装备重新登记到图片清单。"""
    for row in rows:
        for item in row:
            item_id = item.get("id")
            if (
                isinstance(item_id, bool)
                or not isinstance(item_id, int)
                or item_id <= 0
            ):
                raise ValueError(f"保留的 OP.GG 核心装备 ID 无效: {item_id!r}")
            expected_rel = f"assets/img/item/{item_id}.png"
            if item.get("icon") != expected_rel:
                raise ValueError(
                    "保留的 OP.GG 核心装备图片路径无效: "
                    f"{item.get('icon')!r}"
                )
            img(
                expected_rel,
                f"{DD_IMG}/{version}/img/item/{item_id}.png",
            )
    return rows


def public_video_record(video):
    """只把可向读者解释的字段投影到静态站点数据。"""
    return {
        key: value
        for key, value in video.items()
        if key in PUBLIC_VIDEO_FIELDS
    }


def public_video_availability(video):
    """首页只需知道运行时仍可见的视频数量和版本状态。"""
    return {
        "expiresAt": video.get("expiresAt"),
        "patchStatus": video.get("patchStatus"),
    }


def normalize_search_term(value):
    """统一全角/半角与大小写，避免同一个搜索词产生多个二进制写法。"""
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def finalize_index_search_terms(index_rows):
    """移除跨英雄冲突词与无辨识度的单字母英文词。"""
    owners = {}
    terms_by_alias = {}
    for row in index_rows:
        terms = sorted({
            term
            for term in (
                normalize_search_term(value)
                for value in str(row.get("search") or "").split(",")
            )
            if term
        })
        terms_by_alias[row["alias"]] = terms
        for term in terms:
            owners.setdefault(term, set()).add(row["alias"])

    ambiguous = {
        term for term, aliases in owners.items()
        if len(aliases) > 1
    }
    single_letter_latin = {
        term for term in owners
        if re.fullmatch(r"[a-z]", term)
    }
    disallowed = ambiguous | single_letter_latin
    removed_occurrences = 0
    for row in index_rows:
        terms = terms_by_alias[row["alias"]]
        safe_terms = [term for term in terms if term not in disallowed]
        required = {
            normalize_search_term(row[field])
            for field in ("alias", "name", "epithet")
        }
        missing = sorted(required - set(safe_terms))
        if missing:
            raise ValueError(
                f"{row['alias']}: 搜索词清理误删必要身份 {missing}"
            )
        removed_occurrences += len(terms) - len(safe_terms)
        row["search"] = ",".join(safe_terms)
    return {
        "ambiguousTerms": len(ambiguous),
        "singleLetterTerms": len(single_letter_latin),
        "removedOccurrences": removed_occurrences,
    }


STAT_MODS = {
    5001: "成长生命值", 5005: "攻击速度", 5007: "技能急速", 5008: "自适应之力",
    5010: "移动速度", 5011: "生命值", 5013: "韧性和减速抵抗",
}
RARITY_NORM = {"白银": "白银", "银": "白银", "黄金": "黄金", "金": "黄金", "棱彩": "棱彩"}
ROLE_ZH = {"fighter": "战士", "mage": "法师", "assassin": "刺客",
           "marksman": "射手", "tank": "坦克", "support": "辅助"}


def public_patch(version):
    """16.14.1 / 16.14 等数据版本转成 Riot 对外补丁号 26.14。"""
    match = re.fullmatch(r"16\.(\d{1,2})(?:\.\d+)?", str(version or ""))
    if not match:
        raise ValueError(f"无法转换来源版本: {version!r}")
    return f"26.{match.group(1)}"


def require_opgg_source_versions(record, expected, context):
    """拒绝把其他 OP.GG 补丁的英雄缓存混入本轮站点快照。"""
    actual = (record or {}).get("sourceVersions")
    if actual != expected:
        raise ValueError(
            f"{context}: OP.GG 缓存版本不一致 {actual!r} != {expected!r}"
        )


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
    # 英雄明细没有自带 buildId；先用抓取批次清单验证全部文件，拒绝混合缓存。
    verify_cache_manifest(os.path.join(RAW, "hexdata"))
    version = load_json(os.path.join(RAW, "static_meta.json"))["ddragonVersion"]
    game_patch = public_patch(version)
    opgg_meta = load_json(os.path.join(RAW, "opgg", "_meta.json"))
    opgg_versions = opgg_meta.get("sourceVersions") or {}
    opgg_patch = public_patch(opgg_versions.get("mayhem"))
    opgg_runes_patch = public_patch(opgg_versions.get("aram"))

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
    catalog_video_rows = (
        load_json(VIDEO_CATALOG).get("videos", [])
        if os.path.exists(VIDEO_CATALOG)
        else []
    )
    video_rows = [
        video
        for video in catalog_video_rows
        if video_is_currently_publishable(video)
    ]
    videos_by_hero = {}
    for video in video_rows:
        for video_alias in video.get("heroes", []):
            videos_by_hero.setdefault(video_alias, []).append(
                public_video_record(video)
            )
    for video_alias in videos_by_hero:
        videos_by_hero[video_alias].sort(
            key=lambda row: (row.get("publishedAt") or "", row.get("reviewedAt") or ""),
            reverse=True,
        )

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
        if opgg:
            require_opgg_source_versions(opgg, opgg_versions, alias)

        # zh_CN 语义:ddragon name=称号,title=音译名;hero_list 同向
        display = hl["title"]      # 安妮
        epithet = hl["name"]       # 黑暗之女
        roles = [r for r in hl.get("roles", []) if r in ROLE_ZH]
        search_terms = set()
        for kw in (hl.get("keywords") or "").split(","):
            term = normalize_search_term(kw)
            if term:
                search_terms.add(term)
        for t in hx.get("searchTerms") or []:
            term = normalize_search_term(t)
            if term:
                search_terms.add(term)
        search_terms.update(
            normalize_search_term(value)
            for value in (display, epithet, alias)
        )

        icon = img(f"assets/img/champion/{alias}.png", f"{DD_IMG}/{version}/img/champion/{alias}.png")
        stats = {"tier": hx.get("tier"), "winRate": hx.get("winRate"), "pickRate": hx.get("pickRate"),
                 "games": hx.get("games"), "kda": hx.get("kda")}
        hero_videos = videos_by_hero.get(alias, [])
        video_availability = [
            public_video_availability(video)
            for video in hero_videos
        ]
        index_rows.append({
            "alias": alias, "name": display, "epithet": epithet,
            "roles": roles, "icon": icon,
            "tier": stats["tier"], "winRate": stats["winRate"],
            "search": ",".join(sorted(search_terms)),
            "videoAvailability": video_availability,
        })

        # ---- 详情 ----
        detail_path = os.path.join(RAW, "hexdata", "heroes", f"{key}.json")
        hx_detail = load_json(detail_path) if os.path.exists(detail_path) else {}
        existing_site_path = os.path.join(SITE_DATA, "heroes", f"{alias}.json")
        existing_site = load_json(existing_site_path) if os.path.exists(existing_site_path) else {}

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
                "hexLabel": row.get("hexLabel"),
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
            "games": t.get("games"),
        } for t in trios]

        formula = hx_formula.get(key) or {}
        hx_items = sorted(hx_detail.get("items", []), key=lambda i: -(i.get("hexScore") or 0))

        def uniq(seq):
            seen = set()
            return [x for x in seq if not (x in seen or seen.add(x))]

        raw_core_rows = valid_item_core_rows(
            m.get("items", {}).get("cores", [])
        )
        opgg_core_rows = [
            [item_ref(i) for i in row]
            for row in raw_core_rows[:3]
        ]
        existing_core_rows = valid_item_core_rows(
            (existing_site.get("items") or {}).get("opggCores") or []
        )
        existing_patch = (existing_site.get("patch") or {}).get("opggGame")
        # 同补丁的缓存响应偶尔只返回部分组合。新结果更少时保留已发布的完整集合，
        # 防止视频目录重建顺带把正常攻略数据回退。
        if existing_patch == opgg_patch and len(opgg_core_rows) < len(existing_core_rows):
            opgg_core_rows = register_preserved_item_assets(
                existing_core_rows,
                version,
            )

        detail = {
            "name": display, "epithet": epithet, "roles": roles, "icon": icon,
            "patch": {"game": game_patch, "opggGame": opgg_patch,
                      "opggRunes": opgg_runes_patch, "statsGame": stats_patch,
                      "ddragon": version, "hexdataDate": hx_build.get("reportDate")},
            "stats": stats,
            "runes": {
                "pages": rune_pages,
                "source": (
                    f"op.gg 极地大乱斗 {opgg_runes_patch}"
                    "(海克斯模式无独立天赋统计,思路通用)"
                ),
            },
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
                "opggCores": opgg_core_rows,
                "hexTop": [{
                    "item": item_ref(r["itemId"], r.get("itemName")),
                    "hexLabel": r.get("hexLabel"),
                } for r in hx_items[:8]],
            },
            "douyinUrl": "https://www.douyin.com/search/" + urllib.parse.quote(f"{display} 海克斯大乱斗"),
            "videos": hero_videos,
        }
        save_json(os.path.join(SITE_DATA, "heroes", f"{alias}.json"), detail)
        written += 1

    search_cleanup = finalize_index_search_terms(index_rows)
    expected_files = {f"{r['alias']}.json" for r in index_rows}
    hero_dir = os.path.join(SITE_DATA, "heroes")
    if os.path.isdir(hero_dir):
        for filename in os.listdir(hero_dir):
            if filename.endswith(".json") and filename not in expected_files:
                os.remove(os.path.join(hero_dir, filename))

    index_rows.sort(key=lambda r: ((r["tier"] or 9), -(r["winRate"] or 0)))
    save_json(os.path.join(SITE_DATA, "index.json"), {
        "patch": {"game": game_patch, "opggGame": opgg_patch,
                  "opggRunes": opgg_runes_patch, "statsGame": stats_patch,
                  "ddragon": version, "hexdataDate": hx_build.get("reportDate")},
        "heroes": index_rows,
    })
    save_json(os.path.join(RAW, "image_manifest.json"), IMG_MANIFEST)
    print(f"index: {len(index_rows)} heroes, details written: {written}, images in manifest: {len(IMG_MANIFEST)}")
    print(
        "搜索词清理:"
        f" 跨英雄冲突 {search_cleanup['ambiguousTerms']} 个,"
        f" 单字母英文 {search_cleanup['singleLetterTerms']} 个,"
        f" 删除出现 {search_cleanup['removedOccurrences']} 次"
    )

    # 自检:昵称索引必须包含"女枪"
    blob = ",".join(r["search"] for r in index_rows)
    assert "女枪" in blob, "昵称索引缺失『女枪』"
    print("昵称自检通过(女枪→", [r["name"] for r in index_rows if "女枪" in r["search"]], ")")


if __name__ == "__main__":
    main()
