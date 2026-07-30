"""验证发布数据的引用、完整性与合规边界。"""
import datetime
import json
import math
import os
import re
import struct
import urllib.parse
import zlib

from build_site_data import (
    PUBLIC_VIDEO_FIELDS,
    normalize_search_term,
    public_patch,
)
from common import valid_item_core_rows
from video_intelligence import evidence_coverage_label, video_is_currently_publishable

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITE = os.path.join(ROOT, "site")
DATA = os.path.join(SITE, "data")
VIDEO_CATALOG = os.path.join(ROOT, "data", "videos", "catalog.json")
VIDEO_STATUSES = {"metadata-only", "multimodal-reviewed", "visual-reviewed", "human-verified"}
PATCH_STATUSES = {"current", "needs-game-check", "obsolete"}
PATCH_IMPACT_STATUSES = {
    "direct-hero-balance-change",
    "direct-recommended-item-change",
}
HERO_ROLES = {"fighter", "mage", "assassin", "marksman", "tank", "support"}
AUGMENT_RARITIES = {"白银", "黄金", "棱彩"}
SKILL_LETTERS = {"Q", "W", "E", "R"}
SUMMONER_SPELL_ICONS = {
    "净化": "assets/img/spell/SummonerBoost.png",
    "屏障": "assets/img/spell/SummonerBarrier.png",
    "幽灵疾步": "assets/img/spell/SummonerHaste.png",
    "引燃": "assets/img/spell/SummonerDot.png",
    "标记": "assets/img/spell/SummonerSnowball.png",
    "治疗术": "assets/img/spell/SummonerHeal.png",
    "清晰术": "assets/img/spell/SummonerMana.png",
    "虚弱": "assets/img/spell/SummonerExhaust.png",
    "闪现": "assets/img/spell/SummonerFlash.png",
}
PUBLIC_HERO_DETAIL_FIELDS = {
    "name",
    "epithet",
    "roles",
    "icon",
    "patch",
    "stats",
    "runes",
    "skills",
    "spells",
    "augments",
    "combos",
    "items",
    "douyinUrl",
    "videos",
}
PUBLIC_PATCH_FIELDS = {
    "game",
    "opggGame",
    "opggRunes",
    "statsGame",
    "ddragon",
    "hexdataDate",
}
PUBLIC_INDEX_FIELDS = {"patch", "heroes"}
PUBLIC_INDEX_HERO_FIELDS = {
    "alias",
    "name",
    "epithet",
    "roles",
    "icon",
    "tier",
    "winRate",
    "search",
    "videoAvailability",
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_BIT_DEPTHS = {
    0: {1, 2, 4, 8, 16},
    2: {8, 16},
    3: {1, 2, 4, 8},
    4: {8, 16},
    6: {8, 16},
}
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
MAX_PNG_FILE_BYTES = 32 * 1024 * 1024
MAX_PNG_DECODED_BYTES = 64 * 1024 * 1024


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def check_asset(path, errors, context):
    if not path:
        return
    if (
        not isinstance(path, str)
        or not re.fullmatch(r"assets/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", path)
        or ".." in path.split("/")
    ):
        errors.append(f"{context}: 图片路径无效 {path!r}")
        return
    if not os.path.isfile(os.path.join(SITE, path)):
        errors.append(f"{context}: 图片不存在 {path}")


def _png_pass_size(size, start, step):
    if size <= start:
        return 0
    return (size - start + step - 1) // step


def _png_scanline_layout(width, height, bits_per_pixel, interlace):
    if interlace == 0:
        passes = ((width, height),)
    else:
        passes = tuple(
            (
                _png_pass_size(width, x_start, x_step),
                _png_pass_size(height, y_start, y_step),
            )
            for x_start, y_start, x_step, y_step in (
                (0, 0, 8, 8),
                (4, 0, 8, 8),
                (0, 4, 4, 8),
                (2, 0, 4, 4),
                (0, 2, 2, 4),
                (1, 0, 2, 2),
                (0, 1, 1, 2),
            )
        )
    return tuple(
        (pass_height, (pass_width * bits_per_pixel + 7) // 8)
        for pass_width, pass_height in passes
        if pass_width and pass_height
    )


def _inspect_png(path):
    file_size = os.path.getsize(path)
    if file_size > MAX_PNG_FILE_BYTES:
        raise ValueError("PNG 文件超过 32 MiB 发布上限")
    with open(path, "rb") as handle:
        payload = handle.read()
    if not payload.startswith(PNG_SIGNATURE):
        raise ValueError("PNG 签名无效")

    offset = len(PNG_SIGNATURE)
    first_chunk = True
    seen_ihdr = False
    seen_palette = False
    seen_idat = False
    idat_finished = False
    seen_iend = False
    idat_parts = []
    width = height = bit_depth = color_type = interlace = None

    while offset < len(payload):
        if len(payload) - offset < 12:
            raise ValueError("PNG 块头或校验值被截断")
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        if length > 0x7FFFFFFF:
            raise ValueError("PNG 块长度超过规范上限")
        chunk_type = payload[offset + 4:offset + 8]
        if not all(
            65 <= byte <= 90 or 97 <= byte <= 122
            for byte in chunk_type
        ):
            raise ValueError("PNG 块类型无效")
        data_start = offset + 8
        data_end = data_start + length
        chunk_end = data_end + 4
        if chunk_end > len(payload):
            raise ValueError("PNG 块数据被截断")
        chunk_data = payload[data_start:data_end]
        expected_crc = struct.unpack(">I", payload[data_end:chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(f"{chunk_type.decode('ascii')} 块 CRC 不匹配")

        if first_chunk and chunk_type != b"IHDR":
            raise ValueError("IHDR 必须是首个 PNG 块")
        first_chunk = False

        if chunk_type == b"IHDR":
            if seen_ihdr or length != 13:
                raise ValueError("IHDR 数量或长度无效")
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filtering,
                interlace,
            ) = struct.unpack(">IIBBBBB", chunk_data)
            if width == 0 or height == 0:
                raise ValueError("PNG 尺寸必须大于零")
            if width > 0x7FFFFFFF or height > 0x7FFFFFFF:
                raise ValueError("PNG 尺寸超过规范上限")
            if (
                color_type not in PNG_BIT_DEPTHS
                or bit_depth not in PNG_BIT_DEPTHS[color_type]
            ):
                raise ValueError("PNG 色彩类型与位深组合无效")
            if compression != 0 or filtering != 0 or interlace not in {0, 1}:
                raise ValueError("PNG 压缩、过滤或交错方法无效")
            seen_ihdr = True
        elif not seen_ihdr:
            raise ValueError("PNG 缺少 IHDR")
        elif chunk_type == b"PLTE":
            if seen_palette or seen_idat or length == 0 or length % 3:
                raise ValueError("PLTE 数量、顺序或长度无效")
            entries = length // 3
            if entries > 256 or color_type in {0, 4}:
                raise ValueError("PLTE 与色彩类型不兼容")
            if color_type == 3 and entries > 2 ** bit_depth:
                raise ValueError("PLTE 条目超过位深可表示范围")
            seen_palette = True
        elif chunk_type == b"IDAT":
            if idat_finished:
                raise ValueError("IDAT 块必须连续")
            if color_type == 3 and not seen_palette:
                raise ValueError("索引色 PNG 缺少 PLTE")
            seen_idat = True
            idat_parts.append(chunk_data)
        else:
            if seen_idat:
                idat_finished = True
            if chunk_type == b"IEND":
                if seen_iend or length != 0 or not seen_idat:
                    raise ValueError("IEND 数量、长度或顺序无效")
                seen_iend = True
                if chunk_end != len(payload):
                    raise ValueError("IEND 后存在额外内容")
            elif chunk_type[0] & 0x20 == 0:
                raise ValueError(
                    f"不支持的关键块 {chunk_type.decode('ascii')}"
                )

        offset = chunk_end
        if seen_iend:
            break

    if not seen_ihdr or not seen_idat or not seen_iend:
        raise ValueError("PNG 缺少 IHDR、IDAT 或 IEND")
    if color_type == 3 and not seen_palette:
        raise ValueError("索引色 PNG 缺少 PLTE")

    bits_per_pixel = PNG_CHANNELS[color_type] * bit_depth
    layout = _png_scanline_layout(
        width,
        height,
        bits_per_pixel,
        interlace,
    )
    decoded_size = sum(
        pass_height * (row_bytes + 1)
        for pass_height, row_bytes in layout
    )
    if decoded_size > MAX_PNG_DECODED_BYTES:
        raise ValueError("PNG 解码数据超过 64 MiB 发布上限")

    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(
            b"".join(idat_parts),
            decoded_size + 1,
        )
    except zlib.error as exc:
        raise ValueError("IDAT zlib 数据损坏") from exc
    if (
        len(decoded) != decoded_size
        or not decoder.eof
        or decoder.unconsumed_tail
    ):
        raise ValueError(
            f"IDAT 解码长度无效 expected={decoded_size} actual={len(decoded)}"
        )

    scanline_offset = 0
    for pass_height, row_bytes in layout:
        for _ in range(pass_height):
            if decoded[scanline_offset] > 4:
                raise ValueError("PNG 扫描线过滤器无效")
            scanline_offset += row_bytes + 1
    if scanline_offset != len(decoded):
        raise ValueError("PNG 扫描线长度不一致")
    return width, height


def check_png_asset(path, errors, context):
    try:
        return _inspect_png(path)
    except (OSError, ValueError, struct.error, zlib.error) as exc:
        errors.append(f"{context}: PNG 无法完整解码 ({exc})")
        return None


def check_site_png_assets(errors):
    image_root = os.path.join(SITE, "assets", "img")
    paths = []
    for directory, _, filenames in os.walk(image_root):
        for filename in filenames:
            path = os.path.join(directory, filename)
            if filename.lower().endswith(".png"):
                paths.append(path)
            elif os.path.isfile(path):
                errors.append(
                    f"站点图片目录包含非 PNG 文件 "
                    f"{os.path.relpath(path, SITE)}"
                )
    for path in sorted(paths):
        check_png_asset(path, errors, os.path.relpath(path, SITE))
    if not paths:
        errors.append("站点图片目录没有 PNG 文件")
    return len(paths)


def check_douyin_search_url(value, errors, context):
    try:
        parsed = urllib.parse.urlparse(str(value or ""))
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.hostname != "www.douyin.com"
        or not parsed.path.startswith("/search/")
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
    ):
        errors.append(f"{context}: 抖音搜索链接无效")


def is_valid_hero_alias(value):
    return isinstance(value, str) and bool(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,31}", value)
    )


def check_index_search_terms(heroes, errors):
    owners = {}
    for hero in heroes:
        if not isinstance(hero, dict) or not isinstance(hero.get("search"), str):
            continue
        context = f"index/{hero.get('alias')}"
        terms = hero["search"].split(",")
        normalized = [normalize_search_term(term) for term in terms]
        if normalized != terms:
            errors.append(f"{context}: 搜索词必须使用 NFKC 小写规范")
        if len(terms) != len(set(terms)):
            errors.append(f"{context}: 搜索词不能重复")
        if any(re.fullmatch(r"[a-z]", term) for term in terms):
            errors.append(f"{context}: 搜索词不能使用单字母英文缩写")
        required = {
            normalize_search_term(hero.get(field))
            for field in ("alias", "name", "epithet")
        }
        missing = sorted(required - set(terms))
        if missing:
            errors.append(f"{context}: 搜索词缺少必要身份 {missing}")
        for term in terms:
            owners.setdefault(term, set()).add(hero.get("alias"))

    for term, aliases in sorted(owners.items()):
        if len(aliases) > 1:
            errors.append(
                f"首页搜索词跨英雄冲突 {term!r}: {sorted(aliases)}"
            )


def is_finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def check_hero_stats(stats, errors, context):
    if not isinstance(stats, dict):
        errors.append(f"{context}: 英雄统计必须是对象")
        return

    tier = stats.get("tier")
    if not isinstance(tier, int) or isinstance(tier, bool) or not 1 <= tier <= 5:
        errors.append(f"{context}: 段位必须是 1–5 的整数")

    for field, label in (("winRate", "胜率"), ("pickRate", "登场率")):
        value = stats.get(field)
        if not is_finite_number(value) or not 0 <= value <= 1:
            errors.append(f"{context}: {label}必须是 0–1 的有限数值")

    games = stats.get("games")
    if (
        not isinstance(games, int)
        or isinstance(games, bool)
        or games < 0
    ):
        errors.append(f"{context}: 样本场次必须是非负整数")

    kda = stats.get("kda")
    if not is_finite_number(kda) or kda < 0:
        errors.append(f"{context}: KDA 必须是非负有限数值")


def check_hero_roles(roles, errors, context):
    if (
        not isinstance(roles, list)
        or not 1 <= len(roles) <= 3
        or any(not isinstance(role, str) or role not in HERO_ROLES for role in roles)
        or len(set(roles)) != len(roles)
    ):
        errors.append(f"{context}: 定位必须为 1–3 个互不重复的已知值")


def check_public_hero_detail_fields(detail, errors, context):
    if not isinstance(detail, dict):
        errors.append(f"{context}: 英雄详情必须是对象")
        return False
    missing_fields = sorted(PUBLIC_HERO_DETAIL_FIELDS - set(detail))
    unexpected_fields = sorted(set(detail) - PUBLIC_HERO_DETAIL_FIELDS)
    if missing_fields:
        errors.append(f"{context}: 英雄详情缺少公开字段 {missing_fields}")
    if unexpected_fields:
        errors.append(f"{context}: 英雄详情包含未公开字段 {unexpected_fields}")
    return True


def check_public_patch_fields(patch, errors, context):
    if not isinstance(patch, dict):
        errors.append(f"{context}: 来源快照必须是对象")
        return False

    missing_fields = sorted(PUBLIC_PATCH_FIELDS - set(patch))
    unexpected_fields = sorted(set(patch) - PUBLIC_PATCH_FIELDS)
    if missing_fields:
        errors.append(f"{context}: 来源快照缺少公开字段 {missing_fields}")
    if unexpected_fields:
        errors.append(f"{context}: 来源快照包含未公开字段 {unexpected_fields}")

    for field, label in (
        ("game", "客户端资料"),
        ("opggGame", "OP.GG 攻略"),
        ("opggRunes", "OP.GG 符文"),
        ("statsGame", "统计"),
    ):
        value = patch.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"\d{2}\.\d{1,2}", value):
            errors.append(f"{context}: {label}版本无效 {value!r}")

    ddragon = patch.get("ddragon")
    if (
        not isinstance(ddragon, str)
        or not re.fullmatch(r"\d{1,2}\.\d{1,2}(?:\.\d+)?", ddragon)
    ):
        errors.append(f"{context}: Data Dragon 版本无效 {ddragon!r}")
    else:
        try:
            expected_game = public_patch(ddragon)
        except ValueError:
            errors.append(f"{context}: Data Dragon 版本无法映射到客户端资料 {ddragon!r}")
        else:
            if patch.get("game") != expected_game:
                errors.append(
                    f"{context}: Data Dragon 与客户端资料版本不一致 "
                    f"{ddragon!r} -> {expected_game!r}"
                )

    snapshot_date = patch.get("hexdataDate")
    try:
        parsed_date = datetime.date.fromisoformat(snapshot_date)
    except (TypeError, ValueError):
        parsed_date = None
    if parsed_date is None or parsed_date.isoformat() != snapshot_date:
        errors.append(f"{context}: 统计日期无效 {snapshot_date!r}")
    return True


