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
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "site" / "data" / "index.json"
CATALOG_PATH = ROOT / "data" / "videos" / "catalog.json"
CACHE_DIR = ROOT / "data" / "cache" / "video_intelligence"
TIKHUB_SEARCH_PATH = "/api/v1/douyin/search/fetch_multi_search"

ALLOWED_STATUSES = {
    "metadata-only",
    "multimodal-reviewed",
    "visual-reviewed",  # 兼容首条人工抽帧记录
    "human-verified",
}
STRATEGY_KEYS = ("augments", "items", "runes", "skillOrder", "playstyle")
GUIDE_TERMS = ("海克斯", "海斗", "大乱斗", "强化", "符文", "出装", "攻略", "玩法")


class VideoPipelineError(RuntimeError):
    """可向操作者直接展示的流程错误。"""


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


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


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 45) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")[:500]
        raise VideoPipelineError(f"请求失败 HTTP {error.code}: {message}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise VideoPipelineError(f"请求失败: {error}") from error


def get_json(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")[:500]
        raise VideoPipelineError(f"读取任务失败 HTTP {error.code}: {message}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise VideoPipelineError(f"读取任务失败: {error}") from error


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
    def __init__(self, token: str, base_url: str):
        if not token:
            raise VideoPipelineError("缺少 TIKHUB_TOKEN，尚不能自动搜索抖音")
        self.token = token
        self.base_url = base_url.rstrip("/")

    def search(self, keyword: str, sort_type: str, publish_time: str) -> list[dict[str, Any]]:
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
        response = post_json(
            self.base_url + TIKHUB_SEARCH_PATH,
            payload,
            {"Authorization": f"Bearer {self.token}"},
        )
        if response.get("code") not in (None, 0, 200):
            raise VideoPipelineError(f"TikHub 返回错误: {response.get('message_zh') or response.get('message')}")
        data = response.get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        rows = data.get("business_data") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []


def discover(args: argparse.Namespace) -> Path:
    aliases = [value.strip() for value in (args.heroes or "").split(",") if value.strip()]
    if not aliases:
        aliases = select_refresh_heroes(args.refresh_limit)
    client = TikHubClient(
        token=os.getenv("TIKHUB_TOKEN", ""),
        base_url=os.getenv("TIKHUB_API_BASE", args.tikhub_base),
    )
    all_candidates: dict[str, dict[str, Any]] = {}
    query_log: list[dict[str, Any]] = []
    for alias in aliases:
        hero = hero_by_alias(alias)
        for query in hero_queries(hero):
            for sort_type in args.sorts.split(","):
                sort_type = sort_type.strip()
                if sort_type not in {"0", "1", "2"}:
                    raise VideoPipelineError(f"无效 sort_type: {sort_type}")
                rows = client.search(query, sort_type, args.publish_time)
                query_log.append({
                    "hero": alias,
                    "query": query,
                    "sortType": sort_type,
                    "resultCount": len(rows),
                })
                for row in rows:
                    candidate = normalize_candidate(row, hero, query)
                    if not candidate:
                        continue
                    existing = all_candidates.get(candidate["id"])
                    if not existing or candidate["candidateScore"] > existing["candidateScore"]:
                        all_candidates[candidate["id"]] = candidate

    candidates = sorted(
        all_candidates.values(),
        key=lambda row: (row["candidateScore"], row.get("publishedAt") or ""),
        reverse=True,
    )
    per_hero: dict[str, int] = {}
    selected = []
    for candidate in candidates:
        alias = candidate["hero"]
        if per_hero.get(alias, 0) >= args.limit_per_hero:
            continue
        per_hero[alias] = per_hero.get(alias, 0) + 1
        selected.append(candidate)
    payload = {
        "schemaVersion": 1,
        "discoveredAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "queries": query_log,
        "candidates": selected,
    }
    output = Path(args.output) if args.output else (
        CACHE_DIR / f"candidates-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    save_json(output, payload)
    print(f"发现完成: {len(aliases)} 位英雄、{len(selected)} 条候选，结果写入 {output}")
    return output


def analysis_prompt(hero: dict[str, Any], current_patch: str) -> str:
    return f"""
你正在为 LOL 海克斯大乱斗攻略站核对一条抖音视频。目标英雄是 {hero['name']}（alias={hero['alias']}），
站点当前游戏补丁是 {current_patch}。

必须同时利用视频画面、字幕和语音；不要只复述标题。视频未明确展示或说明的内容必须留空，禁止猜测。
特别检查：强化符文、装备及购买顺序、符文天赋、技能加点、召唤师技能、打法条件。
每个关键结论都要给出视频时间戳，并标明证据来自 frame、subtitle 或 audio。
若是娱乐剪辑、纯战绩展示或信息不足，confidence 必须低于 0.65。

在笔记末尾输出且只输出一个 ```json 代码块，结构严格如下：
{{
  "schemaVersion": 1,
  "hero": "{hero['alias']}",
  "title": "对攻略内容的准确标题",
  "summary": "一到两句话总结作者主张",
  "patchMentioned": null,
  "strategy": {{
    "augments": [{{"name": "强化名", "priority": "核心或可选", "reason": "作者给出的理由"}}],
    "items": [{{"name": "装备名", "order": 1, "reason": "作者给出的理由"}}],
    "runes": ["符文名"],
    "skillOrder": ["Q", "E", "W"],
    "summonerSpells": ["闪现", "雪球"],
    "playstyle": ["可执行打法"]
  }},
  "evidence": [
    {{"timestamp": "00:12", "kind": "frame", "claim": "画面实际支持的结论"}}
  ],
  "confidence": 0.0,
  "caveat": "版本、样本或条件限制"
}}
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
        if isinstance(payload, dict) and payload.get("schemaVersion") == 1:
            return payload
    raise VideoPipelineError("BiliNote 结果里没有找到合格的 JSON 分析块")


def timestamp_seconds(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    match = re.fullmatch(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})|(\d{1,2}):(\d{2})", str(value or "").strip())
    if not match:
        return None
    if match.group(4) is not None:
        return int(match.group(4)) * 60 + int(match.group(5))
    return int(match.group(1) or 0) * 3600 + int(match.group(2)) * 60 + int(match.group(3))


def validate_analysis(payload: dict[str, Any], expected_alias: str) -> list[str]:
    errors = []
    if payload.get("hero") != expected_alias:
        errors.append(f"英雄不匹配: expected={expected_alias} actual={payload.get('hero')}")
    if not str(payload.get("summary") or "").strip():
        errors.append("缺少攻略摘要")
    strategy = payload.get("strategy")
    if not isinstance(strategy, dict) or not any(strategy.get(key) for key in STRATEGY_KEYS):
        errors.append("没有提取到任何强化、出装、符文、加点或打法")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 2:
        errors.append("至少需要两条带时间戳的证据")
    else:
        has_frame = False
        for index, row in enumerate(evidence):
            if not isinstance(row, dict):
                errors.append(f"证据 {index + 1} 格式无效")
                continue
            if timestamp_seconds(row.get("timestamp")) is None:
                errors.append(f"证据 {index + 1} 缺少有效时间戳")
            if row.get("kind") not in {"frame", "subtitle", "audio"}:
                errors.append(f"证据 {index + 1} kind 无效")
            has_frame = has_frame or row.get("kind") == "frame"
            if not str(row.get("claim") or "").strip():
                errors.append(f"证据 {index + 1} 缺少结论")
        if not has_frame:
            errors.append("缺少画面证据，不能标记为多模态已读")
    try:
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError):
        confidence = -1
    if not 0 <= confidence <= 1:
        errors.append("confidence 必须在 0 到 1 之间")
    return errors


class BiliNoteClient:
    def __init__(self, base_url: str, provider_id: str, model_name: str, timeout_seconds: int):
        self.base_url = base_url.rstrip("/")
        self.provider_id = provider_id
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def submit(self, candidate: dict[str, Any], hero: dict[str, Any], current_patch: str) -> str:
        payload = {
            "video_url": candidate["url"],
            "platform": "douyin",
            "quality": "fast",
            "screenshot": True,
            "link": True,
            "model_name": self.model_name,
            "provider_id": self.provider_id,
            "format": ["link", "screenshot"],
            "style": "default",
            "extras": analysis_prompt(hero, current_patch),
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


def catalog_record(candidate: dict[str, Any], analysis: dict[str, Any], current_patch: str) -> dict[str, Any]:
    strategy = analysis.get("strategy") or {}
    patch_mentioned = str(analysis.get("patchMentioned") or "").strip()
    patch_status = "current" if patch_mentioned and patch_mentioned == current_patch else "needs-game-check"
    confidence = round(float(analysis.get("confidence") or 0), 3)
    caveat = str(analysis.get("caveat") or "").strip()
    if patch_status != "current":
        caveat = (caveat + " " if caveat else "") + f"视频未明确证明适用于当前 {current_patch} 版本。"
    return {
        "id": candidate["id"],
        "platform": "抖音",
        "url": candidate["url"],
        "heroes": [candidate["hero"]],
        "title": str(analysis.get("title") or candidate.get("title") or "").strip(),
        "creator": candidate.get("creator") or "未知作者",
        "publishedAt": candidate.get("publishedAt") or "",
        "durationSeconds": candidate.get("durationSeconds") or 0,
        "discoveredAt": today(),
        "analysisStatus": "multimodal-reviewed",
        "analysisLabel": "AI 已看画面、字幕与语音",
        "reviewedAt": today(),
        "patchStatus": patch_status,
        "patchMentioned": patch_mentioned or None,
        "summary": str(analysis.get("summary") or "").strip(),
        "strategy": {
            "augments": normalized_list(strategy.get("augments")),
            "items": normalized_list(strategy.get("items")),
            "runes": normalized_list(strategy.get("runes")),
            "skillOrder": normalized_list(strategy.get("skillOrder")),
            "summonerSpells": normalized_list(strategy.get("summonerSpells")),
            "playstyle": normalized_list(strategy.get("playstyle")),
        },
        "evidence": normalized_list(analysis.get("evidence")),
        "confidence": confidence,
        "engagement": candidate.get("engagement") or {},
        "caveat": caveat,
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
    task_id = client.submit(candidate, hero, current_patch)
    print(f"BiliNote 任务已提交: {task_id}")
    result = client.wait(task_id)
    markdown = str(result.get("markdown") or "")
    analysis_payload = extract_json_block(markdown)
    errors = validate_analysis(analysis_payload, hero["alias"])
    record = catalog_record(candidate, analysis_payload, current_patch)
    draft = {
        "schemaVersion": 1,
        "candidate": candidate,
        "analysis": analysis_payload,
        "catalogRecord": record,
        "qualityGate": {
            "passed": not errors and record["confidence"] >= args.min_confidence,
            "minimumConfidence": args.min_confidence,
            "errors": errors,
        },
        "bilinote": {
            "taskId": task_id,
            "videoUnderstanding": True,
        },
    }
    output = Path(args.output) if args.output else CACHE_DIR / f"draft-{candidate['videoId']}.json"
    save_json(output, draft)
    raw_output = output.with_name(output.stem + "-bilinote.md")
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.write_text(markdown, encoding="utf-8")
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


def refresh(args: argparse.Namespace) -> None:
    """一次完成发现、分析与可选发布；每位英雄最多处理一条新视频。"""
    discovery_args = argparse.Namespace(
        heroes=args.heroes,
        refresh_limit=args.refresh_limit,
        limit_per_hero=max(2, args.limit_per_hero),
        sorts=args.sorts,
        publish_time=args.publish_time,
        tikhub_base=args.tikhub_base,
        output=args.candidates_output,
    )
    candidates_path = discover(discovery_args)
    candidates = load_json(candidates_path).get("candidates", [])
    existing_ids = {
        row.get("id") for row in (
            load_json(CATALOG_PATH).get("videos", []) if CATALOG_PATH.exists() else []
        )
    }
    selected = []
    selected_heroes = set()
    for candidate in candidates:
        if candidate.get("id") in existing_ids or candidate.get("hero") in selected_heroes:
            continue
        selected.append(candidate)
        selected_heroes.add(candidate.get("hero"))
        if len(selected) >= args.max_videos:
            break
    if not selected:
        print("本轮没有发现尚未处理的新视频")
        return

    published = 0
    failed = []
    for candidate in selected:
        try:
            draft_path = analyze(argparse.Namespace(
                candidates=str(candidates_path),
                candidate_id=candidate["id"],
                bilinote_base=args.bilinote_base,
                provider_id=args.provider_id,
                model_name=args.model_name,
                timeout=args.timeout,
                min_confidence=args.min_confidence,
                output=None,
            ))
            draft = load_json(draft_path)
            if args.publish and (draft.get("qualityGate") or {}).get("passed"):
                publish(argparse.Namespace(
                    draft=str(draft_path),
                    human_verified=False,
                    no_rebuild=True,
                ))
                published += 1
        except (VideoPipelineError, subprocess.CalledProcessError) as error:
            failed.append(f"{candidate['id']}: {error}")
            print(f"跳过 {candidate['id']}: {error}")

    if published:
        subprocess.run([sys.executable, str(ROOT / "pipeline" / "build_site_data.py")], check=True)
        subprocess.run([sys.executable, str(ROOT / "pipeline" / "validate_site_data.py")], check=True)
    print(f"本轮完成: 分析 {len(selected)} 条，发布 {published} 条，失败 {len(failed)} 条")
    if failed:
        raise VideoPipelineError("；".join(failed))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="海斗速查抖音视频情报管线")
    commands = root.add_subparsers(dest="command", required=True)

    discover_parser = commands.add_parser("discover", help="通过 TikHub 搜索候选抖音视频")
    discover_parser.add_argument("--heroes", help="逗号分隔的 Data Dragon alias；省略时自动选最久未更新英雄")
    discover_parser.add_argument("--refresh-limit", type=int, default=5, help="自动选择的英雄数量")
    discover_parser.add_argument("--limit-per-hero", type=int, default=6)
    discover_parser.add_argument("--sorts", default="2,1", help="TikHub 排序：2=最新，1=最多点赞")
    discover_parser.add_argument("--publish-time", default="180", choices=("1", "7", "180", "0"))
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
    refresh_parser.add_argument("--refresh-limit", type=int, default=5)
    refresh_parser.add_argument("--limit-per-hero", type=int, default=4)
    refresh_parser.add_argument("--max-videos", type=int, default=5)
    refresh_parser.add_argument("--sorts", default="2,1")
    refresh_parser.add_argument("--publish-time", default="180", choices=("1", "7", "180", "0"))
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
