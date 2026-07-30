"""拉取 hexdata.com.cn 的静态 JSON(中文英雄/强化统计、出装、三强化组合)。

所有响应先写入临时目录；批次完整、首尾 buildId 一致后才逐文件替换正式
缓存，并在最后写入带 SHA-256 的清单。这样即使下载或本地替换中断，合成
脚本也会因清单不匹配而失败关闭，不会静默混用两批英雄明细。
"""
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from common import fetch_json, save_json, load_json

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "raw", "hexdata")
BASE = "https://hexdata.com.cn/data"
RATE = 0.35  # 秒/请求
MANIFEST_SCHEMA = "hexdata-cache-v1"
CORE_FILES = ("meta.json", "heroes.json", "augments.json", "hero_formula_items.json")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _latest_entry(archive):
    entries = archive.get("entries") if isinstance(archive, dict) else None
    if not entries or not isinstance(entries[0], dict):
        raise ValueError("hexdata archive 缺少最新构建记录")
    latest = entries[0]
    for field in ("buildId", "reportPatch", "reportDate", "heroCount", "augmentCount"):
        if latest.get(field) in (None, ""):
            raise ValueError(f"hexdata archive 最新构建缺少 {field}")
    return latest


def _hero_ids(rows):
    if not isinstance(rows, list):
        raise ValueError("hexdata heroes.json 不是数组")
    ids = [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id") is not None]
    if len(ids) != len(rows) or len(ids) != len(set(ids)):
        raise ValueError("hexdata heroes.json 存在缺失或重复 hero id")
    return ids


def _validate_payload(root, expected):
    """验证一批 hexdata 文件与 archive/meta 声明相符。"""
    meta = load_json(os.path.join(root, "meta.json"))
    heroes = load_json(os.path.join(root, "heroes.json"))
    augments = load_json(os.path.join(root, "augments.json"))
    formula = load_json(os.path.join(root, "hero_formula_items.json"))
    ids = _hero_ids(heroes)

    for field in ("buildId", "reportPatch", "reportDate"):
        if str(meta.get(field) or "") != str(expected.get(field) or ""):
            raise ValueError(
                f"hexdata meta {field} 不一致: {meta.get(field)!r} != {expected.get(field)!r}"
            )
    if len(ids) != int(expected["heroCount"]) or meta.get("heroCount") != len(ids):
        raise ValueError("hexdata 英雄总数与 archive/meta 不一致")
    if not isinstance(augments, list) or len(augments) != int(expected["augmentCount"]):
        raise ValueError("hexdata 强化总数与 archive 不一致")
    if meta.get("augmentCount") != len(augments):
        raise ValueError("hexdata 强化总数与 meta 不一致")
    if not isinstance(formula, dict) or not isinstance(formula.get("byHeroId"), dict):
        raise ValueError("hexdata hero_formula_items.json 结构无效")

    # buildId 末段来自上游 corePayloadHash；至少把 archive 与 meta 的声明绑定。
    core_hash = meta.get("corePayloadHash") or (meta.get("dataContract") or {}).get("corePayloadHash")
    build_suffix = str(expected["buildId"]).rsplit("-", 1)[-1]
    if not isinstance(core_hash, str) or not core_hash.startswith(build_suffix):
        raise ValueError("hexdata buildId 与 meta corePayloadHash 不一致")

    for hero_id in ids:
        path = os.path.join(root, "heroes", f"{hero_id}.json")
        detail = load_json(path)
        if not isinstance(detail, dict):
            raise ValueError(f"hexdata 英雄明细不是对象: {hero_id}")
        for field in ("augments", "items", "trios"):
            if not isinstance(detail.get(field), list):
                raise ValueError(f"hexdata 英雄 {hero_id} 缺少数组字段 {field}")
    return ids


def _make_manifest(root, latest, hero_ids):
    relative_files = list(CORE_FILES) + [f"heroes/{hero_id}.json" for hero_id in hero_ids]
    return {
        "schemaVersion": MANIFEST_SCHEMA,
        "buildId": latest["buildId"],
        "reportPatch": latest["reportPatch"],
        "reportDate": latest["reportDate"],
        "sourceGeneratedAt": latest.get("generatedAt"),
        "heroCount": int(latest["heroCount"]),
        "augmentCount": int(latest["augmentCount"]),
        "files": {
            relative: _sha256(os.path.join(root, relative))
            for relative in sorted(relative_files)
        },
    }


def verify_cache_manifest(root=OUT):
    """验证正式缓存是清单声明的同一完整批次；失败时抛出 ValueError。"""
    stamp_path = os.path.join(root, "_buildId.json")
    manifest = load_json(stamp_path)
    files = manifest.get("files")
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA or not isinstance(files, dict):
        raise ValueError("hexdata 缓存缺少 v1 哈希清单")

    ids = _validate_payload(root, manifest)
    expected_files = set(CORE_FILES) | {f"heroes/{hero_id}.json" for hero_id in ids}
    if set(files) != expected_files:
        raise ValueError("hexdata 清单文件集合与英雄索引不一致")

    for relative, expected_hash in sorted(files.items()):
        normalized = os.path.normpath(relative)
        if os.path.isabs(relative) or normalized != relative or normalized.startswith(".."):
            raise ValueError(f"hexdata 清单包含不安全路径: {relative!r}")
        path = os.path.join(root, relative)
        if not os.path.isfile(path):
            raise ValueError(f"hexdata 清单文件缺失: {relative}")
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            raise ValueError(f"hexdata 清单哈希不一致: {relative}")
    return manifest


def _promote(staging, manifest):
    """逐文件原子替换，清单最后落盘；中断时旧清单会让读取端失败关闭。"""
    os.makedirs(os.path.join(OUT, "heroes"), exist_ok=True)
    for relative in sorted(manifest["files"]):
        destination = os.path.join(OUT, relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        os.replace(os.path.join(staging, relative), destination)
    os.replace(os.path.join(staging, "_buildId.json"), os.path.join(OUT, "_buildId.json"))


def main():
    if "--verify-only" in sys.argv[1:]:
        manifest = verify_cache_manifest()
        print("hexdata cache verified:", manifest["buildId"], manifest["heroCount"], "heroes")
        return

    archive = fetch_json(f"{BASE}/archive.json")
    latest = _latest_entry(archive)
    print("hexdata build:", latest["buildId"], "patch", latest["reportPatch"])

    stamp_path = os.path.join(OUT, "_buildId.json")
    if os.path.exists(stamp_path) and load_json(stamp_path).get("buildId") == latest["buildId"]:
        try:
            verify_cache_manifest()
        except (OSError, ValueError, KeyError, TypeError) as error:
            print("同 build 缓存未通过完整性校验,重新下载:", error)
        else:
            print("build 未变化且缓存完整,跳过")
            return

    staging_parent = os.path.dirname(OUT)
    os.makedirs(staging_parent, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".hexdata-stage-", dir=staging_parent) as staging:
        for name in ("meta", "heroes", "augments", "hero_formula_items"):
            save_json(
                os.path.join(staging, f"{name}.json"),
                fetch_json(f"{BASE}/{name}.json", min_interval=RATE),
            )
            print("ok", name)

        heroes = load_json(os.path.join(staging, "heroes.json"))
        ids = _hero_ids(heroes)
        for n, hero_id in enumerate(ids, 1):
            save_json(
                os.path.join(staging, "heroes", f"{hero_id}.json"),
                fetch_json(f"{BASE}/heroes/{hero_id}.json", min_interval=RATE),
            )
            if n % 30 == 0 or n == len(ids):
                print(f"heroes {n}/{len(ids)}")

        ending_latest = _latest_entry(fetch_json(f"{BASE}/archive.json", min_interval=RATE))
        if ending_latest["buildId"] != latest["buildId"]:
            raise RuntimeError(
                "hexdata 下载期间 buildId 已变化,放弃本批: "
                f"{latest['buildId']} -> {ending_latest['buildId']}"
            )

        ids = _validate_payload(staging, latest)
        manifest = _make_manifest(staging, latest, ids)
        save_json(os.path.join(staging, "_buildId.json"), manifest)
        verify_cache_manifest(staging)
        _promote(staging, manifest)

    verified = verify_cache_manifest()
    print("done:", verified["heroCount"], "hero files; manifest verified")


if __name__ == "__main__":
    main()
