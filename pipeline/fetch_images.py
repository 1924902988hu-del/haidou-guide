"""按构建清单原子下载缺失或损坏图片，并清理过期站点 PNG。"""
import os
import re
import stat
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from common import fetch, load_json
from validate_site_data import check_png_asset

ROOT = os.path.join(os.path.dirname(__file__), "..")
SITE = os.path.join(ROOT, "site")
RATE = 0.12
IMAGE_PATH_RE = re.compile(
    r"assets/img/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.png"
)


def validate_image_source(rel, url, ddragon_version=None):
    """把本地资源类别绑定到唯一允许的上游主机与路径。"""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise ValueError(f"图片清单来源无效: {url!r}") from exc
    if (
        parsed.scheme != "https"
        or parsed.query
        or parsed.fragment
        or parsed.netloc not in {
            "ddragon.leagueoflegends.com",
            "raw.communitydragon.org",
        }
    ):
        raise ValueError(f"图片清单来源无效: {url!r}")

    if ddragon_version is not None and not re.fullmatch(
        r"\d+\.\d+\.\d+",
        str(ddragon_version),
    ):
        raise ValueError(
            f"图片清单来源无效: Data Dragon 版本 {ddragon_version!r}"
        )
    version = (
        re.escape(ddragon_version)
        if ddragon_version is not None
        else r"\d+\.\d+\.\d+"
    )

    category_patterns = (
        ("champion", r"([A-Za-z][A-Za-z0-9]*)"),
        ("item", r"(\d+)"),
        ("spell", r"([A-Za-z][A-Za-z0-9_]*)"),
    )
    for category, filename_pattern in category_patterns:
        match = re.fullmatch(
            rf"assets/img/{category}/{filename_pattern}\.png",
            rel,
        )
        if not match:
            continue
        expected_path = (
            rf"/cdn/{version}/img/{category}/"
            rf"{re.escape(match.group(1))}\.png"
        )
        if (
            parsed.netloc != "ddragon.leagueoflegends.com"
            or not re.fullmatch(expected_path, parsed.path)
        ):
            raise ValueError(f"图片清单来源无效: {rel!r} -> {url!r}")
        return

    if re.fullmatch(r"assets/img/rune/[A-Za-z0-9_.-]+\.png", rel):
        if (
            parsed.netloc != "ddragon.leagueoflegends.com"
            or not re.fullmatch(
                r"/cdn/img/perk-images/"
                r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.png",
                parsed.path,
            )
        ):
            raise ValueError(f"图片清单来源无效: {rel!r} -> {url!r}")
        return

    if re.fullmatch(r"assets/img/augment/\d+\.png", rel):
        if (
            parsed.netloc != "raw.communitydragon.org"
            or not re.fullmatch(
                r"/latest/plugins/rcp-be-lol-game-data/global/default/"
                r"(?:"
                r"assets/ux/(?:cherry|kiwi)/augments/icons/"
                r"|assets/maps/particles/kiwi/"
                r")"
                r"[a-z0-9_.-]+\.png",
                parsed.path,
            )
        ):
            raise ValueError(f"图片清单来源无效: {rel!r} -> {url!r}")
        return

    raise ValueError(f"图片清单来源无效: 未知资源类别 {rel!r}")


def validate_manifest(manifest, ddragon_version=None):
    """拒绝不完整或越界的图片清单，避免清理错误目录。"""
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("图片清单必须是非空对象")
    for rel, url in manifest.items():
        if (
            not isinstance(rel, str)
            or not IMAGE_PATH_RE.fullmatch(rel)
            or ".." in rel.split("/")
        ):
            raise ValueError(f"图片清单路径无效: {rel!r}")
        if not isinstance(url, str):
            raise ValueError(f"图片清单来源无效: {url!r}")
        validate_image_source(rel, url, ddragon_version=ddragon_version)


def png_validation_error(path):
    errors = []
    if check_png_asset(path, errors, str(path)) is None:
        return errors[0]
    return None


def image_refresh_candidates(manifest, site=SITE, ddragon_version=None):
    """区分不存在与已存在但不可完整解码的清单图片。"""
    validate_manifest(manifest, ddragon_version=ddragon_version)
    missing = {}
    damaged = {}
    for rel, url in manifest.items():
        path = os.path.join(site, *rel.split("/"))
        if not os.path.isfile(path):
            missing[rel] = url
        elif png_validation_error(path):
            damaged[rel] = url
    return missing, damaged


