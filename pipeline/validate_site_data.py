"""验证发布数据的引用、完整性与合规边界。"""
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITE = os.path.join(ROOT, "site")
DATA = os.path.join(SITE, "data")
VIDEO_CATALOG = os.path.join(ROOT, "data", "videos", "catalog.json")


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def check_asset(path, errors, context):
    if not path or not path.startswith("assets/"):
        return
    if not os.path.isfile(os.path.join(SITE, path)):
        errors.append(f"{context}: 图片不存在 {path}")


def main():
    errors = []
    index = load(os.path.join(DATA, "index.json"))
    heroes = index.get("heroes") or []
    aliases = [hero.get("alias") for hero in heroes]
    if len(heroes) < 170:
        errors.append(f"英雄数量异常: {len(heroes)}")
    if len(aliases) != len(set(aliases)):
        errors.append("首页存在重复 alias")

    detail_dir = os.path.join(DATA, "heroes")
    detail_files = {name for name in os.listdir(detail_dir) if name.endswith(".json")}
    expected = {f"{alias}.json" for alias in aliases}
    if detail_files != expected:
        missing = sorted(expected - detail_files)
        extra = sorted(detail_files - expected)
        errors.append(f"详情文件不匹配 missing={missing[:5]} extra={extra[:5]}")

    if os.path.exists(VIDEO_CATALOG):
        catalog = load(VIDEO_CATALOG)
        video_ids = []
        for video in catalog.get("videos", []):
            video_ids.append(video.get("id"))
            if video.get("analysisStatus") not in {"metadata-only", "visual-reviewed", "human-verified"}:
                errors.append(f"{video.get('id')}: 视频核对状态无效")
            if not str(video.get("url", "")).startswith("https://"):
                errors.append(f"{video.get('id')}: 视频来源链接无效")
            if video.get("analysisStatus") != "metadata-only":
                if not video.get("summary") or not video.get("keyPoints") or not video.get("reviewedAt"):
                    errors.append(f"{video.get('id')}: 已核对视频缺少摘要、要点或核对日期")
            for video_alias in video.get("heroes", []):
                if video_alias not in aliases:
                    errors.append(f"{video.get('id')}: 未知英雄 alias {video_alias}")
        if len(video_ids) != len(set(video_ids)):
            errors.append("视频目录存在重复 id")

    for row in heroes:
        alias = row.get("alias")
        check_asset(row.get("icon"), errors, f"index/{alias}")
        detail = load(os.path.join(detail_dir, f"{alias}.json"))
        if str(detail.get("id")) != str(row.get("id")) or detail.get("alias") != alias:
            errors.append(f"{alias}: 索引与详情身份不一致")
        if detail.get("patch", {}).get("game") != index.get("patch", {}).get("game"):
            errors.append(f"{alias}: 补丁号与首页不一致")
        check_asset(detail.get("icon"), errors, alias)

        for augment in detail.get("augments", []):
            if "winRate" in augment:
                errors.append(f"{alias}/{augment.get('name')}: 不应输出强化胜率")
            if "?" in (augment.get("desc") or ""):
                errors.append(f"{alias}/{augment.get('name')}: 描述仍有占位符")
            check_asset(augment.get("icon"), errors, f"{alias}/{augment.get('name')}")
        for combo in detail.get("combos", []):
            for augment in combo.get("augments", []):
                if augment:
                    check_asset(augment.get("icon"), errors, f"{alias}/combo")
        for group in ("starter", "core", "boots"):
            for item in detail.get("items", {}).get(group, []):
                check_asset(item.get("icon"), errors, f"{alias}/{group}")
        for row_items in detail.get("items", {}).get("opggCores", []):
            for item in row_items:
                check_asset(item.get("icon"), errors, f"{alias}/opggCores")
        for ranked in detail.get("items", {}).get("hexTop", []):
            check_asset((ranked.get("item") or {}).get("icon"), errors, f"{alias}/hexTop")
        for page in detail.get("runes", {}).get("pages", []):
            for style in (page.get("primaryStyle"), page.get("subStyle")):
                check_asset((style or {}).get("icon"), errors, f"{alias}/runeStyle")
            for rune in (page.get("primary") or []) + (page.get("sub") or []):
                check_asset((rune or {}).get("icon"), errors, f"{alias}/rune")
        for pair in detail.get("spells", []):
            for spell in pair:
                check_asset((spell or {}).get("icon"), errors, f"{alias}/spell")

    if errors:
        print("\n".join(f"ERROR {error}" for error in errors[:100]))
        raise SystemExit(f"数据验证失败,共 {len(errors)} 项")
    print(f"数据验证通过: {len(heroes)} 位英雄、{len(detail_files)} 个详情文件、全部本地图片引用有效")


if __name__ == "__main__":
    main()