def check_public_index_fields(index, errors):
    if not isinstance(index, dict):
        errors.append("首页数据必须是对象")
        return False
    missing_fields = sorted(PUBLIC_INDEX_FIELDS - set(index))
    unexpected_fields = sorted(set(index) - PUBLIC_INDEX_FIELDS)
    if missing_fields:
        errors.append(f"首页数据缺少公开字段 {missing_fields}")
    if unexpected_fields:
        errors.append(f"首页数据包含未公开字段 {unexpected_fields}")
    return True


def check_public_index_hero_fields(hero, errors, context):
    if not isinstance(hero, dict):
        errors.append(f"{context}: 首页英雄必须是对象")
        return False
    missing_fields = sorted(PUBLIC_INDEX_HERO_FIELDS - set(hero))
    unexpected_fields = sorted(set(hero) - PUBLIC_INDEX_HERO_FIELDS)
    if missing_fields:
        errors.append(f"{context}: 首页英雄缺少公开字段 {missing_fields}")
    if unexpected_fields:
        errors.append(f"{context}: 首页英雄包含未公开字段 {unexpected_fields}")

    for field, label in (("name", "名称"), ("epithet", "称号"), ("search", "搜索词")):
        value = hero.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{context}: 英雄{label}不能为空")
    search = hero.get("search")
    if isinstance(search, str) and any(not term for term in search.split(",")):
        errors.append(f"{context}: 搜索词不能包含空项")

    tier = hero.get("tier")
    if not isinstance(tier, int) or isinstance(tier, bool) or not 1 <= tier <= 5:
        errors.append(f"{context}: 段位必须是 1–5 的整数")
    win_rate = hero.get("winRate")
    if not is_finite_number(win_rate) or not 0 <= win_rate <= 1:
        errors.append(f"{context}: 胜率必须是 0–1 的有限数值")

    availability = hero.get("videoAvailability")
    if not isinstance(availability, list):
        errors.append(f"{context}: 视频可用性必须是数组")
    else:
        for index, record in enumerate(availability, 1):
            record_context = f"{context}/videoAvailability/{index}"
            if not isinstance(record, dict):
                errors.append(f"{record_context}: 视频可用性必须是对象")
                continue
            missing = sorted({"expiresAt", "patchStatus"} - set(record))
            unexpected = sorted(set(record) - {"expiresAt", "patchStatus"})
            if missing:
                errors.append(f"{record_context}: 缺少公开字段 {missing}")
            if unexpected:
                errors.append(f"{record_context}: 包含未公开字段 {unexpected}")
            expires_at = record.get("expiresAt")
            try:
                parsed_date = datetime.date.fromisoformat(expires_at)
            except (TypeError, ValueError):
                parsed_date = None
            if parsed_date is None or parsed_date.isoformat() != expires_at:
                errors.append(f"{record_context}: 到期日期无效 {expires_at!r}")
            if record.get("patchStatus") not in PATCH_STATUSES:
                errors.append(f"{record_context}: 版本状态无效")
    return True


