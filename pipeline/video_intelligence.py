#!/usr/bin/env python3
"""抖音攻略视频发现、BiliNote 分析与审核发布工具。

依赖边界：
- TikHub 只负责搜索公开抖音视频，不负责总结。
- BiliNote 必须启用 video_understanding，负责实际读取画面、字幕与语音。
- 本脚本只把通过证据闸门的结果写入 data/videos/catalog.json。

密钥、Cookie、临时视频与签名播放地址都不会写入仓库。
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import hashlib
import http.client
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # 允许 `python3 pipeline/video_intelligence.py ...` 直接运行时导入 pipeline 子模块。
    sys.path.insert(0, str(ROOT))
INDEX_PATH = ROOT / "site" / "data" / "index.json"
CATALOG_PATH = ROOT / "data" / "videos" / "catalog.json"
PATCH_IMPACTS_PATH = ROOT / "data" / "videos" / "patch_impacts.json"
FRAME_REVIEWS_PATH = ROOT / "data" / "videos" / "frame_reviews.json"
EVIDENCE_REVIEWS_PATH = ROOT / "data" / "videos" / "evidence_reviews.json"
CACHE_DIR = ROOT / "data" / "cache" / "video_intelligence"
TIKHUB_SEARCH_PATH = "/api/v1/douyin/search/fetch_multi_search"
TIKHUB_DETAIL_PATHS = (
    "/api/v1/douyin/web/fetch_one_video",
    "/api/v1/douyin/web/fetch_one_video_v2",
)
TIKHUB_PRICING_URL = "https://tikhub.io/pricing"
TIKHUB_PUBLIC_PRICE_RANGE_USD = (0.001, 0.01)
MAX_VIDEO_BYTES = 256 * 1024 * 1024
ANALYSIS_CONTRACT_VERSION = 5
DEFAULT_RECENT_DAYS = 45
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
)
SCREENSHOT_MARKER_RE = re.compile(
    r"\*?Screenshot-(?:\[(\d{2}):(\d{2})\]|(\d{2}):(\d{2}))"
)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
SCREENSHOT_FILENAME_RE = re.compile(
    r"screenshot_\d{3}_[0-9A-Za-z-]+\.jpg"
)
LOCAL_KEY_FRAME_TOLERANCE_SECONDS = 0.51


def load_local_env(path: Path) -> None:
    """读取项目本地 .env，已有进程环境变量优先且永不被覆盖。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            os.environ.setdefault(key, value)


load_local_env(ROOT / ".env")

ALLOWED_STATUSES = {
    "metadata-only",
    "multimodal-reviewed",
    "visual-reviewed",  # 兼容首条人工抽帧记录
    "human-verified",
}
STRATEGY_KEYS = ("augments", "items", "runes", "skillOrder", "playstyle")
EVIDENCE_STRATEGY_KEYS = (
    "augments",
    "items",
    "runes",
    "skillOrder",
    "summonerSpells",
)
GUIDE_TERMS = ("海克斯", "海斗", "大乱斗", "强化", "符文", "出装", "攻略", "玩法")
MODE_TERMS = ("海克斯大乱斗", "海克斯乱斗", "海斗")
TUTORIAL_TERMS = ("攻略", "玩法", "教学", "出装", "推荐", "一图流", "上手", "构建", "思路", "怎么玩")
ENTERTAINMENT_TERMS = ("整活", "集锦", "高光", "五杀", "四杀", "爽局", "乱杀", "精彩操作")
MULTI_BUILD_TERMS = (
    "多种玩法",
    "多种套路",
    "多套玩法",
    "多套方案",
    "三种玩法",
    "三种主要玩法",
    "三个玩法",
    "不同玩法",
)
UNSUPPORTED_TEXT_TERMS = (
    "强制掉线",
    "豪宫深渊",
    "年华战桥",
)
MAYHEM_SPELL_MODES = {"ARAM", "KIWI", "KIWI_JADE"}
ITEM_EVIDENCE_TERMS = ("装备", "购买", "出装", "装备栏", "做出", "合成")
SAFE_NAME_ALIASES = {
    "augments": {
        "芽仙子": "牙仙子",
        "物法接修": "物法皆修",
        "秘术充拳": "秘术冲拳",
    },
    "items": {
        "卢安娜的巨风": "卢安娜的飓风",
        "斯塔提克电刃": "斯塔缇克电刃",
        "毁灭死帽": "灭世者的死亡之帽",
        "青龙刀": "朔极之矛",
        "冰心": "冰霜之心",
        "反甲": "荆棘之甲",
        "沙漏": "中娅沙漏",
        "大面具": "兰德里的折磨",
        "大帽子": "灭世者的死亡之帽",
        "冰杖": "瑞莱的冰晶节杖",
        "法穿棒": "虚空之杖",
        "借弓": "界弓",
    },
}


class VideoPipelineError(RuntimeError):
    """可向操作者直接展示的流程错误。"""


def safe_error_text(value: Any) -> str:
    """第三方错误偶尔回显请求头；所有可见错误必须先移除凭证。"""
    text = str(value or "")
    secrets = {
        os.getenv("TIKHUB_TOKEN", ""),
        os.getenv("OPENAI_API_KEY", ""),
    }
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(
        r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)[^\"'\s,}]+",
        r"\1[REDACTED]",
        text,
    )


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def discovery_run_metadata(
    *,
    started_at: str,
    completed_at: str | None,
    resume: bool,
    checkpoint_requests_before: int,
    checkpoint_requests_after: int,
) -> dict[str, Any]:
    """把本轮增量与断点累计分开，避免恢复运行重复报告历史费用。"""
    requests_added = max(
        0,
        checkpoint_requests_after - checkpoint_requests_before,
    )
    return {
        "status": "completed" if completed_at else "in-progress",
        "mode": "resume" if resume else "fresh",
        "startedAt": started_at,
        "completedAt": completed_at,
        "searchRequestsAdded": requests_added,
        "checkpointSearchRequestsBefore": checkpoint_requests_before,
        "checkpointSearchRequestsTotal": checkpoint_requests_after,
        "estimatedSearchCostUpperBoundUsd": round(
            requests_added * TIKHUB_PUBLIC_PRICE_RANGE_USD[1],
            4,
        ),
        "pricing": {
            "scope": "TikHub search requests added during this run only",
            "publicRangeUsdPerRequest": list(TIKHUB_PUBLIC_PRICE_RANGE_USD),
            "estimateType": "public-range-upper-bound-before-discounts",
            "sourceUrl": TIKHUB_PRICING_URL,
            "checkedAt": today(),
        },
    }


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def today() -> str:
    return dt.date.today().isoformat()


def iso_from_timestamp(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number > 10_000_000_000:
        number /= 1000
    try:
        return dt.datetime.fromtimestamp(number, tz=dt.timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def parse_date(value: str | None) -> dt.date:
    try:
        return dt.date.fromisoformat((value or "")[:10])
    except ValueError:
        return dt.date(1970, 1, 1)


def video_publication_expiry(
    published_at: str | None,
    max_age_days: int = DEFAULT_RECENT_DAYS,
) -> str:
    published = parse_date(published_at)
    if published == dt.date(1970, 1, 1) or max_age_days < 0:
        return ""
    return (published + dt.timedelta(days=max_age_days)).isoformat()


def video_is_within_publication_window(
    video: dict[str, Any],
    reference_date: dt.date | None = None,
) -> bool:
    expiry = parse_date(video.get("expiresAt"))
    if expiry == dt.date(1970, 1, 1):
        expiry = parse_date(
            video_publication_expiry(video.get("publishedAt"))
        )
    return (
        expiry != dt.date(1970, 1, 1)
        and expiry >= (reference_date or dt.date.today())
    )


def patch_impact_for(
    candidate: dict[str, Any],
    current_patch: str,
) -> dict[str, Any] | None:
    if not PATCH_IMPACTS_PATH.exists():
        return None
    patch = (
        (load_json(PATCH_IMPACTS_PATH).get("patches") or {})
        .get(current_patch)
    )
    if not isinstance(patch, dict):
        return None
    impact = (patch.get("heroes") or {}).get(candidate.get("hero"))
    if not isinstance(impact, dict):
        return None
    effective_at = str(patch.get("effectiveAt") or "")
    if parse_date(candidate.get("publishedAt")) >= parse_date(effective_at):
        return None
    return {
        "patch": current_patch,
        "effectiveAt": effective_at,
        "sourcePublishedAt": patch.get("sourcePublishedAt") or "",
        "source": patch.get("source") or "",
        "status": impact.get("status") or "",
        "summary": impact.get("summary") or "",
    }


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 45) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": HTTP_USER_AGENT,
            **headers,
        },
        method="POST",
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                try:
                    body = response.read()
                except http.client.IncompleteRead as error:
                    body = error.partial
                    try:
                        return json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        if attempt == 0:
                            continue
                        raise VideoPipelineError("请求失败: TikHub 响应传输中断") from error
                try:
                    return json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    if attempt == 0:
                        continue
                    raise VideoPipelineError("请求失败: TikHub 响应不是完整 JSON") from error
        except urllib.error.HTTPError as error:
            message = safe_error_text(error.read().decode("utf-8", errors="replace"))[:500]
            raise VideoPipelineError(f"请求失败 HTTP {error.code}: {message}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise VideoPipelineError(f"请求失败: {error}") from error
    raise VideoPipelineError("请求失败: TikHub 响应传输中断")


def post_json_curl(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 45,
) -> dict[str, Any]:
    """用系统 libcurl 稳定读取 TikHub 的大型分块响应，密钥不进入进程参数。"""
    curl = shutil.which("curl")
    if not curl:
        return post_json(url, payload, headers, timeout)

    def config_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    with tempfile.NamedTemporaryFile(prefix="haidou-tikhub-", suffix=".json") as body_file:
        body_file.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        body_file.flush()
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": HTTP_USER_AGENT,
            **headers,
        }
        config = [
            "silent",
            "show-error",
            "fail-with-body",
            f"max-time = {timeout}",
            'request = "POST"',
            f'url = "{config_value(url)}"',
            f'data-binary = "@{config_value(body_file.name)}"',
        ]
        config.extend(
            f'header = "{config_value(name)}: {config_value(value)}"'
            for name, value in request_headers.items()
        )
        for attempt in range(2):
            result = subprocess.run(
                [curl, "--config", "-"],
                input=("\n".join(config) + "\n").encode("utf-8"),
                capture_output=True,
                check=False,
                timeout=timeout + 5,
            )
            try:
                stdout = (
                    result.stdout.decode("utf-8")
                    if isinstance(result.stdout, bytes)
                    else result.stdout
                )
            except UnicodeDecodeError as error:
                if attempt == 0:
                    continue
                raise VideoPipelineError("TikHub 请求失败: 响应不是完整 UTF-8") from error
            stderr = (
                result.stderr.decode("utf-8", errors="replace")
                if isinstance(result.stderr, bytes)
                else result.stderr
            )
            if result.returncode != 0:
                detail = safe_error_text(stdout or stderr or "未知错误").strip()[:500]
                raise VideoPipelineError(f"TikHub 请求失败（curl {result.returncode}）: {detail}")
            try:
                return json.loads(stdout)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                if attempt == 0:
                    continue
                raise VideoPipelineError("TikHub 请求失败: 响应不是完整 JSON") from error
    raise VideoPipelineError("TikHub 请求失败: 响应不是完整 JSON")


