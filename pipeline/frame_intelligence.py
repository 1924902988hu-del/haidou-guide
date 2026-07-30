#!/usr/bin/env python3
"""关键界面抽帧、macOS Vision OCR 与保守图标候选匹配。

本模块只生成可复核的本地证据清单。OCR/图标候选不能单独越过视频质量闸门。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


ROOT = Path(__file__).resolve().parents[1]
VISION_SOURCE = ROOT / "pipeline" / "macos_vision_ocr.swift"
VISION_BINARY = ROOT / "data" / "cache" / "video_intelligence" / "bin" / "macos-vision-ocr"
CRITICAL_UI_TERMS = (
    "海克斯强化",
    "强化推荐",
    "推荐强化",
    "装备推荐",
    "推荐出装",
    "核心装备",
    "最终出装",
    "符文推荐",
    "推荐符文",
    "技能加点",
    "主升",
    "副升",
    "流派",
)


class FrameIntelligenceError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def video_duration_seconds(video_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise FrameIntelligenceError("缺少 ffprobe，无法确定视频时长")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as error:
        raise FrameIntelligenceError("ffprobe 未返回有效视频时长") from error
    if not math.isfinite(duration) or duration <= 0:
        raise FrameIntelligenceError("视频时长无效")
    return duration


def extract_sequence(
    video_path: Path,
    output_dir: Path,
    *,
    prefix: str,
    start: float,
    duration: float,
    fps: float,
    stage: str,
) -> list[dict[str, Any]]:
    """用单次 ffmpeg 调用抽取等间隔序列，避免每帧启动一个进程。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FrameIntelligenceError("缺少 ffmpeg，无法抽取关键帧")
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / f"{prefix}-%04d.jpg"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{max(0.05, duration):.3f}",
            "-vf",
            f"fps={fps:.6f}",
            "-q:v",
            "2",
            "-start_number",
            "0",
            "-y",
            str(pattern),
        ],
        check=True,
    )
    rows = []
    for index, path in enumerate(sorted(output_dir.glob(f"{prefix}-*.jpg"))):
        rows.append({
            "path": path,
            "timestampSeconds": round(start + index / fps, 3),
            "stage": stage,
        })
    return rows


def ensure_vision_binary() -> Path:
    if platform.system() != "Darwin":
        raise FrameIntelligenceError("macOS Vision OCR 只可在 macOS 运行")
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise FrameIntelligenceError("缺少 swiftc，无法构建 Vision OCR 助手")
    if (
        VISION_BINARY.exists()
        and VISION_BINARY.stat().st_mtime >= VISION_SOURCE.stat().st_mtime
    ):
        return VISION_BINARY
    VISION_BINARY.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [swiftc, str(VISION_SOURCE), "-O", "-o", str(VISION_BINARY)],
        check=True,
        capture_output=True,
        text=True,
    )
    return VISION_BINARY


def vision_ocr(frame_paths: list[Path]) -> list[dict[str, Any]]:
    if not frame_paths:
        return []
    binary = ensure_vision_binary()
    result = subprocess.run(
        [str(binary)],
        input=json.dumps([str(path.resolve()) for path in frame_paths]),
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, list):
        raise FrameIntelligenceError("Vision OCR 返回结构无效")
    return payload


def known_names() -> dict[str, set[str]]:
    augments = {
        str(row.get("name") or "").strip()
        for row in load_json(ROOT / "data" / "raw" / "hexdata" / "augments.json")
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }
    items = {
        str(row.get("name") or "").strip()
        for row in (
            load_json(ROOT / "data" / "raw" / "ddragon" / "item.json")
            .get("data", {})
            .values()
        )
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }
    runes_payload = load_json(
        ROOT / "data" / "raw" / "ddragon" / "runesReforged.json"
    )
    runes = set()
    for style in runes_payload:
        if not isinstance(style, dict):
            continue
        if style.get("name"):
            runes.add(str(style["name"]).strip())
        for slot in style.get("slots") or []:
            for rune in (slot or {}).get("runes") or []:
                if (rune or {}).get("name"):
                    runes.add(str(rune["name"]).strip())
    return {"augments": augments, "items": items, "runes": runes}