def check_skill_plan(skills, errors, context):
    if not isinstance(skills, dict):
        errors.append(f"{context}: 技能方案必须是对象")
        return
    priority = skills.get("priority")
    if (
        not isinstance(priority, list)
        or len(priority) != 3
        or any(
            not isinstance(letter, str) or letter not in SKILL_LETTERS
            for letter in priority
        )
        or len(set(priority)) != 3
    ):
        errors.append(f"{context}: 技能优先级必须是 3 个不重复的 Q/W/E/R")
    sequence = skills.get("sequence")
    if (
        not isinstance(sequence, list)
        or len(sequence) != 15
        or any(
            not isinstance(letter, str) or letter not in SKILL_LETTERS
            for letter in sequence
        )
    ):
        errors.append(f"{context}: 技能序列必须是 15 个 Q/W/E/R")


def check_augment_rarity(rarity, errors, context):
    if not isinstance(rarity, str) or rarity not in AUGMENT_RARITIES:
        errors.append(f"{context}: 强化稀有度无效")


def check_augment_reference(augment, errors, context, *, recommendation=False):
    if not isinstance(augment, dict):
        errors.append(f"{context}: 强化必须是对象")
        return None, None

    allowed_fields = {
        "id",
        "name",
        "rarity",
        "desc",
        "needsGameCheck",
        "icon",
    }
    if recommendation:
        allowed_fields.update({"hexLabel", "opgg"})
    unexpected_fields = sorted(set(augment) - allowed_fields)
    if unexpected_fields:
        errors.append(f"{context}: 强化包含未公开字段 {unexpected_fields}")

    augment_id = augment.get("id")
    if (
        not isinstance(augment_id, int)
        or isinstance(augment_id, bool)
        or augment_id <= 0
    ):
        errors.append(f"{context}: 强化 ID 必须是正整数")
        augment_id = None

    name = augment.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{context}: 强化名称不能为空")
        name = None
    else:
        name = name.strip()

    check_augment_rarity(augment.get("rarity"), errors, context)
    desc = augment.get("desc")
    if not isinstance(desc, str) or not desc.strip():
        errors.append(f"{context}: 强化描述不能为空")
    elif "?" in desc:
        errors.append(f"{context}: 描述仍有占位符")

    if not isinstance(augment.get("needsGameCheck"), bool):
        errors.append(f"{context}: 游戏内核对标记必须是布尔值")

    icon = augment.get("icon")
    if not isinstance(icon, str) or not icon.strip():
        errors.append(f"{context}: 强化图片不能为空")
    else:
        check_asset(icon, errors, context)

    if recommendation:
        if not isinstance(augment.get("opgg"), bool):
            errors.append(f"{context}: OP.GG 推荐标记必须是布尔值")
        label = augment.get("hexLabel")
        if label is not None and (
            not isinstance(label, str) or not label.strip()
        ):
            errors.append(f"{context}: 推荐度标签不能为空")

    return augment_id, name