def get_json(
    url: str,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": HTTP_USER_AGENT,
            **(headers or {}),
        },
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                try:
                    body = response.read()
                except http.client.IncompleteRead as error:
                    body = error.partial
                    try:
                        return json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        if attempt == 0:
                            continue
                        raise VideoPipelineError("读取任务失败: 响应传输中断") from error
                try:
                    return json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    if attempt == 0:
                        continue
                    raise VideoPipelineError("读取任务失败: 响应不是完整 JSON") from error
        except urllib.error.HTTPError as error:
            message = safe_error_text(error.read().decode("utf-8", errors="replace"))[:500]
            raise VideoPipelineError(f"读取任务失败 HTTP {error.code}: {message}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise VideoPipelineError(f"读取任务失败: {error}") from error
    raise VideoPipelineError("读取任务失败: 响应传输中断")


def post_file(
    url: str,
    path: Path,
    filename: str,
    timeout: int = 180,
) -> dict[str, Any]:
    """用 multipart/form-data 上传一个受大小限制的本地视频。"""
    size = path.stat().st_size
    if size <= 0 or size > MAX_VIDEO_BYTES:
        raise VideoPipelineError(f"视频文件大小无效（上限 {MAX_VIDEO_BYTES // 1024 // 1024} MB）")
    boundary = f"----Haidou{uuid.uuid4().hex}"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {media_type}\r\n\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    body = prefix + path.read_bytes() + suffix
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")[:500]
        raise VideoPipelineError(f"上传视频失败 HTTP {error.code}: {message}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise VideoPipelineError(f"上传视频失败: {error}") from error


def download_video(url: str, suffix: str = ".mp4") -> Path:
    """把一次性播放地址下载到系统临时目录；错误信息不回显签名 URL。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise VideoPipelineError("TikHub 返回了不安全的视频地址")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "video/*,*/*;q=0.8",
        },
    )
    temporary = tempfile.NamedTemporaryFile(prefix="haidou-video-", suffix=suffix, delete=False)
    path = Path(temporary.name)
    try:
        with temporary, urllib.request.urlopen(request, timeout=120) as response:
            declared_size = int(response.headers.get("Content-Length") or 0)
            if declared_size > MAX_VIDEO_BYTES:
                raise VideoPipelineError("视频超过 256 MB 安全上限")
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise VideoPipelineError("视频超过 256 MB 安全上限")
                temporary.write(chunk)
        if path.stat().st_size == 0:
            raise VideoPipelineError("视频下载结果为空")
        return path
    except urllib.error.HTTPError as error:
        path.unlink(missing_ok=True)
        raise VideoPipelineError(f"临时视频下载失败 HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        path.unlink(missing_ok=True)
        raise VideoPipelineError("临时视频下载失败") from None


def cleanup_bilinote_upload(filename: str) -> None:
    """只清理本轮在本地 BiliNote 上传目录生成的精确文件。"""
    upload_root_value = os.getenv("BILINOTE_UPLOAD_DIR", "").strip()
    if not upload_root_value:
        return
    upload_root = Path(upload_root_value).expanduser().resolve()
    source = (upload_root / Path(filename).name).resolve()
    if source.parent != upload_root:
        raise VideoPipelineError("拒绝清理 BiliNote 上传目录之外的文件")
    related = (
        source,
        source.with_suffix(".mp3"),
        source.with_name(source.stem + "_cover.jpg"),
    )
    for path in related:
        path.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def bilinote_storage_paths() -> tuple[Path, Path] | None:
    """定位本地 BiliNote 的原始笔记与截图目录，不把绝对路径写入草稿。"""
    upload_value = os.getenv("BILINOTE_UPLOAD_DIR", "").strip()
    results_value = os.getenv("BILINOTE_NOTE_RESULTS_DIR", "").strip()
    screenshots_value = os.getenv("BILINOTE_SCREENSHOT_DIR", "").strip()
    backend_root = (
        Path(upload_value).expanduser().resolve().parent
        if upload_value
        else None
    )
    if not results_value and backend_root:
        results_value = str(backend_root / "note_results")
    if not screenshots_value:
        if backend_root:
            screenshots_value = str(backend_root / "static" / "screenshots")
        elif results_value:
            screenshots_value = str(
                Path(results_value).expanduser().resolve().parent
                / "static"
                / "screenshots"
            )
    if not results_value or not screenshots_value:
        return None
    return (
        Path(results_value).expanduser().resolve(),
        Path(screenshots_value).expanduser().resolve(),
    )


def screenshot_marker_rows(markdown: str) -> list[dict[str, Any]]:
    rows = []
    for match in SCREENSHOT_MARKER_RE.finditer(markdown):
        minutes = int(match.group(1) or match.group(3))
        seconds = int(match.group(2) or match.group(4))
        rows.append({
            "timestamp": f"{minutes:02}:{seconds:02}",
            "timestampSeconds": minutes * 60 + seconds,
        })
    return rows


def screenshot_filenames(markdown: str) -> list[str]:
    filenames = []
    for match in MARKDOWN_IMAGE_RE.finditer(markdown):
        url_path = urllib.parse.unquote(
            urllib.parse.urlparse(match.group(1)).path
        )
        filename = Path(url_path).name
        if SCREENSHOT_FILENAME_RE.fullmatch(filename):
            filenames.append(filename)
    return filenames


def bilinote_screenshot_manifest(
    task_id: str,
    video_id: str,
    processed_markdown: str,
) -> dict[str, Any]:
    """按 BiliNote 的替换顺序绑定原始 Screenshot 时间码与最终图片。"""
    if not re.fullmatch(r"[0-9A-Za-z-]+", task_id):
        return {"status": "unavailable", "reason": "invalid-task-id", "frames": []}
    if not re.fullmatch(r"[0-9A-Za-z_-]+", video_id):
        return {"status": "unavailable", "reason": "invalid-video-id", "frames": []}
    storage = bilinote_storage_paths()
    if not storage:
        return {
            "status": "unavailable",
            "reason": "bilinote-storage-not-configured",
            "frames": [],
        }
    results_root, screenshots_root = storage
    raw_path = (results_root / f"{task_id}_markdown.md").resolve()
    if raw_path.parent != results_root or not raw_path.is_file():
        return {
            "status": "unavailable",
            "reason": "raw-markdown-missing",
            "frames": [],
        }
    raw_markdown = raw_path.read_text(encoding="utf-8")
    markers = screenshot_marker_rows(raw_markdown)
    filenames = screenshot_filenames(processed_markdown)
    base = {
        "schemaVersion": 1,
        "mappingSource": "bilinote-raw-marker-and-final-image-order",
        "rawMarkdownSha256": hashlib.sha256(
            raw_markdown.encode("utf-8")
        ).hexdigest(),
        "processedMarkdownSha256": hashlib.sha256(
            processed_markdown.encode("utf-8")
        ).hexdigest(),
        "markerCount": len(markers),
        "imageCount": len(filenames),
    }
    if not markers and not filenames:
        return {**base, "status": "no-screenshots", "frames": []}
    if len(markers) != len(filenames):
        return {**base, "status": "unavailable", "reason": "count-mismatch", "frames": []}

    sources = []
    for filename in filenames:
        source = (screenshots_root / filename).resolve()
        if source.parent != screenshots_root or not source.is_file():
            return {
                **base,
                "status": "unavailable",
                "reason": "screenshot-missing",
                "frames": [],
            }
        sources.append(source)

    destination_root = (CACHE_DIR / "frames" / video_id).resolve()
    if destination_root.parent.parent != CACHE_DIR.resolve():
        return {
            **base,
            "status": "unavailable",
            "reason": "unsafe-destination",
            "frames": [],
        }
    destination_root.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, (marker, filename, source) in enumerate(
        zip(markers, filenames, sources, strict=True)
    ):
        destination = destination_root / filename
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        frames.append({
            "index": index,
            **marker,
            "filename": filename,
            "cachedPath": destination.relative_to(CACHE_DIR.resolve()).as_posix(),
            "sha256": sha256_file(destination),
        })
    return {**base, "status": "captured", "frames": frames}


def attach_bilinote_screenshot_manifest(
    draft: dict[str, Any],
    draft_path: Path,
    processed_markdown: str | None = None,
) -> dict[str, Any]:
    bilinote = dict(draft.get("bilinote") or {})
    task_id = str(bilinote.get("taskId") or "")
    candidate = draft.get("candidate") or {}
    video_id = str(candidate.get("videoId") or "")
    if processed_markdown is None:
        markdown_candidates = [
            draft_path.with_name(draft_path.stem + "-bilinote.md"),
        ]
        if draft_path.stem.endswith(
            f"-v{ANALYSIS_CONTRACT_VERSION}"
        ):
            markdown_candidates.append(
                draft_path.with_name(
                    draft_path.stem.removesuffix(
                        f"-v{ANALYSIS_CONTRACT_VERSION}"
                    )
                    + "-v4-bilinote.md"
                )
            )
        raw_output = next(
            (path for path in markdown_candidates if path.is_file()),
            None,
        )
        if raw_output is None:
            manifest = {
                "status": "unavailable",
                "reason": "processed-markdown-missing",
                "frames": [],
            }
        else:
            processed_markdown = raw_output.read_text(encoding="utf-8")
            manifest = bilinote_screenshot_manifest(
                task_id,
                video_id,
                processed_markdown,
            )
            manifest["processedMarkdownSource"] = raw_output.name
    else:
        manifest = bilinote_screenshot_manifest(
            task_id,
            video_id,
            processed_markdown,
        )
    bilinote["screenshotManifest"] = manifest
    updated = dict(draft)
    updated["bilinote"] = bilinote
    return updated


def attach_bilinote_frame_intelligence(
    draft: dict[str, Any],
) -> dict[str, Any]:
    """对带精确时间码的历史 BiliNote 截图补跑本机 OCR。

    这里只读取已经持久化到缓存的截图；不会重新下载视频，也不会调用
    TikHub、BiliNote 模型或 OpenAI。已有关键帧结果始终优先保留。
    """
    existing = draft.get("keyFrameIntelligence") or {}
    if existing.get("status") == "captured":
        return draft
    manifest = (draft.get("bilinote") or {}).get("screenshotManifest") or {}
    frames = normalized_list(manifest.get("frames"))
    if manifest.get("status") != "captured" or not frames:
        return draft
    candidate = draft.get("candidate") or {}
    video_id = str(candidate.get("videoId") or "")
    if not re.fullmatch(r"[0-9A-Za-z_-]+", video_id):
        return draft
    expected_root = (CACHE_DIR / "frames" / video_id).resolve()
    frame_rows = []
    try:
        for row in frames:
            if not isinstance(row, dict):
                raise VideoPipelineError("BiliNote 截图清单包含无效帧")
            source = (CACHE_DIR / str(row.get("cachedPath") or "")).resolve()
            if source.parent != expected_root or not source.is_file():
                raise VideoPipelineError("BiliNote OCR 截图路径无效")
            frame_rows.append({
                "path": source,
                "timestampSeconds": float(row["timestampSeconds"]),
                "stage": "bilinote-screenshot",
            })
        from pipeline.frame_intelligence import enrich_frame_rows, vision_ocr

        ocr_rows = vision_ocr([row["path"] for row in frame_rows])
        enriched = enrich_frame_rows(
            frame_rows,
            ocr_rows,
            match_icons=False,
        )
        intelligence = {
            "schemaVersion": 1,
            "status": "captured",
            "source": "bilinote-persisted-screenshots",
            "frameCount": len(enriched),
            "frames": enriched,
        }
    except (
        ImportError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as error:
        intelligence = {
            "schemaVersion": 1,
            "status": "unavailable",
            "source": "bilinote-persisted-screenshots",
            "error": safe_error_text(error),
        }
    updated = dict(draft)
    updated["keyFrameIntelligence"] = intelligence
    return updated


def persisted_screenshot_manifest(draft: dict[str, Any]) -> dict[str, Any]:
    manifest = draft.get("evidenceScreenshotManifest")
    if isinstance(manifest, dict):
        return manifest
    return (draft.get("bilinote") or {}).get("screenshotManifest") or {}


def attach_evidence_screenshot_manifest(
    draft: dict[str, Any],
    *,
    tolerance_seconds: float = LOCAL_KEY_FRAME_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    """把 BiliNote 截图与本地关键帧合并为可持久化证据清单。

    本地关键帧只补齐 catalogRecord 中已经存在的 frame 时间戳，不生成
    新结论；最多允许半秒抽帧量化误差，并保留实际捕获时间与文件哈希。
    """
    updated = dict(draft)
    record = updated.get("catalogRecord") or {}
    required_rows = [
        row
        for row in normalized_list(record.get("evidence"))
        if isinstance(row, dict) and row.get("kind") == "frame"
    ]
    required_timestamps = {
        str(row.get("timestamp") or "")
        for row in required_rows
        if timestamp_seconds(row.get("timestamp")) is not None
    }
    bilinote_manifest = (
        (updated.get("bilinote") or {}).get("screenshotManifest") or {}
    )
    frames = [
        dict(row)
        for row in normalized_list(bilinote_manifest.get("frames"))
        if isinstance(row, dict) and str(row.get("timestamp") or "")
    ]
    mapped_timestamps = {
        str(row.get("timestamp") or "")
        for row in frames
    }

    candidate = updated.get("candidate") or {}
    video_id = str(candidate.get("videoId") or "")
    key_frame_root = (CACHE_DIR / "key_frames" / video_id).resolve()
    destination_root = (CACHE_DIR / "frames" / video_id).resolve()
    key_frame_manifest = updated.get("keyFrameIntelligence") or {}
    local_rows = [
        row
        for row in normalized_list(key_frame_manifest.get("frames"))
        if isinstance(row, dict)
    ]
    local_added = 0
    if (
        required_timestamps - mapped_timestamps
        and re.fullmatch(r"[0-9A-Za-z_-]+", video_id)
        and key_frame_manifest.get("status") == "captured"
    ):
        destination_root.mkdir(parents=True, exist_ok=True)
        for timestamp in sorted(required_timestamps - mapped_timestamps):
            required_seconds = timestamp_seconds(timestamp)
            if required_seconds is None:
                continue
            ranked = []
            for row in local_rows:
                try:
                    captured_seconds = float(row.get("timestampSeconds"))
                except (TypeError, ValueError):
                    continue
                offset = abs(captured_seconds - required_seconds)
                if offset > tolerance_seconds:
                    continue
                ranked.append((offset, captured_seconds, row))
            if not ranked:
                continue
            _, captured_seconds, selected = min(
                ranked,
                key=lambda value: (
                    value[0],
                    value[1],
                    str(value[2].get("path") or ""),
                ),
            )
            source_value = str(selected.get("path") or "")
            source = Path(source_value)
            if not source.is_absolute():
                source = (ROOT / source).resolve()
            else:
                source = source.resolve()
            if (
                not source.is_file()
                or not source.is_relative_to(key_frame_root)
            ):
                continue
            source_sha = sha256_file(source)
            safe_timestamp = timestamp.replace(":", "-")
            destination = (
                destination_root
                / f"keyframe-{safe_timestamp}-{source_sha[:12]}{source.suffix.lower()}"
            )
            if not destination.is_file() or sha256_file(destination) != source_sha:
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                try:
                    shutil.copy2(source, temporary)
                    if sha256_file(temporary) != source_sha:
                        raise VideoPipelineError("本地关键帧复制后哈希不一致")
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
            frames.append({
                "index": len(frames),
                "timestamp": timestamp,
                "timestampSeconds": required_seconds,
                "capturedTimestampSeconds": captured_seconds,
                "timestampOffsetSeconds": round(
                    captured_seconds - required_seconds,
                    3,
                ),
                "filename": destination.name,
                "cachedPath": destination.relative_to(
                    CACHE_DIR.resolve()
                ).as_posix(),
                "sha256": source_sha,
                "source": "local-key-frame-intelligence",
                "stage": str(selected.get("stage") or ""),
            })
            mapped_timestamps.add(timestamp)
            local_added += 1

    frames.sort(
        key=lambda row: (
            timestamp_seconds(row.get("timestamp"))
            if timestamp_seconds(row.get("timestamp")) is not None
            else math.inf,
            str(row.get("source") or "bilinote"),
            str(row.get("filename") or ""),
        )
    )
    missing = sorted(required_timestamps - mapped_timestamps)
    sources = []
    if frames:
        if any(row.get("source") != "local-key-frame-intelligence" for row in frames):
            sources.append("bilinote")
        if any(row.get("source") == "local-key-frame-intelligence" for row in frames):
            sources.append("local-key-frame-intelligence")
    manifest = {
        "schemaVersion": 1,
        "status": "captured" if frames else str(
            bilinote_manifest.get("status") or "unavailable"
        ),
        "mappingSource": "+".join(sources) or str(
            bilinote_manifest.get("mappingSource") or ""
        ),
        "bilinoteStatus": str(
            bilinote_manifest.get("status") or "unavailable"
        ),
        "localFramesAdded": local_added,
        "requiredFrameTimestamps": sorted(required_timestamps),
        "missingFrameTimestamps": missing,
        "frames": frames,
    }
    updated["evidenceScreenshotManifest"] = manifest
    return updated


def screenshot_manifest_gate_errors(draft: dict[str, Any]) -> list[str]:
    record = draft.get("catalogRecord") or {}
    frame_timestamps = {
        str(row.get("timestamp") or "")
        for row in normalized_list(record.get("evidence"))
        if isinstance(row, dict) and row.get("kind") == "frame"
    }
    if not frame_timestamps:
        return []
    manifest = persisted_screenshot_manifest(draft)
    if manifest.get("status") != "captured":
        return ["公开画面证据缺少可持久化截图时间映射"]
    mapped_timestamps = {
        str(row.get("timestamp") or "")
        for row in normalized_list(manifest.get("frames"))
        if isinstance(row, dict)
    }
    missing = sorted(frame_timestamps - mapped_timestamps)
    if not missing:
        return []
    return ["公开画面证据没有同时间戳截图: " + "、".join(missing)]


def frame_review_gate_errors(draft: dict[str, Any]) -> list[str]:
    review = draft.get("frameReview")
    if not isinstance(review, dict):
        return []
    errors = [
        str(error)
        for error in normalized_list(review.get("errors"))
        if str(error)
    ]
    if review.get("status") != "applied":
        return errors or ["逐帧复核覆盖层无效"]
    manifest = persisted_screenshot_manifest(draft)
    manifest_frames = {
        str(row.get("timestamp") or ""): row
        for row in normalized_list(manifest.get("frames"))
        if isinstance(row, dict)
    }
    for frame in normalized_list(review.get("reviewedFrames")):
        if not isinstance(frame, dict):
            errors.append("逐帧复核记录格式无效")
            continue
        timestamp = str(frame.get("timestamp") or "")
        expected_sha = str(frame.get("screenshotSha256") or "")
        manifest_frame = manifest_frames.get(timestamp) or {}
        actual_sha = str(manifest_frame.get("sha256") or "")
        if not expected_sha or actual_sha != expected_sha:
            errors.append(f"逐帧复核截图哈希不匹配 {timestamp}")
    return errors


def apply_screenshot_manifest_gate(draft: dict[str, Any]) -> dict[str, Any]:
    updated = dict(draft)
    gate = dict(updated.get("qualityGate") or {})
    errors = [
        str(error)
        for error in normalized_list(gate.get("errors"))
        if str(error)
    ]
    for error in (
        screenshot_manifest_gate_errors(updated)
        + frame_review_gate_errors(updated)
    ):
        if error not in errors:
            errors.append(error)
    try:
        confidence = float(
            (updated.get("catalogRecord") or {}).get("confidence")
        )
        minimum_confidence = float(gate.get("minimumConfidence"))
    except (TypeError, ValueError):
        confidence = -1
        minimum_confidence = 1
    gate["errors"] = errors
    gate["passed"] = not errors and confidence >= minimum_confidence
    updated["qualityGate"] = gate
    return updated


def hero_rows() -> list[dict[str, Any]]:
    return load_json(INDEX_PATH).get("heroes", [])


def hero_by_alias(alias: str) -> dict[str, Any]:
    for hero in hero_rows():
        if hero.get("alias", "").lower() == alias.lower():
            return hero
    raise VideoPipelineError(f"未知英雄 alias: {alias}")


def hero_queries(hero: dict[str, Any]) -> list[str]:
    """每位英雄默认只发两组查询，控制第三方 API 成本。"""
    name = hero["name"]
    terms = [part.strip() for part in (hero.get("search") or "").split(",") if part.strip()]
    nicknames = [
        term for term in terms
        if 1 < len(term) <= 5
        and re.fullmatch(r"[\u4e00-\u9fff]+", term)
        and term not in {name, hero.get("epithet")}
    ]
    queries = [f"{name} 海克斯大乱斗"]
    if nicknames:
        queries.append(f"{nicknames[0]} 海克斯大乱斗")
    else:
        queries.append(f"{name} 海斗")
    return list(dict.fromkeys(queries))


def hero_match_terms(hero: dict[str, Any]) -> list[str]:
    terms = [
        str(hero.get("name") or "").strip(),
        str(hero.get("epithet") or "").strip(),
    ]
    terms.extend(
        part.strip()
        for part in str(hero.get("search") or "").split(",")
        if 1 < len(part.strip()) <= 8
    )
    return list(dict.fromkeys(term for term in terms if term))


def candidate_rejection_reasons(
    candidate: dict[str, Any],
    hero: dict[str, Any],
    max_age_days: int,
    max_duration_seconds: int = 600,
) -> list[str]:
    reasons = []
    published = parse_date(candidate.get("publishedAt"))
    age_days = (dt.date.today() - published).days
    if published == dt.date(1970, 1, 1):
        reasons.append("缺少有效发布日期")
    elif age_days < -1:
        reasons.append("发布日期异常")
    elif age_days > max_age_days:
        reasons.append(f"视频超过 {max_age_days} 天")
    duration = int(candidate.get("durationSeconds") or 0)
    if duration <= 0 or duration > max_duration_seconds:
        reasons.append(f"视频时长不在 1-{max_duration_seconds} 秒范围")
    title = str(candidate.get("title") or "")
    if not any(term in title for term in hero_match_terms(hero)):
        reasons.append("标题未明确命中目标英雄")
    if not any(term in title for term in MODE_TERMS):
        reasons.append("标题未明确标注海克斯大乱斗")
    if not any(term in title for term in TUTORIAL_TERMS):
        reasons.append("标题不像攻略或教学")
    if any(term in title for term in ENTERTAINMENT_TERMS) and not any(
        term in title
        for term in ("攻略", "教学", "推荐", "一图流", "构建", "怎么玩")
    ):
        reasons.append("标题明确偏娱乐或集锦")
    return reasons


def select_refresh_heroes(limit: int) -> list[str]:
    catalog = load_json(CATALOG_PATH) if CATALOG_PATH.exists() else {"videos": []}
    latest: dict[str, dt.date] = {}
    for video in catalog.get("videos", []):
        published = parse_date(video.get("publishedAt"))
        for alias in video.get("heroes", []):
            latest[alias] = max(latest.get(alias, dt.date(1970, 1, 1)), published)
    heroes = hero_rows()
    heroes.sort(key=lambda row: (
        latest.get(row["alias"], dt.date(1970, 1, 1)),
        row.get("tier") or 9,
        row.get("name") or "",
    ))
    return [row["alias"] for row in heroes[:limit]]


def decode_search_row(row: dict[str, Any]) -> dict[str, Any] | None:
    data = row.get("data", row)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    aweme = data.get("aweme_info") or data.get("awemeInfo") or data
    return aweme if isinstance(aweme, dict) else None


def normalize_candidate(row: dict[str, Any], hero: dict[str, Any], query: str) -> dict[str, Any] | None:
    aweme = decode_search_row(row)
    if not aweme:
        return None
    video_id = str(aweme.get("aweme_id") or aweme.get("awemeId") or row.get("data_id") or "")
    if not video_id.isdigit():
        return None
    author = aweme.get("author") or {}
    statistics = aweme.get("statistics") or {}
    video = aweme.get("video") or {}
    description = (aweme.get("desc") or aweme.get("title") or "").strip()
    published = (
        iso_from_timestamp(aweme.get("create_time") or aweme.get("createTime"))
        or str(aweme.get("publishedAt") or "")[:10]
    )
    canonical_url = f"https://www.douyin.com/video/{video_id}"
    share_url = aweme.get("share_url") or aweme.get("shareUrl") or canonical_url
    parsed_share_url = urllib.parse.urlparse(str(share_url))
    if parsed_share_url.scheme != "https" or parsed_share_url.hostname not in {
        "www.douyin.com",
        "v.douyin.com",
    }:
        share_url = canonical_url
    duration = video.get("duration") or aweme.get("duration") or 0
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 0
    if duration > 10_000:
        duration = round(duration / 1000)
    candidate = {
        "id": f"douyin-{video_id}",
        "videoId": video_id,
        "platform": "抖音",
        "url": share_url,
        "hero": hero["alias"],
        "heroName": hero["name"],
        "query": query,
        "title": description or f"{hero['name']} 抖音攻略视频",
        "creator": author.get("nickname") or author.get("unique_id") or "未知作者",
        "publishedAt": published,
        "durationSeconds": duration,
        "engagement": {
            "likes": int(statistics.get("digg_count") or 0),
            "comments": int(statistics.get("comment_count") or 0),
            "shares": int(statistics.get("share_count") or 0),
            "plays": int(statistics.get("play_count") or 0),
        },
    }
    candidate["candidateScore"] = score_candidate(candidate)
    return candidate


def score_candidate(candidate: dict[str, Any]) -> float:
    title = candidate.get("title") or ""
    hero_name = candidate.get("heroName") or ""
    keyword_score = 2.5 if hero_name and hero_name in title else 0
    keyword_score += min(3.0, sum(0.75 for term in GUIDE_TERMS if term in title))
    published = parse_date(candidate.get("publishedAt"))
    age_days = max(0, (dt.date.today() - published).days)
    recency_score = max(0, 3.0 - age_days / 60)
    likes = max(0, int((candidate.get("engagement") or {}).get("likes") or 0))
    engagement_score = min(2.0, math.log10(likes + 1) / 2)
    return round(keyword_score + recency_score + engagement_score, 3)


class TikHubClient:
    def __init__(self, token: str, base_url: str, min_interval: float = 0):
        if not token:
            raise VideoPipelineError("缺少 TIKHUB_TOKEN，尚不能自动搜索抖音")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.min_interval = max(0.0, min_interval)
        self.last_request_at = 0.0

    def wait_for_rate_limit(self) -> None:
        remaining = self.min_interval - (time.monotonic() - self.last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self.last_request_at = time.monotonic()

    def search(self, keyword: str, sort_type: str, publish_time: str) -> list[dict[str, Any]]:
        self.wait_for_rate_limit()
        payload = {
            "keyword": keyword,
            "cursor": 0,
            "sort_type": sort_type,
            "publish_time": publish_time,
            "filter_duration": "0",
            "content_type": "1",
            "search_id": "",
            "backtrace": "",
        }
        response = post_json_curl(
            self.base_url + TIKHUB_SEARCH_PATH,
            payload,
            {"Authorization": f"Bearer {self.token}"},
        )
        if response.get("code") not in (None, 0, 200):
            raise VideoPipelineError(f"TikHub 返回错误: {response.get('message_zh') or response.get('message')}")
        return self._search_rows(response)

    @staticmethod
    def _search_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
        """兼容 TikHub 搜索接口的新列表分组与旧字典响应。"""
        data = response.get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        groups = data if isinstance(data, list) else [data]
        rows: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            business_data = group.get("business_data")
            if isinstance(business_data, list):
                rows.extend(row for row in business_data if isinstance(row, dict))
        return rows

    @staticmethod
    def _media_url(response: dict[str, Any]) -> str:
        data = response.get("data") or {}
        if not isinstance(data, dict):
            return ""
        details = [
            data.get("aweme_detail"),
            data.get("aweme_info"),
            data,
        ]
        aweme_list = data.get("aweme_list")
        if isinstance(aweme_list, list):
            details.extend(aweme_list)
        for detail in details:
            if not isinstance(detail, dict):
                continue
            video = detail.get("video")
            if not isinstance(video, dict):
                continue
            for key in ("play_addr_h264", "play_addr", "download_addr"):
                address = video.get(key)
                if not isinstance(address, dict):
                    continue
                for url in address.get("url_list") or []:
                    if isinstance(url, str) and url.startswith("https://"):
                        return url
        return ""

    def resolve_media_url(self, video_id: str) -> str:
        if not video_id.isdigit():
            raise VideoPipelineError("无效的抖音作品 ID")
        for path in TIKHUB_DETAIL_PATHS:
            self.wait_for_rate_limit()
            params = {"aweme_id": video_id}
            if path.endswith("/fetch_one_video"):
                params["need_anchor_info"] = "false"
            query = urllib.parse.urlencode(params)
            response = get_json(
                f"{self.base_url}{path}?{query}",
                timeout=60,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if response.get("code") not in (None, 0, 200):
                continue
            if media_url := self._media_url(response):
                return media_url
        raise VideoPipelineError("TikHub 未返回可用的视频播放地址")


def discover(args: argparse.Namespace) -> Path:
    run_started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    aliases = [value.strip() for value in (args.heroes or "").split(",") if value.strip()]
    if getattr(args, "all_heroes", False):
        aliases = [hero["alias"] for hero in hero_rows()]
    elif not aliases:
        aliases = select_refresh_heroes(args.refresh_limit)
    output = Path(args.output) if args.output else (
        CACHE_DIR / f"candidates-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    max_age_days = int(getattr(args, "max_age_days", DEFAULT_RECENT_DAYS))
    max_duration_seconds = int(getattr(args, "max_duration_seconds", 600))
    fallback_queries = bool(getattr(args, "fallback_queries", False))
    resume = bool(getattr(args, "resume", False))
    request_interval = float(getattr(args, "request_interval", 1.05))
    max_search_requests = int(getattr(args, "max_search_requests", 0))
    expected_config = {
        "maxAgeDays": max_age_days,
        "maxDurationSeconds": max_duration_seconds,
        "sorts": args.sorts,
        "publishTime": args.publish_time,
        "fallbackQueries": fallback_queries,
    }
    previous = load_json(output) if resume and output.exists() else {}
    if previous and previous.get("config") != expected_config:
        raise VideoPipelineError("候选断点的搜索配置与本轮不一致，拒绝混用")
    all_candidates = {
        row["id"]: row
        for row in previous.get("candidates", [])
        if isinstance(row, dict) and row.get("id")
    }
    query_log = list(previous.get("queries", []))
    checkpoint_requests_before = len(query_log)
    completed = set(previous.get("completedHeroes", []))
    client: TikHubClient | None = None
    budget_exhausted = False
    for alias in aliases:
        if alias in completed:
            continue
        if client is None:
            client = TikHubClient(
                token=os.getenv("TIKHUB_TOKEN", ""),
                base_url=os.getenv("TIKHUB_API_BASE", args.tikhub_base),
                min_interval=request_interval,
            )
        hero = hero_by_alias(alias)
        hero_candidates: dict[str, dict[str, Any]] = {}
        for query in hero_queries(hero):
            query_eligible = 0
            for sort_type in args.sorts.split(","):
                sort_type = sort_type.strip()
                if sort_type not in {"0", "1", "2"}:
                    raise VideoPipelineError(f"无效 sort_type: {sort_type}")
                requests_added = len(query_log) - checkpoint_requests_before
                if max_search_requests and requests_added >= max_search_requests:
                    budget_exhausted = True
                    break
                assert client is not None
                try:
                    rows = client.search(query, sort_type, args.publish_time)
                except VideoPipelineError as error:
                    query_log.append({
                        "hero": alias,
                        "query": query,
                        "sortType": sort_type,
                        "resultCount": 0,
                        "eligibleCount": 0,
                        "error": safe_error_text(str(error)),
                    })
                    print(f"搜索警告 {hero['name']}: 当前查询失败，继续尝试其余查询")
                    continue
                accepted = 0
                for row in rows:
                    candidate = normalize_candidate(row, hero, query)
                    if not candidate:
                        continue
                    reasons = candidate_rejection_reasons(
                        candidate,
                        hero,
                        max_age_days=max_age_days,
                        max_duration_seconds=max_duration_seconds,
                    )
                    if reasons:
                        continue
                    accepted += 1
                    query_eligible += 1
                    existing = hero_candidates.get(candidate["id"])
                    if not existing or candidate["candidateScore"] > existing["candidateScore"]:
                        hero_candidates[candidate["id"]] = candidate
                query_log.append({
                    "hero": alias,
                    "query": query,
                    "sortType": sort_type,
                    "resultCount": len(rows),
                    "eligibleCount": accepted,
                })
            if budget_exhausted:
                break
            if fallback_queries and query_eligible:
                break
        if budget_exhausted:
            print(
                f"本轮达到搜索请求硬上限 {max_search_requests}；"
                "停止继续搜索，并分析当前断点已发现的候选"
            )
            break
        selected_for_hero = sorted(
            hero_candidates.values(),
            key=lambda row: (row.get("publishedAt") or "", row["candidateScore"]),
            reverse=True,
        )[: args.limit_per_hero]
        all_candidates = {
            candidate_id: candidate
            for candidate_id, candidate in all_candidates.items()
            if candidate.get("hero") != alias
        }
        all_candidates.update({candidate["id"]: candidate for candidate in selected_for_hero})
        completed.add(alias)
        candidates = sorted(
            all_candidates.values(),
            key=lambda row: (
                row.get("hero") or "",
                row.get("publishedAt") or "",
                row["candidateScore"],
            ),
            reverse=True,
        )
        payload = {
            "schemaVersion": 3,
            "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "config": expected_config,
            "completedHeroes": [item for item in aliases if item in completed],
            "queries": query_log,
            "candidates": candidates,
            "lastRun": discovery_run_metadata(
                started_at=run_started_at,
                completed_at=None,
                resume=resume,
                checkpoint_requests_before=checkpoint_requests_before,
                checkpoint_requests_after=len(query_log),
            ),
        }
        save_json(output, payload)
        print(
            f"发现进度 {len(completed & set(aliases))}/{len(aliases)}: "
            f"{hero['name']} {len(selected_for_hero)} 条近期攻略"
        )
    selected = sorted(
        all_candidates.values(),
        key=lambda row: (
            row.get("hero") or "",
            row.get("publishedAt") or "",
            row.get("candidateScore") or 0,
        ),
        reverse=True,
    )
    completed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    run_metadata = discovery_run_metadata(
        started_at=run_started_at,
        completed_at=completed_at,
        resume=resume,
        checkpoint_requests_before=checkpoint_requests_before,
        checkpoint_requests_after=len(query_log),
    )
    if budget_exhausted:
        run_metadata["status"] = "budget-exhausted"
    save_json(output, {
        "schemaVersion": 3,
        "updatedAt": completed_at,
        "config": expected_config,
        "completedHeroes": [item for item in aliases if item in completed],
        "queries": query_log,
        "candidates": selected,
        "lastRun": run_metadata,
    })
    print(
        f"发现阶段结束: {len(completed & set(aliases))}/{len(aliases)} 位英雄、"
        f"{len(selected)} 条候选、"
        f"本轮新增搜索 {run_metadata['searchRequestsAdded']} 次、"
        f"断点累计 {run_metadata['checkpointSearchRequestsTotal']} 次；"
        f"本轮公开价格上限估算 "
        f"${run_metadata['estimatedSearchCostUpperBoundUsd']:.2f}，"
        f"实际费用以 TikHub 账单为准。结果写入 {output}"
    )
    return output


def analysis_prompt(
    hero: dict[str, Any],
    current_patch: str,
    video_duration_seconds: int | float | None = None,
) -> str:
    augment_names, item_names = known_strategy_names()
    rune_names = known_rune_names()
    summoner_spell_names = allowed_summoner_spell_names()
    augment_vocabulary = "、".join(sorted(augment_names))
    item_vocabulary = "、".join(sorted(item_names))
    rune_vocabulary = "、".join(sorted(rune_names))
    summoner_spell_vocabulary = "、".join(sorted(summoner_spell_names))
    alias_lines = [
        f"{alias} → {standard}"
        for group in SAFE_NAME_ALIASES.values()
        for alias, standard in group.items()
    ]
    try:
        duration_seconds = int(float(video_duration_seconds))
    except (TypeError, ValueError, OverflowError):
        duration_seconds = 0
    duration_rule = (
        f"\n视频真实时长为 {duration_seconds} 秒；每条 evidence 的时间戳必须满足 "
        f"0 <= t < {duration_seconds} 秒，不能指向视频结束或之后。"
        if duration_seconds > 0
        else ""
    )
    return f"""
你正在为 LOL 海克斯大乱斗攻略站核对一条抖音视频。目标英雄是 {hero['name']}（alias={hero['alias']}），
站点当前游戏补丁是 {current_patch}。{duration_rule}

必须同时利用视频画面、字幕和语音；不要只复述标题。视频未明确展示或说明的内容必须留空，禁止猜测。
特别检查：强化符文、装备及购买顺序、符文天赋、技能加点、召唤师技能、打法条件。
每个关键结论都要给出视频时间戳，并标明证据来自 frame、subtitle 或 audio。
任何 kind=frame 的 evidence 都必须在笔记正文同一时间点插入 *Screenshot-[mm:ss]，
以便保存可复核的原始画面；做不到就不能把该条证据标成 frame。
evidence 中至少要有一条时间戳证据，在 claim 里明确出现目标英雄的官方中文名“{hero['name']}”
或英文 alias“{hero['alias']}”；标题、搜索词和 hero 字段不能替代这条身份依据。
分类必须严格：海克斯强化只能写入 augments，商店购买的装备只能写入 items，常规基石/小符文只能写入 runes。
如果视频包含多套互斥玩法，必须拆成 strategies 中彼此独立的方案；不得把多套方案摊平成一条出装列表。
每套方案必须有稳定且唯一的 id、简短 label，并在对应 evidence 上填写同一个 strategyId。
只提取画面和讲解证据足够完整的方案；单个视频最多保留 4 套，证据不足的方案不要猜。
每套方案内 items 的 order 必须从 1 开始且不能重复；其强化、装备、符文和打法必须属于同一套方案。
items 中每件装备都必须按标准全名出现在一条带时间戳的“装备/购买/出装/装备栏/做出/合成”证据里；同名强化不能充当装备证据。
augments 与 runes 中每个名称都必须按标准全名出现在至少一条带时间戳的证据里；不能用同类泛称或总结代替逐项证据。
skillOrder 只写技能升级顺序；必须有一条时间戳证据明确出现“加点”“主升/副升”或等价表述，并覆盖所列 Q/W/E/R 字母。
名称必须使用画面或口播能确认的游戏内标准中文名；“减CD”“攻速”等泛称、OCR 不确定词和谐音词不能冒充名称。
patchMentioned 只有在画面、字幕或语音明确出现完整补丁号时才能填写；不得从本提示、发布日期或“新版本”等模糊词推断。
若填写 patchMentioned，evidence 必须有一条 claim 原样包含这个完整补丁号；否则必须为 null。
名称只能从下列当前本地词典中选择；无法唯一匹配时必须留空，不得选择读音相近的名称：
- 海克斯强化（{len(augment_names)} 项）：{augment_vocabulary}
- 可购买装备（{len(item_names)} 项）：{item_vocabulary}
- 当前符文与符文系（{len(rune_names)} 项）：{rune_vocabulary}
- 海克斯大乱斗可用召唤师技能（{len(summoner_spell_names)} 项）：{summoner_spell_vocabulary}
以下俗称或常见 OCR 错字只允许按确定映射纠正：{"；".join(alias_lines)}。
若提取了任何具体强化或装备，evidence 中至少要有一条 kind=frame 的画面证据，说明画面实际显示了什么；
如果所有关键搭配都无法由画面确认，就将 confidence 设为低于 0.65，并在 caveat 说明。
若是娱乐剪辑、纯战绩展示或信息不足，confidence 必须低于 0.65。
title 必须用一句话准确概括视频中已证实的核心玩法；summary 必须用 2 至 3 句总结适用条件、核心搭配与局限。
reason、playstyle、title 与 summary 不得补写证据里没有的效果、打法或适用条件；自动发布时网页只会展示通过证据闸门的搭配名称和确定性摘要。
title 或 summary 留空会被质量闸门拒绝，不能照抄空字段模板。

在笔记末尾输出且只输出一个 ```json 代码块，结构严格如下：
{{
  "schemaVersion": 2,
  "hero": "{hero['alias']}",
  "title": "",
  "summary": "",
  "patchMentioned": null,
  "strategies": [
    {{
      "id": "build-1",
      "label": "主方案",
      "augments": [],
      "items": [],
      "runes": [],
      "skillOrder": [],
      "summonerSpells": [],
      "playstyle": []
    }}
  ],
  "evidence": [],
  "confidence": 0.0,
  "caveat": ""
}}

以上空数组只是结构模板，不是建议答案。不得输出“装备名”“符文名”“强化名”“作者未明确指出”等模板占位词。
augments 条目格式为 {{"name":"","priority":"核心或可选","reason":""}}；
items 条目格式为 {{"name":"","order":1,"reason":""}}；
单方案 evidence 至少两条；多方案时每套方案至少两条且至少一条为 frame。
条目格式为 {{"timestamp":"mm:ss","kind":"frame或subtitle或audio","strategyId":"build-1","claim":""}}；
只用于英雄身份或版本号的公共证据可省略 strategyId，具体强化、装备、符文和加点证据不能省略。
""".strip()


def extract_json_block(markdown: str) -> dict[str, Any]:
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", markdown or "", flags=re.S | re.I)
    candidates = list(reversed(blocks))
    stripped = (markdown or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for raw in candidates:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("schemaVersion") in {1, 2}:
            return payload
    raise VideoPipelineError("BiliNote 结果里没有找到合格的 JSON 分析块")


def timestamp_seconds(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or value < 0:
            return None
        return int(value)
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


@functools.lru_cache(maxsize=1)
def known_strategy_names() -> tuple[set[str], set[str]]:
    augments = load_json(ROOT / "data" / "raw" / "hexdata" / "augments.json")
    client_augments = load_json(ROOT / "data" / "raw" / "cherry_augments_zh.json")
    items = load_json(ROOT / "data" / "raw" / "ddragon" / "item.json").get("data", {})
    augment_names = {
        str(row.get("name") or "").strip()
        for row in augments
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }
    current_client_augment_names = {
        str(row.get("nameTRA") or "").strip()
        for row in client_augments
        if (
            isinstance(row, dict)
            and (
                "/UX/Cherry/" in str(row.get("augmentSmallIconPath") or "")
                or "/UX/Kiwi/" in str(row.get("augmentSmallIconPath") or "")
            )
            and str(row.get("nameTRA") or "").strip()
            and "？" not in str(row.get("nameTRA") or "")
        )
    }
    augment_names.intersection_update(current_client_augment_names)
    augment_names.update(
        str(row.get("nameTRA") or "").strip()
        for row in client_augments
        if (
            isinstance(row, dict)
            and "/UX/Kiwi/" in str(row.get("augmentSmallIconPath") or "")
            and str(row.get("nameTRA") or "").strip()
            and "？" not in str(row.get("nameTRA") or "")
        )
    )
    observed_mayhem_item_names = set()
    for hero_path in (ROOT / "data" / "raw" / "hexdata" / "heroes").glob("*.json"):
        for row in normalized_list(load_json(hero_path).get("items")):
            name = str((row or {}).get("itemName") or "").strip()
            if name:
                observed_mayhem_item_names.add(name)
    item_names = {
        str(row.get("name") or "").strip()
        for row in items.values()
        if (
            isinstance(row, dict)
            and str(row.get("name") or "").strip()
            and (row.get("gold") or {}).get("purchasable")
            and (
                (row.get("maps") or {}).get("12") is True
                or str(row.get("name") or "").strip()
                in observed_mayhem_item_names
            )
        )
    }
    return augment_names, item_names


def known_rune_names() -> set[str]:
    styles = load_json(ROOT / "data" / "raw" / "ddragon" / "runesReforged.json")
    names = {
        str(style.get("name") or "").strip()
        for style in styles
        if isinstance(style, dict) and str(style.get("name") or "").strip()
    }
    for style in styles:
        if not isinstance(style, dict):
            continue
        for slot in normalized_list(style.get("slots")):
            if not isinstance(slot, dict):
                continue
            names.update(
                str(rune.get("name") or "").strip()
                for rune in normalized_list(slot.get("runes"))
                if isinstance(rune, dict) and str(rune.get("name") or "").strip()
            )
    return names


def allowed_summoner_spell_names() -> set[str]:
    """Data Dragon 中可用于普通 ARAM 或海克斯大乱斗模式的召唤师技能。"""
    rows = (
        load_json(ROOT / "data" / "raw" / "ddragon" / "summoner.json")
        .get("data", {})
        .values()
    )
    return {
        str(row.get("name") or "").strip()
        for row in rows
        if isinstance(row, dict)
        and str(row.get("name") or "").strip()
        and MAYHEM_SPELL_MODES.intersection(normalized_list(row.get("modes")))
    }


def ensure_strategy_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """把 v1 单方案分析无损升级为 v2 多方案结构，并保留首方案兼容字段。"""
    upgraded = json.loads(json.dumps(payload, ensure_ascii=False))
    raw_strategies = upgraded.get("strategies")
    if isinstance(raw_strategies, list) and raw_strategies:
        source_rows = [row for row in raw_strategies if isinstance(row, dict)]
    else:
        legacy = upgraded.get("strategy")
        source_rows = [legacy] if isinstance(legacy, dict) else []

    strategies = []
    used_ids = set()
    for index, source in enumerate(source_rows[:4], 1):
        strategy = json.loads(json.dumps(source, ensure_ascii=False))
        strategy_id = str(strategy.get("id") or f"build-{index}").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", strategy_id):
            strategy_id = f"build-{index}"
        while strategy_id in used_ids:
            strategy_id = f"build-{index}-{len(used_ids) + 1}"
        used_ids.add(strategy_id)
        strategy["id"] = strategy_id
        strategy["label"] = str(
            strategy.get("label")
            or ("主方案" if len(source_rows) == 1 else f"方案 {index}")
        ).strip()[:24]
        for key in (*STRATEGY_KEYS, "summonerSpells"):
            strategy[key] = normalized_list(strategy.get(key))
        strategies.append(strategy)

    upgraded["schemaVersion"] = 2
    upgraded["strategies"] = strategies
    upgraded["strategy"] = (
        json.loads(json.dumps(strategies[0], ensure_ascii=False))
        if strategies
        else {}
    )
    return upgraded


def analysis_strategies(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """读取多方案结构；旧草稿会被包装成单元素列表。"""
    return ensure_strategy_contract(payload).get("strategies", [])


def strategy_evidence_rows(
    payload: dict[str, Any],
    strategy_id: str,
    *,
    require_assignment: bool,
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in normalized_list(payload.get("evidence"))
        if isinstance(row, dict)
    ]
    if not require_assignment:
        return rows
    return [
        row
        for row in rows
        if str(row.get("strategyId") or "").strip() == strategy_id
    ]


def normalize_analysis_names(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """只纠正可审计的一对一俗称/OCR 映射；原始时间戳证据保持视频原话。"""
    normalized = ensure_strategy_contract(payload)
    strategies = normalized.get("strategies", [])
    changes = []
    if not strategies:
        return normalized, changes
    for strategy in strategies:
        for group, aliases in SAFE_NAME_ALIASES.items():
            for row in normalized_list(strategy.get(group)):
                if not isinstance(row, dict):
                    continue
                original = str(row.get("name") or "").strip()
                standard = aliases.get(original)
                if not standard:
                    continue
                row["name"] = standard
                changes.append({
                    "strategyId": strategy["id"],
                    "group": group,
                    "from": original,
                    "to": standard,
                })

    narrative_aliases = {
        alias: standard
        for aliases in SAFE_NAME_ALIASES.values()
        for alias, standard in aliases.items()
    }

    def normalize_text(value: Any, field: str) -> Any:
        if not isinstance(value, str):
            return value
        result = value
        for alias, standard in sorted(
            narrative_aliases.items(), key=lambda row: len(row[0]), reverse=True
        ):
            if alias not in result:
                continue
            result = result.replace(alias, standard)
            changes.append(
                {"group": "narrative", "field": field, "from": alias, "to": standard}
            )
        return result

    for field in ("title", "summary", "caveat"):
        normalized[field] = normalize_text(normalized.get(field), field)
    for strategy in strategies:
        for group in ("augments", "items"):
            for row in normalized_list(strategy.get(group)):
                if isinstance(row, dict):
                    row["reason"] = normalize_text(
                        row.get("reason"),
                        f"strategies.{strategy['id']}.{group}.reason",
                    )
        strategy["playstyle"] = [
            normalize_text(value, f"strategies.{strategy['id']}.playstyle")
            for value in normalized_list(strategy.get("playstyle"))
        ]
    normalized["strategy"] = json.loads(
        json.dumps(strategies[0], ensure_ascii=False)
    )
    return normalized, changes


def attach_key_frame_identity_evidence(
    payload: dict[str, Any],
    frame_intelligence: dict[str, Any],
    hero: dict[str, Any],
) -> dict[str, Any]:
    """用本地 OCR 补齐可复核的英雄身份时间戳，不补写任何攻略搭配。"""
    normalized = ensure_strategy_contract(payload)
    evidence = [
        dict(row)
        for row in normalized_list(normalized.get("evidence"))
        if isinstance(row, dict)
    ]
    official_terms = [
        str(hero.get("name") or "").strip(),
        str(hero.get("alias") or "").strip(),
    ]
    if any(
        term
        and term.casefold() in str(row.get("claim") or "").casefold()
        for row in evidence
        for term in official_terms
    ):
        normalized["evidence"] = evidence
        return normalized

    identity_terms = []
    contextual_single_character_terms = []
    for value in (
        *official_terms,
        str(hero.get("epithet") or "").strip(),
        *re.split(r"[,，/、\s]+", str(hero.get("search") or "").strip()),
    ):
        term = str(value or "").strip()
        if len(term) >= 2 and term not in identity_terms:
            identity_terms.append(term)
        elif (
            len(term) == 1
            and term == official_terms[0]
            and term not in contextual_single_character_terms
        ):
            contextual_single_character_terms.append(term)

    for frame in sorted(
        normalized_list(frame_intelligence.get("frames")),
        key=lambda row: float((row or {}).get("timestampSeconds") or 0),
    ):
        if not isinstance(frame, dict):
            continue
        ocr_text = str(frame.get("ocrText") or "")
        matched_term = next(
            (
                term
                for term in identity_terms
                if term.casefold() in ocr_text.casefold()
            ),
            "",
        )
        if not matched_term:
            matched_term = next(
                (
                    term
                    for term in contextual_single_character_terms
                    if re.search(
                        re.escape(term)
                        + r"(?:的)?(?:玩法|攻略|出装|强化|海克斯|教学|怎么玩)",
                        ocr_text,
                    )
                ),
                "",
            )
        if not matched_term:
            continue
        seconds = max(
            0,
            int(round(float(frame.get("timestampSeconds") or 0))),
        )
        minutes, remaining = divmod(seconds, 60)
        evidence.append({
            "timestamp": f"{minutes:02}:{remaining:02}",
            "kind": "frame",
            "claim": (
                f"画面文字出现“{matched_term}”，对应目标英雄"
                f"{official_terms[0]}（{official_terms[1]}）"
            ),
            "source": "local-vision-ocr",
        })
        break
    normalized["evidence"] = evidence
    return normalized


def strategy_names(rows: Any) -> list[str]:
    names = []
    for row in normalized_list(rows):
        value = row.get("name") if isinstance(row, dict) else row
        name = str(value or "").strip()
        if name:
            names.append(name)
    return names


def strategy_name_variants(group: str, name: str) -> set[str]:
    """返回标准名及其已审核的一对一别名，不改写原始时间戳证据。"""
    variants = {name}
    variants.update(
        alias
        for alias, standard in SAFE_NAME_ALIASES.get(group, {}).items()
        if standard == name
    )
    return variants


def claim_supports_strategy_name(group: str, name: str, claims: list[str]) -> bool:
    variants = strategy_name_variants(group, name)
    return any(
        variant and variant in claim
        for variant in variants
        for claim in claims
    )


def project_grounded_analysis(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """只保留当前词典合法且有逐项时间戳证据的公开搭配子集。"""
    projected = ensure_strategy_contract(payload)
    strategies = projected.get("strategies", [])
    if not strategies:
        return projected, []
    known_augments, known_items = known_strategy_names()
    omissions = []
    require_assignment = len(strategies) > 1
    for strategy in strategies:
        strategy_id = strategy["id"]
        evidence_rows = strategy_evidence_rows(
            projected,
            strategy_id,
            require_assignment=require_assignment,
        )
        evidence_claims = [
            str(row.get("claim") or "")
            for row in evidence_rows
        ]
        item_evidence_claims = [
            claim
            for claim in evidence_claims
            if any(term in claim for term in ITEM_EVIDENCE_TERMS)
        ]

        def project_named_rows(
            group: str,
            allowed_names: set[str],
            claims: list[str],
        ) -> list[Any]:
            rows = []
            for row in normalized_list(strategy.get(group)):
                name = str(
                    (row.get("name") if isinstance(row, dict) else row) or ""
                ).strip()
                if name not in allowed_names:
                    omissions.append({
                        "strategyId": strategy_id,
                        "group": group,
                        "name": name,
                        "reason": "not-in-current-dictionary",
                    })
                    continue
                if not claim_supports_strategy_name(group, name, claims):
                    omissions.append({
                        "strategyId": strategy_id,
                        "group": group,
                        "name": name,
                        "reason": "missing-timestamp-evidence",
                    })
                    continue
                rows.append(row)
            return rows

        strategy["augments"] = project_named_rows(
            "augments", known_augments, evidence_claims
        )
        strategy["items"] = project_named_rows(
            "items", known_items, item_evidence_claims
        )
        strategy["runes"] = project_named_rows(
            "runes", known_rune_names(), evidence_claims
        )
        strategy["summonerSpells"] = project_named_rows(
            "summonerSpells",
            allowed_summoner_spell_names(),
            evidence_claims,
        )

        skill_rows = normalized_list(strategy.get("skillOrder"))
        if skill_rows:
            level_terms = (
                "加点", "主升", "副升", "主Q", "主W", "主E",
                "一级点", "最后加", "点满",
            )
            skill_evidence = [
                claim
                for claim in evidence_claims
                if any(term in claim for term in level_terms)
            ]
            if (
                not skill_evidence
                or not skill_order_keys(skill_rows).issubset(
                    skill_order_keys(skill_evidence)
                )
            ):
                omissions.extend({
                    "strategyId": strategy_id,
                    "group": "skillOrder",
                    "name": name,
                    "reason": "missing-timestamp-evidence",
                } for name in strategy_names(skill_rows))
                strategy["skillOrder"] = []
        strategy["playstyle"] = []
    projected["strategy"] = json.loads(
        json.dumps(strategies[0], ensure_ascii=False)
    )
    return projected, omissions


def video_strategy_errors(video: dict[str, Any]) -> list[str]:
    """检查目录记录里的搭配名称是否仍适用于当前海克斯大乱斗数据。"""
    strategies = analysis_strategies(video)
    if not strategies:
        return ["缺少结构化搭配"]
    known_augments, known_items = known_strategy_names()
    groups = (
        ("强化", "augments", known_augments),
        ("装备", "items", known_items),
        ("符文", "runes", known_rune_names()),
        ("召唤师技能", "summonerSpells", allowed_summoner_spell_names()),
    )
    errors = []
    for strategy in strategies:
        prefix = (
            str(strategy.get("label") or strategy.get("id") or "方案") + "的"
            if len(strategies) > 1
            else ""
        )
        for label, key, allowed_names in groups:
            unknown = sorted(set(strategy_names(strategy.get(key))) - allowed_names)
            if unknown:
                errors.append(
                    f"{prefix}{label}不再适用于当前海克斯大乱斗: "
                    + ", ".join(unknown)
                )
    return errors


def video_is_currently_publishable(
    video: dict[str, Any],
    reference_date: dt.date | None = None,
) -> bool:
    return (
        video_is_within_publication_window(video, reference_date)
        and not video_strategy_errors(video)
    )


def skill_order_keys(rows: Any) -> set[str]:
    keys = set()
    for row in normalized_list(rows):
        values = (
            (row.get("skill"), row.get("order"), row.get("name"))
            if isinstance(row, dict)
            else (row,)
        )
        for value in values:
            keys.update(
                re.findall(r"(?<![A-Z])[QWER](?![A-Z])", str(value or "").upper())
            )
    return keys


def validate_analysis(
    payload: dict[str, Any],
    expected_alias: str,
    current_patch: str = "",
    video_duration_seconds: int | float | None = None,
) -> list[str]:
    errors = []
    raw_strategies = payload.get("strategies")
    if isinstance(raw_strategies, list) and len(raw_strategies) > 4:
        errors.append("单个视频最多允许 4 套有证据的方案")
    payload = ensure_strategy_contract(payload)
    try:
        duration_seconds = int(float(video_duration_seconds))
    except (TypeError, ValueError, OverflowError):
        duration_seconds = 0
    if payload.get("hero") != expected_alias:
        errors.append(f"英雄不匹配: expected={expected_alias} actual={payload.get('hero')}")
    if not str(payload.get("summary") or "").strip():
        errors.append("缺少攻略摘要")
    title_and_summary = (
        str(payload.get("title") or "") + " " + str(payload.get("summary") or "")
    )
    strategies = payload.get("strategies", [])
    if any(term in title_and_summary for term in MULTI_BUILD_TERMS) and len(strategies) < 2:
        errors.append("视频包含多套互斥玩法，当前单方案结构不能安全发布")
    suspicious_terms = [
        term for term in UNSUPPORTED_TEXT_TERMS
        if term in title_and_summary
    ]
    if suspicious_terms:
        errors.append(f"正文含疑似夸张或 OCR 错误: {', '.join(suspicious_terms)}")
    if not strategies or not any(
        any(strategy.get(key) for key in STRATEGY_KEYS)
        for strategy in strategies
    ):
        errors.append("没有提取到任何强化、出装、符文、加点或打法")
    else:
        ids = [str(strategy.get("id") or "") for strategy in strategies]
        labels = [str(strategy.get("label") or "") for strategy in strategies]
        if len(ids) != len(set(ids)):
            errors.append("多方案 strategy id 必须唯一")
        if len(strategies) > 1 and (
            any(not label.strip() for label in labels)
            or len(labels) != len(set(labels))
        ):
            errors.append("多方案 label 必须非空且唯一")
        known_augments, known_items = known_strategy_names()
        champion = (
            load_json(ROOT / "data" / "raw" / "ddragon" / "champion.json")
            .get("data", {})
            .get(expected_alias, {})
        )
        stats = champion.get("stats") or {}
        mana_items = set()
        if stats.get("mp") == 0 and stats.get("mpperlevel") == 0:
            item_rows = (
                load_json(ROOT / "data" / "raw" / "ddragon" / "item.json")
                .get("data", {})
                .values()
            )
            mana_items = {
                str(row.get("name") or "").strip()
                for row in item_rows
                if isinstance(row, dict)
                and (
                    "Mana" in normalized_list(row.get("tags"))
                    or float((row.get("stats") or {}).get("FlatMPPoolMod") or 0) > 0
                )
            }
        require_assignment = len(strategies) > 1
        for strategy in strategies:
            prefix = (
                f"{strategy.get('label') or strategy.get('id')}："
                if require_assignment
                else ""
            )
            if not any(strategy.get(key) for key in EVIDENCE_STRATEGY_KEYS):
                errors.append(prefix + "只有打法概述，缺少可逐项验证的搭配字段")
            placeholder_terms = (
                "装备名", "符文名", "强化名", "作者未明确指出", "可执行打法"
            )
            serialized_strategy = json.dumps(strategy, ensure_ascii=False)
            if any(term in serialized_strategy for term in placeholder_terms):
                errors.append(prefix + "策略里包含模板占位词")
            evidence_rows = strategy_evidence_rows(
                payload,
                str(strategy.get("id") or ""),
                require_assignment=require_assignment,
            )
            if require_assignment:
                if len(evidence_rows) < 2:
                    errors.append(prefix + "至少需要两条归属于本方案的时间戳证据")
                if not any(row.get("kind") == "frame" for row in evidence_rows):
                    errors.append(prefix + "缺少归属于本方案的画面证据")
            evidence_claims = [
                str(row.get("claim") or "")
                for row in evidence_rows
            ]
            unknown_augments = sorted(
                set(strategy_names(strategy.get("augments"))) - known_augments
            )
            unknown_items = sorted(
                set(strategy_names(strategy.get("items"))) - known_items
            )
            unknown_runes = sorted(
                set(strategy_names(strategy.get("runes"))) - known_rune_names()
            )
            unavailable_summoner_spells = sorted(
                set(strategy_names(strategy.get("summonerSpells")))
                - allowed_summoner_spell_names()
            )
            if unknown_augments:
                errors.append(
                    prefix + f"强化名称不在游戏词典: {', '.join(unknown_augments)}"
                )
            if unknown_items:
                errors.append(
                    prefix + f"装备名称不在游戏词典: {', '.join(unknown_items)}"
                )
            missing_augment_evidence = sorted(
                name
                for name in set(strategy_names(strategy.get("augments")))
                if not claim_supports_strategy_name(
                    "augments",
                    name,
                    evidence_claims,
                )
            )
            if missing_augment_evidence:
                errors.append(
                    prefix + "强化缺少逐项时间戳证据: "
                    + ", ".join(missing_augment_evidence)
                )
            item_names = set(strategy_names(strategy.get("items")))
            if item_names:
                item_evidence_claims = [
                    claim
                    for claim in evidence_claims
                    if any(term in claim for term in ITEM_EVIDENCE_TERMS)
                ]
                missing_item_evidence = sorted(
                    name
                    for name in item_names
                    if not claim_supports_strategy_name(
                        "items", name, item_evidence_claims
                    )
                )
                if missing_item_evidence:
                    errors.append(
                        prefix + "出装缺少逐件时间戳证据: "
                        + ", ".join(missing_item_evidence)
                    )
            if unknown_runes:
                errors.append(
                    prefix
                    + f"符文名称不在当前游戏词典: {', '.join(unknown_runes)}"
                )
            missing_rune_evidence = sorted(
                name
                for name in set(strategy_names(strategy.get("runes")))
                if not claim_supports_strategy_name(
                    "runes", name, evidence_claims
                )
            )
            if missing_rune_evidence:
                errors.append(
                    prefix + "符文缺少逐项时间戳证据: "
                    + ", ".join(missing_rune_evidence)
                )
            if unavailable_summoner_spells:
                errors.append(
                    prefix + "召唤师技能不适用于海克斯大乱斗: "
                    + ", ".join(unavailable_summoner_spells)
                )
            skill_rows = normalized_list(strategy.get("skillOrder"))
            if skill_rows:
                level_terms = (
                    "加点", "主升", "副升", "主Q", "主W", "主E",
                    "一级点", "最后加", "点满",
                )
                skill_evidence = [
                    claim
                    for claim in evidence_claims
                    if any(term in claim for term in level_terms)
                ]
                if not skill_evidence:
                    errors.append(prefix + "技能加点缺少明确的时间戳证据")
                else:
                    covered_keys = skill_order_keys(skill_evidence)
                    missing_keys = sorted(
                        skill_order_keys(skill_rows) - covered_keys
                    )
                    if missing_keys:
                        errors.append(
                            prefix
                            + f"技能加点证据未覆盖: {', '.join(missing_keys)}"
                        )
            incompatible_items = sorted(
                set(strategy_names(strategy.get("items"))) & mana_items
            )
            if incompatible_items:
                errors.append(
                    prefix
                    + f"无蓝英雄不能采用法力装备: {', '.join(incompatible_items)}"
                )
            item_orders = []
            for row in normalized_list(strategy.get("items")):
                if not isinstance(row, dict):
                    continue
                try:
                    order = int(row.get("order"))
                except (TypeError, ValueError):
                    errors.append(prefix + "装备购买顺序必须是正整数")
                    continue
                if order < 1:
                    errors.append(prefix + "装备购买顺序必须是正整数")
                item_orders.append(order)
            if len(item_orders) != len(set(item_orders)):
                errors.append(prefix + "装备购买顺序重复，疑似混入多套互斥方案")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 2:
        errors.append("至少需要两条带时间戳的证据")
    else:
        has_frame = False
        for index, row in enumerate(evidence):
            if not isinstance(row, dict):
                errors.append(f"证据 {index + 1} 格式无效")
                continue
            evidence_seconds = timestamp_seconds(row.get("timestamp"))
            if evidence_seconds is None:
                errors.append(f"证据 {index + 1} 缺少有效时间戳")
            elif duration_seconds > 0 and evidence_seconds >= duration_seconds:
                errors.append(
                    f"证据 {index + 1} 时间戳超出视频时长: "
                    f"{row.get('timestamp')} >= {duration_seconds}s"
                )
            if row.get("kind") not in {"frame", "subtitle", "audio"}:
                errors.append(f"证据 {index + 1} kind 无效")
            has_frame = has_frame or row.get("kind") == "frame"
            if not str(row.get("claim") or "").strip():
                errors.append(f"证据 {index + 1} 缺少结论")
        if not has_frame:
            errors.append("缺少画面证据，不能标记为多模态已读")
        hero = hero_by_alias(expected_alias)
        hero_terms = {
            str(hero.get("name") or "").strip(),
            str(hero.get("alias") or "").strip(),
        }
        evidence_claims_casefolded = [
            str(row.get("claim") or "").casefold()
            for row in evidence
            if isinstance(row, dict)
        ]
        if not any(
            term and any(term.casefold() in claim for claim in evidence_claims_casefolded)
            for term in hero_terms
        ):
            errors.append(
                f"英雄身份缺少时间戳证据: {hero['name']}（{hero['alias']}）"
            )
    patch_mentioned = str(payload.get("patchMentioned") or "").strip()
    if patch_mentioned and not any(
        patch_mentioned in str(row.get("claim") or "")
        for row in normalized_list(evidence)
        if isinstance(row, dict)
    ):
        errors.append("版本号缺少对应的时间戳证据")
    has_current_patch_evidence = bool(current_patch) and patch_mentioned == current_patch and any(
        current_patch in str(row.get("claim") or "")
        for row in normalized_list(evidence)
        if isinstance(row, dict)
    )
    if current_patch and current_patch in title_and_summary and not has_current_patch_evidence:
        errors.append(f"正文未经视频证据声称适用于当前补丁 {current_patch}")
    if (
        re.search(r"(当前|本|最新|新)版本", title_and_summary)
        and not has_current_patch_evidence
    ):
        errors.append("正文未经视频证据声称适用于当前或最新版本")
    try:
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError):
        confidence = -1
    if not 0 <= confidence <= 1:
        errors.append("confidence 必须在 0 到 1 之间")
    return errors


def bounded_edit_distance(left: str, right: str, limit: int) -> int:
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, 1):
        current = [left_index]
        for right_index, right_character in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1]
                + (left_character != right_character),
            ))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def conservative_ocr_fuzzy_contains(text: str, name: str) -> bool:
    """只接受较长标准名的小量 OCR 字符误差；调用方还必须要求多帧复现。"""
    compact_text = re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff]+",
        "",
        str(text or "").casefold(),
    )
    compact_name = re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff]+",
        "",
        str(name or "").casefold(),
    )
    if len(compact_name) < 4 or len(compact_text) < len(compact_name):
        return False
    limit = 2 if len(compact_name) >= 7 else 1
    width = len(compact_name)
    return any(
        bounded_edit_distance(
            compact_text[index:index + width],
            compact_name,
            limit,
        )
        <= limit
        for index in range(len(compact_text) - width + 1)
    )


def key_frame_grounding_errors(
    payload: dict[str, Any],
    frame_intelligence: dict[str, Any],
    *,
    tolerance_seconds: float = 3.0,
) -> list[str]:
    """要求每个名称在声称该名称的时间点附近被本地 OCR 再次命中。"""
    if frame_intelligence.get("status") != "captured":
        return ["关键界面 OCR 不可用，不能自动发布视频搭配"]
    frames = [
        row
        for row in normalized_list(frame_intelligence.get("frames"))
        if isinstance(row, dict)
    ]
    if not frames:
        return ["关键界面 OCR 没有生成可复核帧"]

    normalized = ensure_strategy_contract(payload)
    strategies = normalized.get("strategies") or []
    require_assignment = len(strategies) > 1
    errors = []
    for strategy in strategies:
        strategy_id = str(strategy.get("id") or "")
        label = str(strategy.get("label") or strategy_id)
        evidence_rows = strategy_evidence_rows(
            normalized,
            strategy_id,
            require_assignment=require_assignment,
        )
        for group in ("augments", "items", "runes"):
            for name in strategy_names(strategy.get(group)):
                variants = strategy_name_variants(group, name)
                name_evidence_rows = [
                    row
                    for row in evidence_rows
                    if claim_supports_strategy_name(
                        group,
                        name,
                        [str(row.get("claim") or "")],
                    )
                ]
                name_times = [
                    timestamp_seconds(row.get("timestamp"))
                    for row in name_evidence_rows
                ]
                name_times = [
                    value for value in name_times
                    if value is not None
                ]
                nearby_frames = [
                    frame
                    for frame in frames
                    if any(
                        abs(
                            float(frame.get("timestampSeconds") or 0)
                            - timestamp
                        )
                        <= tolerance_seconds
                        for timestamp in name_times
                    )
                ]
                exact_grounded = False
                fuzzy_frame_hits = 0
                for frame in nearby_frames:
                    vocabulary_names = set(
                        normalized_list(
                            (frame.get("vocabularyMatches") or {}).get(group)
                        )
                    )
                    ocr_text = str(frame.get("ocrText") or "")
                    if (
                        name in vocabulary_names
                        or any(
                            variant and variant in ocr_text
                            for variant in variants
                        )
                    ):
                        exact_grounded = True
                        break
                    if any(
                        conservative_ocr_fuzzy_contains(ocr_text, variant)
                        for variant in variants
                    ):
                        fuzzy_frame_hits += 1
                if not exact_grounded and fuzzy_frame_hits < 2:
                    errors.append(
                        f"{label}：{name} 未被同时间点关键画面 OCR 复核"
                    )
    return errors


class BiliNoteClient:
    def __init__(self, base_url: str, provider_id: str, model_name: str, timeout_seconds: int):
        self.base_url = base_url.rstrip("/")
        self.provider_id = provider_id
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def upload(self, local_path: Path, video_id: str) -> tuple[str, str]:
        suffix = local_path.suffix.lower() if local_path.suffix else ".mp4"
        filename = f"haidou-{video_id}-{uuid.uuid4().hex[:10]}{suffix}"
        response = post_file(self.base_url + "/upload", local_path, filename)
        if response.get("code") != 0:
            raise VideoPipelineError(f"BiliNote 拒绝视频上传: {response.get('msg')}")
        upload_url = str((response.get("data") or {}).get("url") or "")
        if upload_url != f"/uploads/{filename}":
            raise VideoPipelineError("BiliNote 返回了异常的上传路径")
        return upload_url, filename

    def submit(
        self,
        candidate: dict[str, Any],
        hero: dict[str, Any],
        current_patch: str,
        video_url: str,
        frame_intelligence: dict[str, Any] | None = None,
    ) -> str:
        frame_context = ""
        if frame_intelligence:
            from pipeline.frame_intelligence import prompt_context

            frame_context = prompt_context(frame_intelligence)
        payload = {
            "video_url": video_url,
            "platform": "local",
            "quality": "fast",
            "screenshot": True,
            "link": True,
            "model_name": self.model_name,
            "provider_id": self.provider_id,
            "format": ["link", "screenshot"],
            "style": "default",
            "extras": analysis_prompt(
                hero,
                current_patch,
                candidate.get("durationSeconds"),
            ) + frame_context,
            "video_understanding": True,
            "video_interval": 3,
            "grid_size": [2, 2],
        }
        response = post_json(self.base_url + "/generate_note", payload, {}, timeout=60)
        if response.get("code") != 0:
            raise VideoPipelineError(f"BiliNote 拒绝任务: {response.get('msg')}")
        task_id = ((response.get("data") or {}).get("task_id") or "").strip()
        if not task_id:
            raise VideoPipelineError("BiliNote 未返回 task_id")
        return task_id

    def wait(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        last_status = ""
        while time.monotonic() < deadline:
            response = get_json(self.base_url + f"/task_status/{urllib.parse.quote(task_id)}")
            if response.get("code") != 0:
                raise VideoPipelineError(f"BiliNote 分析失败: {response.get('msg')}")
            data = response.get("data") or {}
            status = data.get("status") or "PENDING"
            if status != last_status:
                print(f"BiliNote: {status} {data.get('message') or ''}".strip())
                last_status = status
            if status == "SUCCESS":
                result = data.get("result")
                if not isinstance(result, dict):
                    raise VideoPipelineError("BiliNote 完成但结果为空")
                return result
            if status == "FAILED":
                raise VideoPipelineError(f"BiliNote 分析失败: {data.get('message') or status}")
            time.sleep(4)
        raise VideoPipelineError(f"BiliNote 分析超时（{self.timeout_seconds} 秒）")


def find_candidate(path: Path, candidate_id: str) -> dict[str, Any]:
    for candidate in load_json(path).get("candidates", []):
        if candidate.get("id") == candidate_id or candidate.get("videoId") == candidate_id:
            return candidate
    raise VideoPipelineError(f"候选文件里没有 {candidate_id}")


def normalized_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def transcript_review_error(transcript_review: Any) -> str:
    """确认人工复核绑定的 BiliNote 原始转写没有缺失或漂移。"""
    if not isinstance(transcript_review, dict):
        return ""
    task_id = str(transcript_review.get("taskId") or "")
    expected_sha = str(transcript_review.get("sha256") or "")
    expected_segment = transcript_review.get("segment")
    if (
        not task_id
        or not expected_sha
        or not isinstance(expected_segment, dict)
    ):
        return "语音复核缺少原始转写绑定"
    note_results_dir, _ = bilinote_storage_paths()
    transcript_path = note_results_dir / f"{task_id}_transcript.json"
    if not transcript_path.is_file():
        return f"语音复核原始转写缺失 {task_id}"
    if hashlib.sha256(transcript_path.read_bytes()).hexdigest() != expected_sha:
        return f"语音复核原始转写哈希不匹配 {task_id}"
    try:
        transcript = load_json(transcript_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return f"语音复核原始转写无法读取 {task_id}"
    expected_start = expected_segment.get("start")
    expected_end = expected_segment.get("end")
    expected_text = str(expected_segment.get("text") or "")
    for segment in normalized_list(transcript.get("segments")):
        if not isinstance(segment, dict):
            continue
        if (
            segment.get("start") == expected_start
            and segment.get("end") == expected_end
            and str(segment.get("text") or "") == expected_text
        ):
            return ""
    return f"语音复核原始分段已漂移 {task_id}"


def apply_registered_evidence_reviews(
    payload: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """按原始转写或相邻画面复核结果纠正证据类型、时间点与公开结论。"""
    reviewed = json.loads(json.dumps(payload, ensure_ascii=False))
    if not EVIDENCE_REVIEWS_PATH.exists():
        return reviewed, None
    registry = load_json(EVIDENCE_REVIEWS_PATH)
    video_id = str(candidate.get("videoId") or "")
    video_review = (registry.get("videos") or {}).get(video_id)
    if not isinstance(video_review, dict):
        return reviewed, None

    registered = normalized_list(video_review.get("evidence"))
    errors = []
    applied = []
    used_indexes: set[int] = set()
    evidence = []
    for row in normalized_list(reviewed.get("evidence")):
        if not isinstance(row, dict):
            evidence.append(row)
            continue
        matching_indexes = []
        for index, entry in enumerate(registered):
            if not isinstance(entry, dict):
                continue
            source = entry.get("source") or {}
            if (
                isinstance(source, dict)
                and str(source.get("timestamp") or "")
                == str(row.get("timestamp") or "")
                and str(source.get("kind") or "")
                == str(row.get("kind") or "")
                and str(source.get("claim") or "")
                == str(row.get("claim") or "")
            ):
                matching_indexes.append(index)
        if not matching_indexes:
            evidence.append(row)
            continue
        if len(matching_indexes) > 1:
            errors.append(
                "证据复核登记重复 "
                f"{row.get('kind')} {row.get('timestamp')}"
            )
            evidence.append(row)
            continue
        index = matching_indexes[0]
        used_indexes.add(index)
        entry = registered[index]
        verdict = str(entry.get("verdict") or "")
        public = entry.get("public")
        if verdict not in {"supported", "partial", "unsupported"}:
            errors.append(
                "证据复核结论无效 "
                f"{row.get('kind')} {row.get('timestamp')}"
            )
            evidence.append(row)
            continue
        transcript_error = transcript_review_error(entry.get("transcript"))
        if transcript_error:
            errors.append(transcript_error)
            evidence.append(row)
            continue
        if isinstance(public, dict):
            corrected = {
                "timestamp": str(public.get("timestamp") or ""),
                "kind": str(public.get("kind") or ""),
                "claim": str(public.get("claim") or "").strip(),
            }
            if (
                timestamp_seconds(corrected["timestamp"]) is None
                or corrected["kind"] not in {"frame", "subtitle", "audio"}
                or not corrected["claim"]
            ):
                errors.append(
                    "证据复核公开结论无效 "
                    f"{row.get('kind')} {row.get('timestamp')}"
                )
                evidence.append(row)
                continue
            evidence.append(corrected)
        elif verdict != "unsupported":
            errors.append(
                "证据复核缺少安全公开结论 "
                f"{row.get('kind')} {row.get('timestamp')}"
            )
            evidence.append(row)
            continue
        applied.append({
            "sourceTimestamp": str(row.get("timestamp") or ""),
            "sourceKind": str(row.get("kind") or ""),
            "publicTimestamp": (
                str(public.get("timestamp") or "")
                if isinstance(public, dict)
                else ""
            ),
            "publicKind": (
                str(public.get("kind") or "")
                if isinstance(public, dict)
                else ""
            ),
            "verdict": verdict,
        })

    for index, entry in enumerate(registered):
        if index in used_indexes:
            continue
        source = entry.get("source") if isinstance(entry, dict) else {}
        errors.append(
            "证据复核登记的原结论不存在 "
            f"{(source or {}).get('kind', '')} "
            f"{(source or {}).get('timestamp', '')}".strip()
        )
    reviewed["evidence"] = evidence
    return reviewed, {
        "status": "applied" if not errors else "invalid",
        "reviewedAt": str(registry.get("reviewedAt") or ""),
        "reviewType": str(registry.get("reviewType") or ""),
        "reviewedEvidence": applied,
        "errors": errors,
    }


def apply_registered_frame_reviews(
    payload: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """把已逐像素复核的帧结论作为可审计覆盖层应用到发布投影。"""
    reviewed = json.loads(json.dumps(payload, ensure_ascii=False))
    if not FRAME_REVIEWS_PATH.exists():
        return reviewed, None
    registry = load_json(FRAME_REVIEWS_PATH)
    video_id = str(candidate.get("videoId") or "")
    video_review = (registry.get("videos") or {}).get(video_id)
    if not isinstance(video_review, dict):
        return reviewed, None

    registered_frames = video_review.get("frames") or {}
    if not isinstance(registered_frames, dict):
        registered_frames = {}
    errors = []
    applied_frames = []
    source_frame_timestamps = set()
    evidence = []
    for row in normalized_list(reviewed.get("evidence")):
        if not isinstance(row, dict) or row.get("kind") != "frame":
            evidence.append(row)
            continue
        timestamp = str(row.get("timestamp") or "")
        source_frame_timestamps.add(timestamp)
        frame_review = registered_frames.get(timestamp)
        if not isinstance(frame_review, dict):
            errors.append(f"逐帧复核缺少原分析画面 {timestamp}")
            evidence.append(row)
            continue
        source_claim = str(frame_review.get("sourceClaim") or "")
        if str(row.get("claim") or "") != source_claim:
            errors.append(f"逐帧复核原结论已漂移 {timestamp}")
            evidence.append(row)
            continue
        verdict = str(frame_review.get("verdict") or "")
        if verdict not in {"supported", "partial", "unsupported"}:
            errors.append(f"逐帧复核结论无效 {timestamp}")
            evidence.append(row)
            continue
        public_claim = str(frame_review.get("publicClaim") or "").strip()
        if public_claim:
            corrected = dict(row)
            corrected["claim"] = public_claim
            evidence.append(corrected)
        elif verdict != "unsupported":
            errors.append(f"逐帧复核缺少安全公开结论 {timestamp}")
        applied_frames.append({
            "timestamp": timestamp,
            "verdict": verdict,
            "cachedPath": str(frame_review.get("cachedPath") or ""),
            "screenshotSha256": str(frame_review.get("screenshotSha256") or ""),
        })

    for timestamp in sorted(set(registered_frames) - source_frame_timestamps):
        errors.append(f"逐帧复核登记了原分析不存在的画面 {timestamp}")
    reviewed["evidence"] = evidence

    strategy = reviewed.get("strategy")
    overrides = video_review.get("strategyOverrides") or {}
    if overrides:
        if not isinstance(strategy, dict) or not isinstance(overrides, dict):
            errors.append("逐帧复核策略覆盖格式无效")
        else:
            for group, rows in overrides.items():
                if group not in EVIDENCE_STRATEGY_KEYS or not isinstance(rows, list):
                    errors.append(f"逐帧复核策略覆盖无效: {group}")
                    continue
                strategy[group] = json.loads(json.dumps(rows, ensure_ascii=False))

    return reviewed, {
        "status": "applied" if not errors else "invalid",
        "reviewedAt": str(registry.get("reviewedAt") or ""),
        "reviewType": str(registry.get("reviewType") or ""),
        "reviewedFrames": applied_frames,
        "errors": errors,
    }


def attach_frame_review_metadata(
    record: dict[str, Any],
    frame_review: dict[str, Any] | None,
) -> None:
    if not frame_review:
        return
    record["frameReview"] = {
        "status": (
            "pixel-reviewed"
            if frame_review.get("status") == "applied"
            else "invalid"
        ),
        "reviewedAt": frame_review.get("reviewedAt") or "",
        "reviewType": frame_review.get("reviewType") or "",
        "reviewedFrames": len(normalized_list(frame_review.get("reviewedFrames"))),
    }


def attach_evidence_review_metadata(
    record: dict[str, Any],
    evidence_review: dict[str, Any] | None,
) -> None:
    if not evidence_review:
        return
    record["evidenceReview"] = {
        "status": (
            "source-reviewed"
            if evidence_review.get("status") == "applied"
            else "invalid"
        ),
        "reviewedAt": evidence_review.get("reviewedAt") or "",
        "reviewType": evidence_review.get("reviewType") or "",
        "reviewedEvidence": len(
            normalized_list(evidence_review.get("reviewedEvidence"))
        ),
    }


def grounded_catalog_strategy(strategy: dict[str, Any]) -> dict[str, Any]:
    def named_rows(group: str) -> list[dict[str, str]]:
        return [{"name": name} for name in strategy_names(strategy.get(group))]

    return {
        "id": str(strategy.get("id") or "build-1"),
        "label": str(strategy.get("label") or "主方案"),
        "augments": named_rows("augments"),
        "items": named_rows("items"),
        "runes": named_rows("runes"),
        "skillOrder": strategy_names(strategy.get("skillOrder")),
        "summonerSpells": strategy_names(strategy.get("summonerSpells")),
        "playstyle": [],
    }


def grounded_catalog_summary(strategies: list[dict[str, Any]]) -> str:
    supported = []
    for strategy in strategies:
        groups = (
            ("强化", strategy_names(strategy.get("augments"))),
            ("装备", strategy_names(strategy.get("items"))),
            ("符文", strategy_names(strategy.get("runes"))),
            ("加点", strategy_names(strategy.get("skillOrder"))),
            ("召唤师技能", strategy_names(strategy.get("summonerSpells"))),
        )
        details = [
            f"{label}：{'、'.join(names)}"
            for label, names in groups
            if names
        ]
        if details:
            if len(strategies) == 1:
                supported.append("；".join(details))
            else:
                label = str(strategy.get("label") or "主方案")
                supported.append(f"{label}（{'；'.join(details)}）")
    if not supported:
        return "当前时间戳证据没有可逐项发布的搭配结论。"
    return (
        "时间戳证据已明确记录"
        + "；".join(supported)
        + "。未显示的打法、理由和版本适用性不作推断。"
    )


def grounded_catalog_evidence(
    candidate: dict[str, Any],
    evidence: Any,
    strategies: list[dict[str, Any]],
    *,
    partial: bool,
) -> list[dict[str, str]]:
    rows = [
        row
        for row in normalized_list(evidence)
        if isinstance(row, dict)
    ]
    if not partial:
        return rows
    hero_name = str(candidate.get("heroName") or candidate.get("hero") or "").strip()
    hero_alias = str(candidate.get("hero") or "").strip()
    public_groups = (
        ("augments", "强化"),
        ("items", "装备"),
        ("runes", "符文"),
        ("skillOrder", "加点"),
        ("summonerSpells", "召唤师技能"),
    )
    projected = []
    for row in rows:
        claim = str(row.get("claim") or "")
        labels = []
        if any(
            term and term.casefold() in claim.casefold()
            for term in (hero_name, hero_alias)
        ):
            labels.append(f"目标英雄：{hero_name}")
        evidence_strategy_id = str(row.get("strategyId") or "").strip()
        for strategy in strategies:
            strategy_id = str(strategy.get("id") or "")
            if evidence_strategy_id and evidence_strategy_id != strategy_id:
                continue
            for group, label in public_groups:
                supported = [
                    name
                    for name in strategy_names(strategy.get(group))
                    if claim_supports_strategy_name(group, name, [claim])
                ]
                if supported:
                    labels.append(
                        f"{strategy.get('label') or strategy_id}·"
                        f"{label}：{'、'.join(supported)}"
                    )
        projected_row = {
            "timestamp": str(row.get("timestamp") or ""),
            "kind": str(row.get("kind") or ""),
            "claim": (
                "；".join(dict.fromkeys(labels))
                if labels
                else "该时间点已核对，未形成可公开的搭配结论"
            ),
        }
        if evidence_strategy_id:
            projected_row["strategyId"] = evidence_strategy_id
        projected.append(projected_row)
    return projected


def evidence_coverage_label(evidence: Any) -> str:
    """只声明正式记录实际公开了哪些证据类型，不把处理流程冒充结论依据。"""
    rows = normalized_list(evidence)
    labels = [
        label
        for kind, label in (
            ("frame", "画面"),
            ("subtitle", "字幕"),
            ("audio", "语音"),
        )
        if any(
            isinstance(row, dict) and row.get("kind") == kind
            for row in rows
        )
    ]
    return "AI 提炼 · 证据：" + "、".join(labels)


def catalog_record(
    candidate: dict[str, Any],
    analysis: dict[str, Any],
    current_patch: str,
    max_age_days: int = DEFAULT_RECENT_DAYS,
    projection_omissions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    strategies = analysis_strategies(analysis)
    public_strategies = [
        grounded_catalog_strategy(strategy)
        for strategy in strategies
    ]
    public_strategy = public_strategies[0] if public_strategies else {}
    headline_names = (
        [
            name
            for strategy in public_strategies
            for name in (
                strategy_names(strategy.get("augments"))
                + strategy_names(strategy.get("items"))
                + strategy_names(strategy.get("runes"))
            )
        ]
    )
    hero_name = str(candidate.get("heroName") or candidate["hero"]).strip()
    patch_mentioned = str(analysis.get("patchMentioned") or "").strip()
    evidence = grounded_catalog_evidence(
        candidate,
        analysis.get("evidence"),
        public_strategies,
        partial=bool(projection_omissions),
    )
    has_patch_evidence = bool(patch_mentioned) and any(
        patch_mentioned in str(row.get("claim") or "")
        for row in evidence
        if isinstance(row, dict)
    )
    patch_status = (
        "current"
        if patch_mentioned == current_patch and has_patch_evidence
        else "needs-game-check"
    )
    patch_impact = patch_impact_for(candidate, current_patch)
    confidence = round(float(analysis.get("confidence") or 0), 3)
    raw_caveat = str(analysis.get("caveat") or "").strip()
    caveat = (
        "视频中其他识别结果未通过当前词典或逐项时间戳校验，已省略。"
        if projection_omissions
        else raw_caveat
    )
    if patch_status != "current":
        caveat = (caveat + " " if caveat else "") + f"视频未明确证明适用于站点客户端资料快照 {current_patch}。"
    if patch_impact:
        caveat = (caveat + " " if caveat else "") + patch_impact["summary"]
    record = {
        "id": candidate["id"],
        "platform": "抖音",
        "url": candidate["url"],
        "heroes": [candidate["hero"]],
        "title": (
            f"{hero_name} 视频证据：{headline_names[0]}"
            if headline_names
            else f"{hero_name} 视频证据搭配"
        ),
        "creator": candidate.get("creator") or "未知作者",
        "publishedAt": candidate.get("publishedAt") or "",
        "expiresAt": video_publication_expiry(
            candidate.get("publishedAt"),
            max_age_days,
        ),
        "durationSeconds": candidate.get("durationSeconds") or 0,
        "discoveredAt": today(),
        "analysisStatus": "multimodal-reviewed",
        "analysisLabel": evidence_coverage_label(evidence),
        "reviewedAt": today(),
        "patchStatus": patch_status,
        "patchMentioned": patch_mentioned or None,
        "summary": grounded_catalog_summary(public_strategies),
        "strategy": public_strategy,
        "strategies": public_strategies,
        "evidence": evidence,
        "confidence": confidence,
        "engagement": candidate.get("engagement") or {},
        "caveat": caveat,
    }
    if patch_impact:
        record["patchImpact"] = patch_impact
    return record


def collect_key_frame_intelligence(
    local_video: Path,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """视觉预扫描失败不阻断旧链路，但必须留下明确的不可用状态。"""
    try:
        from pipeline.frame_intelligence import analyze_video_frames

        output_dir = (
            CACHE_DIR
            / "key_frames"
            / str(candidate.get("videoId") or "unknown")
        )
        manifest = analyze_video_frames(local_video, output_dir)
        save_json(output_dir / "manifest.json", manifest)
        return manifest
    except (
        ImportError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as error:
        return {
            "schemaVersion": 1,
            "status": "unavailable",
            "error": safe_error_text(error),
        }


def analyze(args: argparse.Namespace) -> Path:
    candidates_path = Path(args.candidates)
    candidate = find_candidate(candidates_path, args.candidate_id)
    hero = hero_by_alias(candidate["hero"])
    current_patch = load_json(INDEX_PATH).get("patch", {}).get("game") or ""
    base_url = os.getenv("BILINOTE_API_BASE", args.bilinote_base)
    provider_id = os.getenv("BILINOTE_PROVIDER_ID", args.provider_id)
    model_name = os.getenv("BILINOTE_MODEL_NAME", args.model_name)
    if not provider_id or not model_name:
        raise VideoPipelineError("缺少 BILINOTE_PROVIDER_ID 或 BILINOTE_MODEL_NAME")
    client = BiliNoteClient(base_url, provider_id, model_name, args.timeout)
    temporary_video: Path | None = None
    uploaded_filename = ""
    frame_intelligence: dict[str, Any] = {
        "schemaVersion": 1,
        "status": "not-run",
    }
    try:
        if args.local_video:
            local_video = Path(args.local_video).expanduser().resolve()
            if not local_video.is_file():
                raise VideoPipelineError(f"本地测试视频不存在: {local_video}")
        else:
            tikhub = TikHubClient(
                token=os.getenv("TIKHUB_TOKEN", ""),
                base_url=os.getenv("TIKHUB_API_BASE", args.tikhub_base),
                min_interval=float(getattr(args, "request_interval", 1.05)),
            )
            media_url = tikhub.resolve_media_url(candidate["videoId"])
            temporary_video = download_video(media_url)
            local_video = temporary_video
        frame_intelligence = collect_key_frame_intelligence(
            local_video,
            candidate,
        )
        upload_url, uploaded_filename = client.upload(local_video, candidate["videoId"])
        task_id = client.submit(
            candidate,
            hero,
            current_patch,
            upload_url,
            frame_intelligence,
        )
        print(f"BiliNote 任务已提交: {task_id}")
        result = client.wait(task_id)
    finally:
        if temporary_video:
            temporary_video.unlink(missing_ok=True)
        if uploaded_filename:
            cleanup_bilinote_upload(uploaded_filename)
    markdown = str(result.get("markdown") or "")
    analysis_payload, normalizations = normalize_analysis_names(extract_json_block(markdown))
    analysis_payload = attach_key_frame_identity_evidence(
        analysis_payload,
        frame_intelligence,
        hero,
    )
    evidence_reviewed_analysis, evidence_review = apply_registered_evidence_reviews(
        analysis_payload,
        candidate,
    )
    reviewed_analysis, frame_review = apply_registered_frame_reviews(
        evidence_reviewed_analysis,
        candidate,
    )
    publication_projection, projection_omissions = project_grounded_analysis(
        reviewed_analysis
    )
    errors = [
        f"候选不适合发布: {reason}"
        for reason in candidate_rejection_reasons(
            candidate,
            hero,
            max_age_days=int(getattr(args, "max_age_days", DEFAULT_RECENT_DAYS)),
            max_duration_seconds=int(getattr(args, "max_duration_seconds", 600)),
        )
    ]
    errors.extend(
        validate_analysis(
            publication_projection,
            hero["alias"],
            current_patch,
            candidate.get("durationSeconds"),
        )
    )
    errors.extend(
        key_frame_grounding_errors(
            publication_projection,
            frame_intelligence,
        )
    )
    if frame_review:
        errors.extend(normalized_list(frame_review.get("errors")))
    if evidence_review:
        errors.extend(normalized_list(evidence_review.get("errors")))
    record = catalog_record(
        candidate,
        publication_projection,
        current_patch,
        int(getattr(args, "max_age_days", DEFAULT_RECENT_DAYS)),
        projection_omissions,
    )
    attach_frame_review_metadata(record, frame_review)
    attach_evidence_review_metadata(record, evidence_review)
    draft = {
        "schemaVersion": 1,
        "analysisContractVersion": ANALYSIS_CONTRACT_VERSION,
        "candidate": candidate,
        "analysis": analysis_payload,
        "publicationProjection": publication_projection,
        "projectionOmissions": projection_omissions,
        "catalogRecord": record,
        "qualityGate": {
            "passed": not errors and record["confidence"] >= args.min_confidence,
            "minimumConfidence": args.min_confidence,
            "errors": errors,
        },
        "normalizations": normalizations,
        "evidenceReview": evidence_review,
        "frameReview": frame_review,
        "keyFrameIntelligence": frame_intelligence,
        "bilinote": {
            "taskId": task_id,
            "videoUnderstanding": True,
            "transport": "temporary-local-upload",
        },
    }
    output = Path(args.output) if args.output else CACHE_DIR / f"draft-{candidate['videoId']}.json"
    raw_output = output.with_name(output.stem + "-bilinote.md")
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.write_text(markdown, encoding="utf-8")
    draft = attach_bilinote_screenshot_manifest(
        draft,
        output,
        processed_markdown=markdown,
    )
    draft = attach_evidence_screenshot_manifest(draft)
    draft = apply_screenshot_manifest_gate(draft)
    save_json(output, draft)
    if draft["qualityGate"]["passed"]:
        print(f"分析通过质量闸门，草稿写入 {output}")
    else:
        print(f"分析未通过质量闸门，草稿保留但不会发布: {errors or ['置信度不足']}")
    return output


def publish(args: argparse.Namespace) -> None:
    draft = load_json(Path(args.draft))
    gate = draft.get("qualityGate") or {}
    if not gate.get("passed") and not args.human_verified:
        raise VideoPipelineError(f"草稿未通过质量闸门: {gate.get('errors') or '置信度不足'}")
    record = draft.get("catalogRecord")
    if not isinstance(record, dict):
        raise VideoPipelineError("草稿缺少 catalogRecord")
    if args.human_verified:
        record["analysisStatus"] = "human-verified"
        record["analysisLabel"] = "人工已核对画面与结论"
        record["humanVerifiedAt"] = today()
    if record.get("analysisStatus") not in ALLOWED_STATUSES:
        raise VideoPipelineError("草稿核对状态无效")
    catalog = load_json(CATALOG_PATH) if CATALOG_PATH.exists() else {"schemaVersion": 2, "videos": []}
    rows = [row for row in catalog.get("videos", []) if row.get("id") != record.get("id")]
    rows.append(record)
    rows.sort(key=lambda row: (row.get("publishedAt") or "", row.get("id") or ""), reverse=True)
    catalog["schemaVersion"] = 2
    catalog["updatedAt"] = today()
    catalog["videos"] = rows
    save_json(CATALOG_PATH, catalog)
    print(f"已发布结构化视频记录: {record['id']}")
    if not args.no_rebuild:
        subprocess.run([sys.executable, str(ROOT / "pipeline" / "build_site_data.py")], check=True)
        subprocess.run([sys.executable, str(ROOT / "pipeline" / "validate_site_data.py")], check=True)


def replace_catalog_for_heroes(
    records: list[dict[str, Any]],
    target_aliases: set[str],
    *,
    max_per_hero: int = 3,
) -> dict[str, Any]:
    previous = (
        load_json(CATALOG_PATH)
        if CATALOG_PATH.exists()
        else {"schemaVersion": 2, "videos": []}
    )
    rows = [
        row
        for row in previous.get("videos", [])
        if target_aliases.isdisjoint(set(row.get("heroes", [])))
    ]
    by_hero: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        for alias in record.get("heroes", []):
            by_hero.setdefault(alias, []).append(record)
    selected_ids = set()
    for hero_records in by_hero.values():
        hero_records.sort(
            key=lambda row: (
                row.get("publishedAt") or "",
                row.get("confidence") or 0,
                row.get("id") or "",
            ),
            reverse=True,
        )
        for record in hero_records[:max(1, max_per_hero)]:
            record_id = record.get("id")
            if record_id in selected_ids:
                continue
            selected_ids.add(record_id)
            rows.append(record)
    rows.sort(key=lambda row: (row.get("publishedAt") or "", row.get("id") or ""), reverse=True)
    save_json(CATALOG_PATH, {
        "schemaVersion": 2,
        "updatedAt": today(),
        "videos": rows,
    })
    return previous


def catalog_record_hero_aliases(
    records: list[dict[str, Any]],
) -> set[str]:
    """Return unique hero aliases represented by published video records."""
    return {
        alias
        for record in records
        for alias in record.get("heroes", [])
        if isinstance(alias, str) and alias
    }


def revalidate_cached_draft(
    draft: dict[str, Any],
    candidate: dict[str, Any],
    *,
    current_patch: str,
    min_confidence: float,
    max_age_days: int,
    max_duration_seconds: int,
) -> dict[str, Any]:
    """复用多模态结果，但始终按当前词典与质量规则重新判定。"""
    hero = hero_by_alias(candidate["hero"])
    analysis, new_normalizations = normalize_analysis_names(draft.get("analysis") or {})
    analysis = attach_key_frame_identity_evidence(
        analysis,
        draft.get("keyFrameIntelligence") or {},
        hero,
    )
    evidence_reviewed_analysis, evidence_review = apply_registered_evidence_reviews(
        analysis,
        candidate,
    )
    reviewed_analysis, frame_review = apply_registered_frame_reviews(
        evidence_reviewed_analysis,
        candidate,
    )
    publication_projection, projection_omissions = project_grounded_analysis(
        reviewed_analysis
    )
    errors = [
        f"候选不适合发布: {reason}"
        for reason in candidate_rejection_reasons(
            candidate,
            hero,
            max_age_days=max_age_days,
            max_duration_seconds=max_duration_seconds,
        )
    ]
    errors.extend(
        validate_analysis(
            publication_projection,
            hero["alias"],
            current_patch,
            candidate.get("durationSeconds"),
        )
    )
    errors.extend(
        key_frame_grounding_errors(
            publication_projection,
            draft.get("keyFrameIntelligence") or {},
        )
    )
    if frame_review:
        errors.extend(normalized_list(frame_review.get("errors")))
    if evidence_review:
        errors.extend(normalized_list(evidence_review.get("errors")))
    record = catalog_record(
        candidate,
        publication_projection,
        current_patch,
        max_age_days,
        projection_omissions,
    )
    attach_frame_review_metadata(record, frame_review)
    attach_evidence_review_metadata(record, evidence_review)
    previous_normalizations = normalized_list(draft.get("normalizations"))
    normalizations = []
    for row in [*previous_normalizations, *new_normalizations]:
        if isinstance(row, dict) and row not in normalizations:
            normalizations.append(row)
    updated = dict(draft)
    updated.update({
        "candidate": candidate,
        "analysis": analysis,
        "publicationProjection": publication_projection,
        "projectionOmissions": projection_omissions,
        "catalogRecord": record,
        "qualityGate": {
            "passed": not errors and record["confidence"] >= min_confidence,
            "minimumConfidence": min_confidence,
            "errors": errors,
        },
        "normalizations": normalizations,
        "evidenceReview": evidence_review,
        "frameReview": frame_review,
    })
    return updated


def refresh(args: argparse.Namespace) -> None:
    """一次完成发现、分析与可选发布；支持每位英雄保留多条独立视频。"""
    all_heroes = bool(getattr(args, "all_heroes", False))
    videos_per_hero = max(1, int(getattr(args, "videos_per_hero", 3)))
    heroes_value = args.heroes
    if all_heroes:
        heroes_value = ",".join(hero["alias"] for hero in hero_rows())
    candidates_output = args.candidates_output
    if all_heroes and not candidates_output:
        candidates_output = str(CACHE_DIR / "candidates-all-heroes.json")
    skip_discovery = bool(getattr(args, "skip_discovery", False))
    if skip_discovery:
        if not candidates_output:
            raise VideoPipelineError("--skip-discovery 必须提供 --candidates-output")
        candidates_path = Path(candidates_output)
        if not candidates_path.is_file():
            raise VideoPipelineError(f"候选断点不存在: {candidates_path}")
    else:
        discovery_args = argparse.Namespace(
            heroes=heroes_value,
            all_heroes=all_heroes,
            refresh_limit=args.refresh_limit,
            limit_per_hero=max(
                videos_per_hero,
                int(getattr(args, "limit_per_hero", videos_per_hero)),
            ),
            sorts=args.sorts,
            publish_time=args.publish_time,
            tikhub_base=args.tikhub_base,
            output=candidates_output,
            max_age_days=args.max_age_days,
            max_duration_seconds=args.max_duration_seconds,
            fallback_queries=args.fallback_queries,
            resume=args.resume,
            request_interval=args.request_interval,
            max_search_requests=args.max_search_requests,
        )
        candidates_path = discover(discovery_args)
    candidates_payload = load_json(candidates_path)
    candidates = candidates_payload.get("candidates", [])
    discovery_run = candidates_payload.get("lastRun") or {}
    search_requests_added = (
        0
        if skip_discovery
        else int(discovery_run.get("searchRequestsAdded") or 0)
    )
    checkpoint_search_requests = int(
        discovery_run.get("checkpointSearchRequestsTotal") or 0
    )
    existing_ids = {
        row.get("id") for row in (
            load_json(CATALOG_PATH).get("videos", []) if CATALOG_PATH.exists() else []
        )
    }
    selected = []
    selected_hero_counts: dict[str, int] = {}
    candidates.sort(
        key=lambda row: (
            row.get("publishedAt") or "",
            row.get("candidateScore") or 0,
        ),
        reverse=True,
    )
    configured_max_videos = int(getattr(args, "max_videos", 0) or 0)
    max_videos = (
        configured_max_videos
        if configured_max_videos > 0
        else (
            len(hero_rows()) * videos_per_hero
            if all_heroes
            else 5
        )
    )
    for candidate in candidates:
        candidate_hero = str(candidate.get("hero") or "")
        if (
            not all_heroes
            and candidate.get("id") in existing_ids
        ) or selected_hero_counts.get(candidate_hero, 0) >= videos_per_hero:
            continue
        selected.append(candidate)
        selected_hero_counts[candidate_hero] = (
            selected_hero_counts.get(candidate_hero, 0) + 1
        )
        if len(selected) >= max_videos:
            break
    if not selected:
        print("本轮没有发现尚未处理的新视频")
        if all_heroes and args.publish:
            previous = replace_catalog_for_heroes(
                [],
                {hero["alias"] for hero in hero_rows()},
                max_per_hero=videos_per_hero,
            )
            try:
                subprocess.run([sys.executable, str(ROOT / "pipeline" / "build_site_data.py")], check=True)
                subprocess.run([sys.executable, str(ROOT / "pipeline" / "validate_site_data.py")], check=True)
            except subprocess.CalledProcessError:
                save_json(CATALOG_PATH, previous)
                raise
        return

    passed_records = []
    rejected = []
    failed = []
    new_analysis_attempts = 0
    cached_drafts_revalidated = 0
    screenshot_manifests_captured = 0
    screenshot_frames_captured = 0
    screenshot_manifests_without_frames = 0
    screenshot_manifests_unavailable = 0
    current_patch = load_json(INDEX_PATH).get("patch", {}).get("game") or ""
    for index, candidate in enumerate(selected, 1):
        try:
            draft_path = (
                CACHE_DIR
                / (
                    f"draft-{str(candidate.get('hero') or '').lower()}-"
                    f"{candidate['videoId']}-v{ANALYSIS_CONTRACT_VERSION}.json"
                )
            )
            output_draft_path = draft_path
            reuse = False
            legacy_draft_path = draft_path.with_name(
                draft_path.name.replace(
                    f"-v{ANALYSIS_CONTRACT_VERSION}.json",
                    "-v4.json",
                )
            )
            if (
                args.resume
                and not draft_path.exists()
                and legacy_draft_path.exists()
            ):
                draft_path = legacy_draft_path
            if args.resume and draft_path.exists():
                cached = load_json(draft_path)
                reuse = (
                    cached.get("analysisContractVersion")
                    in {4, ANALYSIS_CONTRACT_VERSION}
                    and (cached.get("candidate") or {}).get("id") == candidate.get("id")
                )
            if reuse:
                cached_drafts_revalidated += 1
            else:
                new_analysis_attempts += 1
                draft_path = analyze(argparse.Namespace(
                    candidates=str(candidates_path),
                    candidate_id=candidate["id"],
                    bilinote_base=args.bilinote_base,
                    provider_id=args.provider_id,
                    model_name=args.model_name,
                    timeout=args.timeout,
                    min_confidence=args.min_confidence,
                    local_video=None,
                    tikhub_base=args.tikhub_base,
                    output=str(draft_path),
                    max_age_days=args.max_age_days,
                    max_duration_seconds=args.max_duration_seconds,
                    request_interval=args.request_interval,
                ))
            cached_draft = attach_bilinote_screenshot_manifest(
                load_json(draft_path),
                output_draft_path,
            )
            cached_draft = attach_bilinote_frame_intelligence(cached_draft)
            draft = revalidate_cached_draft(
                cached_draft,
                candidate,
                current_patch=current_patch,
                min_confidence=args.min_confidence,
                max_age_days=args.max_age_days,
                max_duration_seconds=args.max_duration_seconds,
            )
            draft = attach_evidence_screenshot_manifest(draft)
            draft = apply_screenshot_manifest_gate(draft)
            draft["analysisContractVersion"] = ANALYSIS_CONTRACT_VERSION
            manifest = persisted_screenshot_manifest(draft)
            manifest_status = manifest.get("status")
            if manifest_status == "captured":
                screenshot_manifests_captured += 1
                screenshot_frames_captured += len(normalized_list(manifest.get("frames")))
            elif manifest_status == "no-screenshots":
                screenshot_manifests_without_frames += 1
            else:
                screenshot_manifests_unavailable += 1
            save_json(output_draft_path, draft)
            if (draft.get("qualityGate") or {}).get("passed"):
                passed_records.append(draft["catalogRecord"])
                status = "通过"
            else:
                rejected.append({
                    "hero": candidate.get("hero"),
                    "videoId": candidate.get("videoId"),
                    "errors": (draft.get("qualityGate") or {}).get("errors", []),
                })
                status = "拒绝"
            print(f"分析进度 {index}/{len(selected)}: {candidate.get('hero')} {status}")
        except (VideoPipelineError, subprocess.CalledProcessError) as error:
            safe_error = safe_error_text(error)
            failed.append({
                "hero": candidate.get("hero"),
                "videoId": candidate.get("videoId"),
                "error": safe_error,
            })
            print(f"跳过 {candidate['id']}: {safe_error}")

    if args.publish:
        target_aliases = (
            {hero["alias"] for hero in hero_rows()}
            if all_heroes
            else {record["heroes"][0] for record in passed_records}
        )
        previous = replace_catalog_for_heroes(
            passed_records,
            target_aliases,
            max_per_hero=videos_per_hero,
        )
        try:
            subprocess.run([sys.executable, str(ROOT / "pipeline" / "build_site_data.py")], check=True)
            subprocess.run([sys.executable, str(ROOT / "pipeline" / "validate_site_data.py")], check=True)
        except subprocess.CalledProcessError:
            save_json(CATALOG_PATH, previous)
            subprocess.run([sys.executable, str(ROOT / "pipeline" / "build_site_data.py")], check=True)
            raise

    target_count = (
        len(hero_rows())
        if all_heroes
        else len(selected_hero_counts)
    )
    passed_hero_aliases = catalog_record_hero_aliases(passed_records)
    fallback_to_opgg = max(0, target_count - len(passed_hero_aliases))
    summary_path = CACHE_DIR / (
        "all-heroes-run-summary.json"
        if all_heroes
        else f"run-summary-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    run_mode = (
        "fresh"
        if not args.resume
        else (
            "resume-cache-only"
            if search_requests_added == 0 and new_analysis_attempts == 0
            else "resume-with-external-work"
        )
    )
    save_json(summary_path, {
        "schemaVersion": 2,
        "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "runMode": run_mode,
        "externalSearchRequestsAdded": search_requests_added,
        "newAnalysisAttempts": new_analysis_attempts,
        "cachedDraftsRevalidated": cached_drafts_revalidated,
        "screenshotManifestsCaptured": screenshot_manifests_captured,
        "screenshotFramesCaptured": screenshot_frames_captured,
        "screenshotManifestsWithoutFrames": screenshot_manifests_without_frames,
        "screenshotManifestsUnavailable": screenshot_manifests_unavailable,
        "checkpointSearchRequestsTotal": checkpoint_search_requests,
        "estimatedSearchCostUpperBoundUsdThisRun": round(
            search_requests_added * TIKHUB_PUBLIC_PRICE_RANGE_USD[1],
            4,
        ),
        "costEstimate": {
            "scope": "TikHub search requests added during this run only",
            "excludes": [
                "TikHub media-detail resolution",
                "local BiliNote processing",
            ],
            "publicRangeUsdPerRequest": list(TIKHUB_PUBLIC_PRICE_RANGE_USD),
            "estimateType": "public-range-upper-bound-before-discounts",
            "sourceUrl": TIKHUB_PRICING_URL,
            "checkedAt": today(),
        },
        "targetHeroes": target_count,
        "candidateHeroes": len(selected_hero_counts),
        "passedHeroes": len(passed_hero_aliases),
        "fallbackToOpgg": fallback_to_opgg,
        "passed": [
            {"hero": record["heroes"][0], "videoId": record["id"].removeprefix("douyin-")}
            for record in passed_records
        ],
        "rejected": rejected,
        "failed": failed,
        "publishedLocally": bool(args.publish),
        "deployed": False,
    })
    print(
        f"本轮完成: 候选 {len(selected)} 条，通过 {len(passed_records)} 条，"
        f"覆盖 {len(passed_hero_aliases)} 位英雄，"
        f"回退 OP.GG {fallback_to_opgg} 位，失败 {len(failed)} 条；"
        f"TikHub 搜索新增 {search_requests_added} 次，"
        f"新分析 {new_analysis_attempts} 条，"
        f"缓存重判 {cached_drafts_revalidated} 条，"
        f"截图清单 {screenshot_manifests_captured} 条/"
        f"{screenshot_frames_captured} 帧"
    )
    if failed:
        raise VideoPipelineError(f"{len(failed)} 条基础设施失败，详见 {summary_path}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="海斗速查抖音视频情报管线")
    commands = root.add_subparsers(dest="command", required=True)

    discover_parser = commands.add_parser("discover", help="通过 TikHub 搜索候选抖音视频")
    discover_parser.add_argument("--heroes", help="逗号分隔的 Data Dragon alias；省略时自动选最久未更新英雄")
    discover_parser.add_argument("--all-heroes", action="store_true", help="覆盖站内全部英雄")
    discover_parser.add_argument("--refresh-limit", type=int, default=5, help="自动选择的英雄数量")
    discover_parser.add_argument("--limit-per-hero", type=int, default=6)
    discover_parser.add_argument("--sorts", default="2,1", help="TikHub 排序：2=最新，1=最多点赞")
    discover_parser.add_argument("--publish-time", default="180", choices=("1", "7", "180", "0"))
    discover_parser.add_argument("--max-age-days", type=int, default=DEFAULT_RECENT_DAYS)
    discover_parser.add_argument("--max-duration-seconds", type=int, default=600)
    discover_parser.add_argument("--fallback-queries", action="store_true", help="首个名称无近期攻略时才查昵称")
    discover_parser.add_argument("--resume", action="store_true", help="复用逐英雄保存的候选断点")
    discover_parser.add_argument("--request-interval", type=float, default=1.05)
    discover_parser.add_argument("--max-search-requests", type=int, default=0, help="0 表示不设硬上限")
    discover_parser.add_argument("--tikhub-base", default="https://api.tikhub.io")
    discover_parser.add_argument("--output")
    discover_parser.set_defaults(handler=discover)

    analyze_parser = commands.add_parser("analyze", help="把候选视频交给本地 BiliNote 多模态分析")
    analyze_parser.add_argument("--candidates", required=True)
    analyze_parser.add_argument("--candidate-id", required=True)
    analyze_parser.add_argument("--bilinote-base", default="http://127.0.0.1:8483/api")
    analyze_parser.add_argument("--provider-id", default="")
    analyze_parser.add_argument("--model-name", default="")
    analyze_parser.add_argument("--timeout", type=int, default=1200)
    analyze_parser.add_argument("--min-confidence", type=float, default=0.68)
    analyze_parser.add_argument("--max-age-days", type=int, default=DEFAULT_RECENT_DAYS)
    analyze_parser.add_argument("--max-duration-seconds", type=int, default=600)
    analyze_parser.add_argument("--request-interval", type=float, default=1.05)
    analyze_parser.add_argument("--local-video", help="仅用于本地链路验证；正常运行时由 TikHub 临时下载")
    analyze_parser.add_argument("--tikhub-base", default="https://api.tikhub.io")
    analyze_parser.add_argument("--output")
    analyze_parser.set_defaults(handler=analyze)

    publish_parser = commands.add_parser("publish", help="把通过质量闸门的草稿写入网站")
    publish_parser.add_argument("--draft", required=True)
    publish_parser.add_argument(
        "--human-verified",
        action="store_true",
        help="人工逐项核对后发布；这是未通过自动质量闸门时唯一允许的覆盖方式",
    )
    publish_parser.add_argument("--no-rebuild", action="store_true")
    publish_parser.set_defaults(handler=publish)

    refresh_parser = commands.add_parser("refresh", help="发现、分析并按质量闸门批量刷新")
    refresh_parser.add_argument("--heroes", help="逗号分隔的 Data Dragon alias")
    refresh_parser.add_argument("--all-heroes", action="store_true", help="处理全部英雄并为无合格视频者回退 OP.GG")
    refresh_parser.add_argument("--refresh-limit", type=int, default=5)
    refresh_parser.add_argument("--limit-per-hero", type=int, default=4)
    refresh_parser.add_argument(
        "--videos-per-hero",
        type=int,
        default=3,
        help="每位英雄最多分析并保留的视频数；每条视频仍可包含多套独立流派",
    )
    refresh_parser.add_argument(
        "--max-videos",
        type=int,
        default=0,
        help="本轮视频硬上限；0 表示普通刷新 5 条、全英雄刷新 英雄数×videos-per-hero",
    )
    refresh_parser.add_argument("--sorts", default="2,1")
    refresh_parser.add_argument("--publish-time", default="180", choices=("1", "7", "180", "0"))
    refresh_parser.add_argument("--max-age-days", type=int, default=DEFAULT_RECENT_DAYS)
    refresh_parser.add_argument("--max-duration-seconds", type=int, default=600)
    refresh_parser.add_argument("--fallback-queries", action="store_true")
    refresh_parser.add_argument("--resume", action="store_true")
    refresh_parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="只分析已有 --candidates-output 断点，不发出任何 TikHub 搜索请求",
    )
    refresh_parser.add_argument("--request-interval", type=float, default=1.05)
    refresh_parser.add_argument("--max-search-requests", type=int, default=0)
    refresh_parser.add_argument("--tikhub-base", default="https://api.tikhub.io")
    refresh_parser.add_argument("--bilinote-base", default="http://127.0.0.1:8483/api")
    refresh_parser.add_argument("--provider-id", default="")
    refresh_parser.add_argument("--model-name", default="")
    refresh_parser.add_argument("--timeout", type=int, default=1200)
    refresh_parser.add_argument("--min-confidence", type=float, default=0.68)
    refresh_parser.add_argument("--candidates-output")
    refresh_parser.add_argument("--publish", action="store_true", help="通过闸门后写入网站；默认只生成草稿")
    refresh_parser.set_defaults(handler=refresh)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        args.handler(args)
    except VideoPipelineError as error:
        raise SystemExit(f"视频管线失败: {error}") from error


if __name__ == "__main__":
    main()