def download_image_atomic(
    rel,
    url,
    site=SITE,
    fetcher=fetch,
    ddragon_version=None,
):
    """校验同目录临时文件后，以原子替换更新最终图片。"""
    validate_manifest(
        {rel: url},
        ddragon_version=ddragon_version,
    )
    path = os.path.join(site, *rel.split("/"))
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    mode = (
        stat.S_IMODE(os.stat(path).st_mode)
        if os.path.isfile(path)
        else 0o644
    )
    data = fetcher(url, min_interval=RATE, binary=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=".image-download-",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        error = png_validation_error(temp_path)
        if error:
            raise ValueError(f"{rel}: 下载图片不可解码 ({error})")
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def cleanup_abandoned_downloads(site=SITE):
    """清理仅由本下载器生成、因进程强退遗留的临时文件。"""
    image_root = os.path.join(site, "assets", "img")
    removed = []
    if not os.path.isdir(image_root):
        return removed
    for directory, _, files in os.walk(image_root):
        for filename in files:
            if not (
                filename.startswith(".image-download-")
                and filename.endswith(".tmp")
            ):
                continue
            path = os.path.join(directory, filename)
            os.remove(path)
            removed.append(os.path.relpath(path, site).replace(os.sep, "/"))
    return sorted(removed)


def stale_image_paths(manifest, site=SITE, ddragon_version=None):
    """返回站点图片目录内、不在完整清单中的 PNG 相对路径。"""
    validate_manifest(manifest, ddragon_version=ddragon_version)
    expected = set(manifest)
    image_root = os.path.join(site, "assets", "img")
    actual = set()
    if not os.path.isdir(image_root):
        return []
    for directory, _, files in os.walk(image_root):
        for filename in files:
            if not filename.lower().endswith(".png"):
                continue
            path = os.path.join(directory, filename)
            actual.add(os.path.relpath(path, site).replace(os.sep, "/"))
    return sorted(actual - expected)


def prune_stale_images(manifest, site=SITE, ddragon_version=None):
    """仅删除站点图片树内、由完整清单判定为过期的 PNG。"""
    stale = stale_image_paths(
        manifest,
        site=site,
        ddragon_version=ddragon_version,
    )
    for rel in stale:
        os.remove(os.path.join(site, *rel.split("/")))
    return stale


def main():
    manifest = load_json(os.path.join(ROOT, "data", "raw", "image_manifest.json"))
    ddragon_version = load_json(
        os.path.join(ROOT, "data", "raw", "static_meta.json")
    )["ddragonVersion"]
    validate_manifest(manifest, ddragon_version=ddragon_version)
    abandoned = cleanup_abandoned_downloads()
    if abandoned:
        print(f"清理上次中断遗留临时文件 {len(abandoned)} 个")
    missing, damaged = image_refresh_candidates(
        manifest,
        ddragon_version=ddragon_version,
    )
    refresh = {**missing, **damaged}
    print(
        f"清单 {len(manifest)} 张,缺失 {len(missing)} 张,"
        f"损坏 {len(damaged)} 张"
    )
    fail = []
    for n, (rel, url) in enumerate(sorted(refresh.items()), 1):
        try:
            download_image_atomic(
                rel,
                url,
                ddragon_version=ddragon_version,
            )
        except Exception as e:
            fail.append((rel, str(e)[:80]))
        if n % 100 == 0 or n == len(refresh):
            print(f"{n}/{len(refresh)}")
    if fail:
        print("失败:", fail[:20], f"共 {len(fail)}")
        raise SystemExit(1)
    remaining_missing, remaining_damaged = image_refresh_candidates(
        manifest,
        ddragon_version=ddragon_version,
    )
    if remaining_missing or remaining_damaged:
        print(
            "下载后仍异常:",
            {
                "missing": sorted(remaining_missing)[:20],
                "damaged": sorted(remaining_damaged)[:20],
            },
        )
        raise SystemExit(1)
    stale = prune_stale_images(
        manifest,
        ddragon_version=ddragon_version,
    )
    print(f"清理过期图片 {len(stale)} 张")
    if stale:
        print("已清理:", stale)


if __name__ == "__main__":
    main()