def check_augment_sections(augments, combos, errors, context):
    if not isinstance(augments, list) or not augments:
        errors.append(f"{context}/augments: 强化推荐必须是非空数组")
    else:
        augment_ids = []
        augment_names = []
        for index, augment in enumerate(augments, 1):
            augment_id, name = check_augment_reference(
                augment,
                errors,
                f"{context}/augments/{index}",
                recommendation=True,
            )
            if augment_id is not None:
                augment_ids.append(augment_id)
            if name is not None:
                augment_names.append(name)
        if len(augment_ids) != len(set(augment_ids)):
            errors.append(f"{context}/augments: 强化推荐存在重复 ID")
        if len(augment_names) != len(set(augment_names)):
            errors.append(f"{context}/augments: 强化推荐存在重复名称")

    if not isinstance(combos, list) or not 1 <= len(combos) <= 6:
        errors.append(f"{context}/combos: 三强化组合必须是 1–6 行")
        return

    combo_keys = []
    for combo_index, combo in enumerate(combos, 1):
        combo_context = f"{context}/combos/{combo_index}"
        if not isinstance(combo, dict):
            errors.append(f"{combo_context}: 三强化组合必须是对象")
            continue
        unexpected_fields = sorted(set(combo) - {"augments", "games"})
        if unexpected_fields:
            errors.append(
                f"{combo_context}: 三强化组合包含未公开字段 "
                f"{unexpected_fields}"
            )
        for forbidden_field in ("tier", "winRate", "winRateTier"):
            if forbidden_field in combo:
                errors.append(
                    f"{combo_context}: 不应公开由胜率派生的组合字段 "
                    f"{forbidden_field}"
                )

        rows = combo.get("augments")
        if not isinstance(rows, list) or len(rows) != 3:
            errors.append(f"{combo_context}: 三强化组合必须恰好包含 3 项")
            continue

        combo_ids = []
        combo_names = []
        for augment_index, augment in enumerate(rows, 1):
            augment_id, name = check_augment_reference(
                augment,
                errors,
                f"{combo_context}/augments/{augment_index}",
            )
            if augment_id is not None:
                combo_ids.append(augment_id)
            if name is not None:
                combo_names.append(name)
        if len(combo_ids) != len(set(combo_ids)):
            errors.append(f"{combo_context}: 组合内强化 ID 不得重复")
        if len(combo_names) != len(set(combo_names)):
            errors.append(f"{combo_context}: 组合内强化名称不得重复")
        if len(combo_ids) == 3:
            combo_keys.append(tuple(sorted(combo_ids)))

        games = combo.get("games")
        if (
            not isinstance(games, int)
            or isinstance(games, bool)
            or games <= 0
        ):
            errors.append(f"{combo_context}: 样本场次必须是正整数")

    if len(combo_keys) != len(set(combo_keys)):
        errors.append(f"{context}/combos: 存在重复的三强化组合")