def text_matches(text: str, vocabulary: dict[str, set[str]]) -> dict[str, list[str]]:
    compact = re.sub(r"\s+", "", text)
    matches = {}
    for group, names in vocabulary.items():
        candidates = sorted(
            (
            name
            for name in names
            if len(name) >= 2
            and re.sub(r"\s+", "", name) in compact
            ),
            key=lambda name: (-len(name), name),
        )
        kept = []
        for name in candidates:
            compact_name = re.sub(r"\s+", "", name)
            if any(compact_name in re.sub(r"\s+", "", longer) for longer in kept):
                continue
            kept.append(name)
        matches[group] = sorted(kept)
    return matches


def dhash(image: Image.Image) -> int:
    grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(grayscale.get_flattened_data())
    value = 0
    for row in range(8):
        for column in range(8):
            value <<= 1
            value |= pixels[row * 9 + column] > pixels[row * 9 + column + 1]
    return value


def mean_rgb(image: Image.Image) -> tuple[float, float, float]:
    return tuple(
        float(value)
        for value in ImageStat.Stat(image.convert("RGB").resize((16, 16))).mean
    )


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def color_distance(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def icon_references() -> list[dict[str, Any]]:
    item_payload = (
        load_json(ROOT / "data" / "raw" / "ddragon" / "item.json")
        .get("data", {})
    )
    augment_payload = load_json(
        ROOT / "data" / "raw" / "hexdata" / "augments.json"
    )
    names = {
        ("items", str(item_id)): str(row.get("name") or "").strip()
        for item_id, row in item_payload.items()
        if isinstance(row, dict)
    }
    names.update({
        ("augments", str(row.get("id") or "")): str(row.get("name") or "").strip()
        for row in augment_payload
        if isinstance(row, dict)
    })
    rows = []
    for group, directory in (
        ("items", ROOT / "site" / "assets" / "img" / "item"),
        ("augments", ROOT / "site" / "assets" / "img" / "augment"),
    ):
        for path in directory.glob("*.png"):
            name = names.get((group, path.stem), "")
            if not name:
                continue
            try:
                with Image.open(path) as image:
                    normalized_image = image.convert("RGBA")
                    rows.append({
                        "group": group,
                        "id": path.stem,
                        "name": name,
                        "hash": dhash(normalized_image),
                        "mean": mean_rgb(normalized_image),
                    })
            except OSError:
                continue
    return rows


def crop_vision_rectangle(
    image: Image.Image,
    bbox: list[float],
) -> Image.Image | None:
    if len(bbox) != 4:
        return None
    x, y, width, height = bbox
    if width <= 0 or height <= 0:
        return None
    left = max(0, int(x * image.width))
    right = min(image.width, int((x + width) * image.width))
    top = max(0, int((1 - y - height) * image.height))
    bottom = min(image.height, int((1 - y) * image.height))
    if right - left < 18 or bottom - top < 18:
        return None
    side = min(right - left, bottom - top)
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    return image.crop((
        center_x - side // 2,
        center_y - side // 2,
        center_x + side // 2,
        center_y + side // 2,
    ))


def match_icon_crop(
    crop: Image.Image,
    references: list[dict[str, Any]],
) -> dict[str, Any] | None:
    crop_hash = dhash(crop)
    crop_mean = mean_rgb(crop)
    ranked = sorted(
        [
            (
            hamming_distance(crop_hash, reference["hash"]),
            color_distance(crop_mean, reference["mean"]),
            reference,
            )
            for reference in references
        ],
        key=lambda row: (row[0], row[1], row[2]["group"], row[2]["id"]),
    )
    if not ranked:
        return None
    hash_distance, rgb_distance, reference = ranked[0]
    if not (
        hash_distance <= 7
        or (hash_distance <= 10 and rgb_distance <= 55)
    ):
        return None
    return {
        "group": reference["group"],
        "id": reference["id"],
        "name": reference["name"],
        "hashDistance": hash_distance,
        "colorDistance": round(rgb_distance, 2),
        "confidence": round(
            max(
                0.0,
                min(
                    1.0,
                    0.75 * (1 - hash_distance / 16)
                    + 0.25 * (1 - rgb_distance / 220),
                ),
            ),
            3,
        ),
    }


def enrich_frame_rows(
    frame_rows: list[dict[str, Any]],
    ocr_rows: list[dict[str, Any]],
    *,
    match_icons: bool,
) -> list[dict[str, Any]]:
    vocabulary = known_names()
    references = icon_references() if match_icons else []
    ocr_by_path = {str(row.get("path") or ""): row for row in ocr_rows}
    enriched = []
    for frame in frame_rows:
        path = Path(frame["path"]).resolve()
        ocr = ocr_by_path.get(str(path), {})
        texts = [
            row
            for row in ocr.get("texts", [])
            if str(row.get("text") or "").strip()
        ]
        joined_text = "\n".join(str(row["text"]) for row in texts)
        ui_terms = [term for term in CRITICAL_UI_TERMS if term in joined_text]
        vocabulary_matches = text_matches(joined_text, vocabulary)
        icon_matches = []
        if references and ocr.get("rectangles"):
            try:
                with Image.open(path) as image:
                    for rectangle in ocr.get("rectangles", []):
                        crop = crop_vision_rectangle(
                            image,
                            list(rectangle.get("bbox") or []),
                        )
                        if crop is None:
                            continue
                        match = match_icon_crop(crop, references)
                        if match and match not in icon_matches:
                            icon_matches.append(match)
            except OSError:
                pass
        enriched.append({
            "path": relative_path(path),
            "timestampSeconds": frame["timestampSeconds"],
            "stage": frame["stage"],
            "ocrText": joined_text,
            "ocrTerms": ui_terms,
            "vocabularyMatches": vocabulary_matches,
            "rectangleCount": len(ocr.get("rectangles") or []),
            "iconMatches": icon_matches,
            "ocrError": ocr.get("error"),
        })
    return enriched


def deduplicate_frames(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = {}
    stage_priority = {"keyword-dense": 3, "tail-dense": 2, "coarse": 1}
    for row in rows:
        key = round(float(row["timestampSeconds"]) * 2) / 2
        existing = selected.get(key)
        if (
            existing is None
            or stage_priority.get(row["stage"], 0)
            > stage_priority.get(existing["stage"], 0)
        ):
            selected[key] = row
    return [selected[key] for key in sorted(selected)]


def analyze_video_frames(
    video_path: Path,
    output_dir: Path,
    *,
    coarse_interval: float = 3.0,
    dense_interval: float = 0.5,
    dense_window_radius: float = 4.0,
    tail_seconds: float = 24.0,
) -> dict[str, Any]:
    duration = video_duration_seconds(video_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    coarse_rows = extract_sequence(
        video_path,
        output_dir,
        prefix="coarse",
        start=0,
        duration=duration,
        fps=1 / coarse_interval,
        stage="coarse",
    )
    tail_start = max(0.0, duration - tail_seconds)
    tail_rows = extract_sequence(
        video_path,
        output_dir,
        prefix="tail",
        start=tail_start,
        duration=duration - tail_start,
        fps=1 / dense_interval,
        stage="tail-dense",
    )
    initial_rows = deduplicate_frames([*coarse_rows, *tail_rows])
    initial_ocr = vision_ocr([Path(row["path"]) for row in initial_rows])
    initial_enriched = enrich_frame_rows(
        initial_rows,
        initial_ocr,
        match_icons=False,
    )
    raw_hit_times = sorted({
        float(row["timestampSeconds"])
        for row in initial_enriched
        if row["ocrTerms"]
        or any(
            values
            for values in (row.get("vocabularyMatches") or {}).values()
        )
    })
    hit_times = []
    for hit_time in raw_hit_times:
        if (
            not hit_times
            or hit_time - hit_times[-1] >= dense_window_radius * 2
        ):
            hit_times.append(hit_time)
    dense_rows = []
    for index, hit_time in enumerate(hit_times):
        start = max(0.0, hit_time - dense_window_radius)
        end = min(duration, hit_time + dense_window_radius)
        dense_rows.extend(extract_sequence(
            video_path,
            output_dir,
            prefix=f"keyword-{index:03d}",
            start=start,
            duration=end - start,
            fps=1 / dense_interval,
            stage="keyword-dense",
        ))
    final_rows = deduplicate_frames([*initial_rows, *dense_rows])
    final_ocr = vision_ocr([Path(row["path"]) for row in final_rows])
    final_enriched = enrich_frame_rows(
        final_rows,
        final_ocr,
        match_icons=True,
    )
    return {
        "schemaVersion": 1,
        "status": "captured",
        "engine": "ffmpeg+macos-vision+perceptual-icon-match",
        "durationSeconds": round(duration, 3),
        "coarseIntervalSeconds": coarse_interval,
        "denseIntervalSeconds": dense_interval,
        "denseWindowRadiusSeconds": dense_window_radius,
        "tailSeconds": tail_seconds,
        "criticalUiTerms": list(CRITICAL_UI_TERMS),
        "rawKeywordHitTimestamps": raw_hit_times,
        "keywordHitTimestamps": hit_times,
        "frameCount": len(final_enriched),
        "frames": final_enriched,
    }


def prompt_context(manifest: dict[str, Any], max_rows: int = 20) -> str:
    """把预扫描结果作为定位提示，明确禁止模型将候选直接当作最终事实。"""
    meaningful_frames = []
    for frame in manifest.get("frames") or []:
        names = []
        for group, values in (frame.get("vocabularyMatches") or {}).items():
            if values:
                names.append(f"{group}={','.join(values)}")
        icon_names = [
            str(row.get("name") or "")
            for row in frame.get("iconMatches") or []
            if float(row.get("confidence") or 0) >= 0.78
        ]
        if not frame.get("ocrTerms") and not names and not icon_names:
            continue
        meaningful_frames.append((frame, names, icon_names))

    if len(meaningful_frames) > max_rows:
        if max_rows <= 1:
            meaningful_frames = meaningful_frames[:1]
        else:
            indexes = {
                round(index * (len(meaningful_frames) - 1) / (max_rows - 1))
                for index in range(max_rows)
            }
            meaningful_frames = [
                row
                for index, row in enumerate(meaningful_frames)
                if index in indexes
            ]

    rows = []
    for frame, names, icon_names in meaningful_frames:
        rows.append(
            f"- {float(frame.get('timestampSeconds') or 0):.1f}s "
            f"界面词={','.join(frame.get('ocrTerms') or []) or '-'}；"
            f"OCR标准名={';'.join(names) or '-'}；"
            f"图标候选={','.join(icon_names) or '-'}"
        )
    if not rows:
        return ""
    return (
        "\n\n本地关键界面预扫描仅用于提醒你重点核对这些时间点；"
        "OCR/图标候选可能误识别，不能直接当作 evidence，必须回看视频画面、字幕或语音后再写入：\n"
        + "\n".join(rows)
    )


def audit_cached_frames(
    input_root: Path,
    *,
    max_videos: int = 20,
    max_frames_per_video: int = 12,
) -> dict[str, Any]:
    """离线审核已有截图，不下载视频、不触发 TikHub 或模型调用。"""
    priority_ids = []
    summary_path = (
        ROOT
        / "data"
        / "cache"
        / "video_intelligence"
        / "all-heroes-run-summary.json"
    )
    if summary_path.exists():
        priority_ids = [
            str(row.get("videoId") or "")
            for row in load_json(summary_path).get("passed", [])
            if str(row.get("videoId") or "")
        ]
    directories = [
        path
        for path in input_root.iterdir()
        if path.is_dir()
    ]
    directories.sort(
        key=lambda path: (
            path.name not in priority_ids,
            priority_ids.index(path.name) if path.name in priority_ids else path.name,
        )
    )
    selected = directories[:max(1, max_videos)]
    frame_rows = []
    video_by_path = {}
    for directory in selected:
        paths = sorted([
            *directory.glob("*.jpg"),
            *directory.glob("*.png"),
        ])[:max_frames_per_video]
        for path in paths:
            resolved = path.resolve()
            video_by_path[str(resolved)] = directory.name
            frame_rows.append({
                "path": resolved,
                "timestampSeconds": 0,
                "stage": "cached-gold-audit",
            })
    ocr_rows = vision_ocr([Path(row["path"]) for row in frame_rows])
    enriched = enrich_frame_rows(frame_rows, ocr_rows, match_icons=True)
    per_video = {}
    for row in enriched:
        video_id = video_by_path.get(str((ROOT / row["path"]).resolve()))
        if not video_id:
            video_id = video_by_path.get(str(Path(row["path"]).resolve()), "unknown")
        bucket = per_video.setdefault(video_id, {
            "videoId": video_id,
            "frames": 0,
            "ocrFrames": 0,
            "criticalUiFrames": 0,
            "vocabularyFrames": 0,
            "iconMatches": 0,
            "iconCandidates": set(),
            "standardNames": {
                "augments": set(),
                "items": set(),
                "runes": set(),
            },
        })
        bucket["frames"] += 1
        bucket["ocrFrames"] += bool(row.get("ocrText"))
        bucket["criticalUiFrames"] += bool(row.get("ocrTerms"))
        has_vocabulary = any(
            values
            for values in (row.get("vocabularyMatches") or {}).values()
        )
        bucket["vocabularyFrames"] += has_vocabulary
        bucket["iconMatches"] += len(row.get("iconMatches") or [])
        bucket["iconCandidates"].update(
            str(match.get("name") or "")
            for match in row.get("iconMatches") or []
            if str(match.get("name") or "")
        )
        for group, values in (row.get("vocabularyMatches") or {}).items():
            bucket["standardNames"].setdefault(group, set()).update(values)
    serializable_videos = []
    for bucket in per_video.values():
        bucket["standardNames"] = {
            group: sorted(values)
            for group, values in bucket["standardNames"].items()
        }
        bucket["iconCandidates"] = sorted(bucket["iconCandidates"])
        serializable_videos.append(bucket)
    serializable_videos.sort(
        key=lambda row: (
            row["videoId"] not in priority_ids,
            priority_ids.index(row["videoId"])
            if row["videoId"] in priority_ids
            else row["videoId"],
        )
    )
    return {
        "schemaVersion": 1,
        "mode": "offline-cached-frame-audit",
        "externalCalls": {
            "TikHub": 0,
            "model": 0,
        },
        "videos": len(serializable_videos),
        "frames": len(enriched),
        "ocrFrames": sum(row["ocrFrames"] for row in serializable_videos),
        "criticalUiFrames": sum(
            row["criticalUiFrames"] for row in serializable_videos
        ),
        "vocabularyFrames": sum(
            row["vocabularyFrames"] for row in serializable_videos
        ),
        "iconMatches": sum(row["iconMatches"] for row in serializable_videos),
        "priorityPassedVideoIds": priority_ids,
        "results": serializable_videos,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    audit = commands.add_parser(
        "audit-cache",
        help="对已有 BiliNote 截图做 0 付费调用的 OCR/图标离线审核",
    )
    audit.add_argument(
        "--input",
        type=Path,
        default=ROOT / "data" / "cache" / "video_intelligence" / "frames",
    )
    audit.add_argument("--max-videos", type=int, default=20)
    audit.add_argument("--max-frames-per-video", type=int, default=12)
    audit.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / "data"
            / "cache"
            / "video_intelligence"
            / "frame-intelligence-audit.json"
        ),
    )
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "audit-cache":
        report = audit_cached_frames(
            args.input,
            max_videos=args.max_videos,
            max_frames_per_video=args.max_frames_per_video,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"离线视觉审核完成: {report['videos']} 视频 / {report['frames']} 帧；"
            f"OCR 命中 {report['ocrFrames']} 帧，标准名命中 "
            f"{report['vocabularyFrames']} 帧，图标候选 {report['iconMatches']} 个；"
            f"报告 {args.output}"
        )


if __name__ == "__main__":
    main()