def check_item_reference(item, errors, context):
    if not isinstance(item, dict):
        errors.append(f"{context}: 装备必须是对象")
        return None
    unexpected_fields = sorted(set(item) - {"id", "name", "icon"})
    if unexpected_fields:
        errors.append(f"{context}: 装备包含未公开字段 {unexpected_fields}")
    item_id = item.get("id")
    if (
        not isinstance(item_id, int)
        or isinstance(item_id, bool)
        or item_id <= 0
    ):
        errors.append(f"{context}: 装备 ID 必须是正整数")
        item_id = None
    if not isinstance(item.get("name"), str) or not item["name"].strip():
        errors.append(f"{context}: 装备名称不能为空")
    check_asset(item.get("icon"), errors, context)
    return item_id


def check_item_sections(items, errors, context):
    if not isinstance(items, dict):
        errors.append(f"{context}: 出装数据必须是对象")
        return

    for group in ("starter", "core", "boots"):
        rows = items.get(group)
        if not isinstance(rows, list):
            errors.append(f"{context}/{group}: 装备分组必须是数组")
            continue
        if group in {"starter", "boots"} and not rows:
            errors.append(f"{context}/{group}: 装备分组不能为空")
        ids = [
            check_item_reference(item, errors, f"{context}/{group}")
            for item in rows
        ]
        valid_ids = [item_id for item_id in ids if item_id is not None]
        if len(valid_ids) != len(set(valid_ids)):
            errors.append(f"{context}/{group}: 装备分组存在重复装备")

    core_rows = items.get("opggCores")
    if not isinstance(core_rows, list) or not 1 <= len(core_rows) <= 3:
        errors.append(f"{context}/opggCores: 核心三件套必须是 1–3 行")
    else:
        if len(valid_item_core_rows(core_rows)) != len(core_rows):
            errors.append(
                f"{context}/opggCores: 每行必须由 3 件不同合法装备组成且组合不重复"
            )
        for row_index, row in enumerate(core_rows, 1):
            if not isinstance(row, list):
                continue
            for item in row:
                check_item_reference(
                    item,
                    errors,
                    f"{context}/opggCores/{row_index}",
                )

    ranked_rows = items.get("hexTop")
    if not isinstance(ranked_rows, list) or not ranked_rows:
        errors.append(f"{context}/hexTop: 单件强度榜必须是非空数组")
        return
    ranked_ids = []
    for row_index, ranked in enumerate(ranked_rows, 1):
        row_context = f"{context}/hexTop/{row_index}"
        if not isinstance(ranked, dict):
            errors.append(f"{row_context}: 单件榜记录必须是对象")
            continue
        unexpected_fields = sorted(set(ranked) - {"item", "hexLabel"})
        if unexpected_fields:
            errors.append(
                f"{row_context}: 单件榜记录包含未公开字段 "
                f"{unexpected_fields}"
            )
        item_id = check_item_reference(ranked.get("item"), errors, row_context)
        if item_id is not None:
            ranked_ids.append(item_id)
        if (
            not isinstance(ranked.get("hexLabel"), str)
            or not ranked["hexLabel"].strip()
        ):
            errors.append(f"{row_context}: 推荐度标签不能为空")
    if len(ranked_ids) != len(set(ranked_ids)):
        errors.append(f"{context}/hexTop: 单件强度榜存在重复装备")


def check_named_asset_reference(value, errors, context, label):
    if not isinstance(value, dict):
        errors.append(f"{context}: {label}必须是对象")
        return None

    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{context}: {label}名称不能为空")
        name = None

    icon = value.get("icon")
    if not isinstance(icon, str) or not icon.strip():
        errors.append(f"{context}: {label}图标不能为空")
    else:
        check_asset(icon, errors, context)
    return name


def check_rune_sections(runes, errors, context):
    if not isinstance(runes, dict):
        errors.append(f"{context}: 符文数据必须是对象")
        return

    source = runes.get("source")
    if not isinstance(source, str) or not source.strip():
        errors.append(f"{context}: 符文来源不能为空")

    pages = runes.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 2:
        errors.append(f"{context}: 符文页必须是 1–2 套")
        return

    for page_index, page in enumerate(pages, 1):
        page_context = f"{context}/runes/{page_index}"
        if not isinstance(page, dict):
            errors.append(f"{page_context}: 符文页必须是对象")
            continue

        primary_style = check_named_asset_reference(
            page.get("primaryStyle"),
            errors,
            page_context,
            "主系",
        )
        sub_style = check_named_asset_reference(
            page.get("subStyle"),
            errors,
            page_context,
            "副系",
        )
        if (
            primary_style is not None
            and sub_style is not None
            and primary_style == sub_style
        ):
            errors.append(f"{page_context}: 主系与副系不能相同")

        rune_names = []
        for field, label, expected_length in (
            ("primary", "主系符文", 4),
            ("sub", "副系符文", 2),
        ):
            rows = page.get(field)
            if not isinstance(rows, list) or len(rows) != expected_length:
                errors.append(
                    f"{page_context}: {label}必须恰好有 {expected_length} 项"
                )
                continue
            for rune_index, rune in enumerate(rows, 1):
                name = check_named_asset_reference(
                    rune,
                    errors,
                    f"{page_context}/{field}/{rune_index}",
                    "符文",
                )
                if name is not None:
                    rune_names.append(name)
        if len(rune_names) != len(set(rune_names)):
            errors.append(f"{page_context}: 同一符文页存在重复符文")

        shards = page.get("shards")
        if (
            not isinstance(shards, list)
            or len(shards) != 3
            or any(not isinstance(shard, str) or not shard.strip() for shard in shards)
        ):
            errors.append(f"{page_context}: 属性碎片必须恰好有 3 个非空名称")

        pick_rate = page.get("pickRate")
        if not is_finite_number(pick_rate) or not 0 <= pick_rate <= 1:
            errors.append(f"{page_context}: 符文采用率必须是 0–1 的有限数值")


def check_spell_sections(spells, errors, context):
    if not isinstance(spells, list) or len(spells) != 2:
        errors.append(f"{context}: 召唤师技能必须恰好有主流、备选 2 组")
        return

    combinations = []
    for pair_index, pair in enumerate(spells, 1):
        pair_context = f"{context}/spells/{pair_index}"
        if not isinstance(pair, list) or len(pair) != 2:
            errors.append(f"{pair_context}: 每组召唤师技能必须恰好有 2 项")
            continue

        names = []
        for spell_index, spell in enumerate(pair, 1):
            spell_context = f"{pair_context}/{spell_index}"
            name = check_named_asset_reference(
                spell,
                errors,
                spell_context,
                "召唤师技能",
            )
            if name is None:
                continue
            names.append(name)
            expected_icon = SUMMONER_SPELL_ICONS.get(name)
            if expected_icon is None:
                errors.append(f"{spell_context}: 召唤师技能不在 ARAM/KIWI 允许列表")
            elif spell.get("icon") != expected_icon:
                errors.append(f"{spell_context}: 召唤师技能名称与图标不匹配")

        if len(names) != len(set(names)):
            errors.append(f"{pair_context}: 同组不能重复召唤师技能")
        if len(names) == 2:
            combinations.append(tuple(sorted(names)))

    if len(combinations) != len(set(combinations)):
        errors.append(f"{context}: 主流与备选召唤师技能组合不能重复")


def timestamp_seconds(value):
    parts = str(value or "").strip().split(":")
    if (
        len(parts) not in {2, 3}
        or any(not re.fullmatch(r"\d{1,2}", part) for part in parts)
    ):
        return None
    values = [int(part) for part in parts]
    if values[-1] >= 60 or (len(values) == 3 and values[-2] >= 60):
        return None
    if len(values) == 2:
        return values[0] * 60 + values[1]
    return values[0] * 3600 + values[1] * 60 + values[2]


def main():
    errors = []
    png_count = check_site_png_assets(errors)
    index = load(os.path.join(DATA, "index.json"))
    if not check_public_index_fields(index, errors):
        print("\n".join(f"ERROR {error}" for error in errors))
        raise SystemExit(f"数据验证失败,共 {len(errors)} 项")
    index_patch = index.get("patch")
    check_public_patch_fields(index_patch, errors, "index/patch")
    with open(os.path.join(SITE, "assets", "hero.js"), encoding="utf-8") as handle:
        hero_script = handle.read()
    with open(os.path.join(SITE, "assets", "index.js"), encoding="utf-8") as handle:
        index_script = handle.read()
    with open(os.path.join(SITE, "assets", "common.js"), encoding="utf-8") as handle:
        common_script = handle.read()
    for required_marker in (
        "stats-provenance",
        "h.patch.game",
        "h.patch.opggGame",
        "h.patch.statsGame",
        "h.patch.hexdataDate",
        "escapeHTML(runes.source)",
    ):
        if required_marker not in hero_script:
            errors.append(f"英雄页未展示来源字段: {required_marker}")
    for forbidden_marker in ("v.confidence", "可信度"):
        if forbidden_marker in hero_script:
            errors.append(
                f"英雄页不应公开未校准的模型置信度: {forbidden_marker}"
            )
    if "calendarAgeLabel" not in common_script:
        errors.append("站点未展示统计快照距今天数")
    for script_name, script in (
        ("首页", index_script),
        ("英雄页", hero_script),
    ):
        if "当前版本" in script:
            errors.append(f"{script_name}不应把静态补丁快照称为当前版本")

    heroes = index.get("heroes") or []
    aliases = [
        hero.get("alias") if isinstance(hero, dict) else None
        for hero in heroes
    ]
    valid_aliases = [alias for alias in aliases if is_valid_hero_alias(alias)]
    if len(heroes) < 170:
        errors.append(f"英雄数量异常: {len(heroes)}")
    if len(valid_aliases) != len(aliases):
        for alias in aliases:
            if not is_valid_hero_alias(alias):
                errors.append(f"首页英雄 alias 无效: {alias!r}")
    if len(valid_aliases) != len(set(valid_aliases)):
        errors.append("首页存在重复 alias")
    check_index_search_terms(heroes, errors)

    detail_dir = os.path.join(DATA, "heroes")
    detail_files = {name for name in os.listdir(detail_dir) if name.endswith(".json")}
    expected = {f"{alias}.json" for alias in valid_aliases}
    if detail_files != expected:
        missing = sorted(expected - detail_files)
        extra = sorted(detail_files - expected)
        errors.append(f"详情文件不匹配 missing={missing[:5]} extra={extra[:5]}")

    if os.path.exists(VIDEO_CATALOG):
        catalog = load(VIDEO_CATALOG)
        video_ids = []
        for video in catalog.get("videos", []):
            video_ids.append(video.get("id"))
            status = video.get("analysisStatus")
            if status not in VIDEO_STATUSES:
                errors.append(f"{video.get('id')}: 视频核对状态无效")
            parsed_url = urllib.parse.urlparse(str(video.get("url", "")))
            if parsed_url.scheme != "https" or parsed_url.hostname not in {"www.douyin.com", "v.douyin.com"}:
                errors.append(f"{video.get('id')}: 视频来源链接无效")
            if video.get("patchStatus") not in PATCH_STATUSES:
                errors.append(f"{video.get('id')}: 视频版本状态无效")
            patch_impact = video.get("patchImpact")
            if patch_impact is not None:
                if not isinstance(patch_impact, dict):
                    errors.append(f"{video.get('id')}: 补丁影响版本无效")
                    patch_impact = {}
                impact_source = urllib.parse.urlparse(
                    str(patch_impact.get("source") or "")
                )
                if patch_impact.get("patch") != index.get("patch", {}).get("game"):
                    errors.append(f"{video.get('id')}: 补丁影响版本无效")
                if patch_impact.get("status") not in PATCH_IMPACT_STATUSES:
                    errors.append(f"{video.get('id')}: 补丁影响状态无效")
                if (
                    impact_source.scheme != "https"
                    or impact_source.hostname != "www.leagueoflegends.com"
                ):
                    errors.append(f"{video.get('id')}: 补丁影响来源无效")
                if not str(patch_impact.get("summary") or "").strip():
                    errors.append(f"{video.get('id')}: 补丁影响缺少摘要")
                for date_field in ("effectiveAt", "sourcePublishedAt"):
                    if not re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}",
                        str(patch_impact.get(date_field) or ""),
                    ):
                        errors.append(
                            f"{video.get('id')}: 补丁影响 {date_field} 无效"
                        )
                if str(video.get("publishedAt") or "") >= str(
                    patch_impact.get("effectiveAt") or ""
                ):
                    errors.append(f"{video.get('id')}: 补丁影响不适用于此发布日期")
            published_at = str(video.get("publishedAt") or "")
            expires_at = str(video.get("expiresAt") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires_at):
                errors.append(f"{video.get('id')}: 视频到期日期无效")
            elif expires_at < published_at:
                errors.append(f"{video.get('id')}: 视频到期日期早于发布日期")
            if status != "metadata-only":
                if not video.get("summary") or not video.get("reviewedAt"):
                    errors.append(f"{video.get('id')}: 已核对视频缺少摘要或核对日期")
                if (
                    not video.get("keyPoints")
                    and not video.get("strategy")
                    and not video.get("strategies")
                ):
                    errors.append(f"{video.get('id')}: 已核对视频缺少攻略字段")
                strategies = video.get("strategies")
                if not isinstance(strategies, list) or not strategies:
                    strategies = [video.get("strategy") or {}]
                strategy_ids = []
                strategy_labels = []
                for strategy in strategies:
                    if not isinstance(strategy, dict):
                        errors.append(f"{video.get('id')}: 流派结构无效")
                        continue
                    strategy_ids.append(str(strategy.get("id") or ""))
                    strategy_labels.append(str(strategy.get("label") or ""))
                    for group in ("augments", "items", "runes"):
                        for strategy_row in strategy.get(group, []):
                            name = (
                                strategy_row
                                if isinstance(strategy_row, str)
                                else (strategy_row or {}).get("name")
                            )
                            if not str(name or "").strip():
                                errors.append(
                                    f"{video.get('id')}: {group} 存在无法渲染的名称"
                                )
                if len(strategies) > 1 and (
                    any(not value for value in strategy_ids)
                    or len(strategy_ids) != len(set(strategy_ids))
                ):
                    errors.append(f"{video.get('id')}: 多流派 id 缺失或重复")
                if len(strategies) > 1 and (
                    any(not value for value in strategy_labels)
                    or len(strategy_labels) != len(set(strategy_labels))
                ):
                    errors.append(f"{video.get('id')}: 多流派标签缺失或重复")
            if status == "multimodal-reviewed":
                try:
                    duration_seconds = int(video.get("durationSeconds"))
                except (TypeError, ValueError):
                    duration_seconds = 0
                if duration_seconds <= 0:
                    errors.append(f"{video.get('id')}: 多模态视频时长无效")
                try:
                    confidence = float(video.get("confidence"))
                except (TypeError, ValueError):
                    confidence = -1
                if not 0 <= confidence <= 1:
                    errors.append(f"{video.get('id')}: 多模态视频 confidence 无效")
                evidence = video.get("evidence") or []
                expected_label = evidence_coverage_label(evidence)
                if video.get("analysisLabel") != expected_label:
                    errors.append(
                        f"{video.get('id')}: 证据标签与公开证据类型不一致 "
                        f"expected={expected_label!r}"
                    )
                if len(evidence) < 2:
                    errors.append(f"{video.get('id')}: 多模态视频至少需要两条证据")
                if not any(row.get("kind") == "frame" for row in evidence if isinstance(row, dict)):
                    errors.append(f"{video.get('id')}: 多模态视频缺少画面证据")
                for evidence_row in evidence:
                    timestamp = str((evidence_row or {}).get("timestamp") or "")
                    evidence_seconds = timestamp_seconds(timestamp)
                    if evidence_seconds is None:
                        errors.append(f"{video.get('id')}: 证据时间戳无效 {timestamp!r}")
                    elif (
                        duration_seconds > 0
                        and evidence_seconds >= duration_seconds
                    ):
                        errors.append(
                            f"{video.get('id')}: 证据时间戳超出视频时长 "
                            f"{timestamp} >= {duration_seconds}s"
                        )
            for video_alias in video.get("heroes", []):
                if video_alias not in aliases:
                    errors.append(f"{video.get('id')}: 未知英雄 alias {video_alias}")
        if len(video_ids) != len(set(video_ids)):
            errors.append("视频目录存在重复 id")

    for row in heroes:
        if not isinstance(row, dict):
            errors.append("首页英雄必须是对象")
            continue
        alias = row.get("alias")
        check_public_index_hero_fields(row, errors, f"index/{alias}")
        if not is_valid_hero_alias(alias):
            continue
        check_asset(row.get("icon"), errors, f"index/{alias}")
        check_hero_roles(row.get("roles"), errors, f"index/{alias}")
        detail = load(os.path.join(detail_dir, f"{alias}.json"))
        if not check_public_hero_detail_fields(detail, errors, alias):
            continue
        if (
            detail.get("name") != row.get("name")
            or detail.get("icon") != row.get("icon")
        ):
            errors.append(f"{alias}: 索引与详情身份不一致")
        detail_roles = detail.get("roles")
        check_hero_roles(detail_roles, errors, alias)
        if detail_roles != row.get("roles"):
            errors.append(f"{alias}: 首页与详情定位不一致")
        detail_stats = detail.get("stats")
        check_hero_stats(detail_stats, errors, alias)
        if isinstance(detail_stats, dict) and any(
            detail_stats.get(field) != row.get(field)
            for field in ("tier", "winRate")
        ):
            errors.append(f"{alias}: 首页与详情统计不一致")
        check_douyin_search_url(detail.get("douyinUrl"), errors, alias)
        detail_videos = detail.get("videos") or []
        expected_availability = [
            {
                "expiresAt": video.get("expiresAt"),
                "patchStatus": video.get("patchStatus"),
            }
            for video in detail_videos
        ]
        if row.get("videoAvailability") != expected_availability:
            errors.append(f"{alias}: 首页视频运行时状态与详情不一致")
        for detail_video in detail_videos:
            unexpected_fields = sorted(set(detail_video) - PUBLIC_VIDEO_FIELDS)
            if unexpected_fields:
                errors.append(
                    f"{alias}/{detail_video.get('id')}: "
                    f"站点数据包含未公开字段 {unexpected_fields}"
                )
            caveat = str(detail_video.get("caveat") or "")
            if re.search(r"适用于当前 \d{2}\.\d{1,2} 版本|当前(?:治疗)?强度", caveat):
                errors.append(
                    f"{alias}/{detail_video.get('id')}: "
                    "公开视频提示不应把静态补丁快照称为当前状态"
                )
            if not video_is_currently_publishable(detail_video):
                errors.append(
                    f"{alias}/{detail_video.get('id')}: 过期或搭配失效的视频不应进入站点数据"
                )
        detail_patch = detail.get("patch")
        check_public_patch_fields(detail_patch, errors, f"{alias}/patch")
        if detail_patch != index_patch:
            errors.append(f"{alias}: 详情来源快照与首页不一致")
        if (
            isinstance(detail_patch, dict)
            and isinstance(detail.get("runes"), dict)
            and str(detail_patch.get("opggRunes") or "")
            not in str(detail["runes"].get("source") or "")
        ):
            errors.append(f"{alias}: 符文标题来源未展示其 OP.GG 版本")
        check_asset(detail.get("icon"), errors, alias)
        check_skill_plan(detail.get("skills"), errors, alias)

        check_augment_sections(
            detail.get("augments"),
            detail.get("combos"),
            errors,
            alias,
        )
        check_item_sections(detail.get("items"), errors, alias)
        check_rune_sections(detail.get("runes"), errors, alias)
        check_spell_sections(detail.get("spells"), errors, alias)

    if errors:
        print("\n".join(f"ERROR {error}" for error in errors[:100]))
        raise SystemExit(f"数据验证失败,共 {len(errors)} 项")
    print(
        f"数据验证通过: {len(heroes)} 位英雄、{len(detail_files)} 个详情文件、"
        f"{png_count} 个站点 PNG 可解码且全部本地图片引用有效"
    )


if __name__ == "__main__":
    main()
