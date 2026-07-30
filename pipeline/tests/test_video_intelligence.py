import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "video_intelligence.py"
SPEC = importlib.util.spec_from_file_location("video_intelligence", MODULE_PATH)
video_intelligence = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(video_intelligence)


class VideoIntelligenceTests(unittest.TestCase):
    def test_catalog_record_hero_aliases_counts_multiple_videos_once(self):
        aliases = video_intelligence.catalog_record_hero_aliases([
            {"id": "douyin-1", "heroes": ["TahmKench"]},
            {"id": "douyin-2", "heroes": ["TahmKench"]},
            {"id": "douyin-3", "heroes": ["Teemo"]},
        ])

        self.assertEqual(aliases, {"TahmKench", "Teemo"})

    def test_safe_error_text_redacts_echoed_authorization(self):
        with mock.patch.dict(
            os.environ,
            {"TIKHUB_TOKEN": "secret-token-value"},
            clear=False,
        ):
            message = video_intelligence.safe_error_text(
                '{"headers":{"Authorization":"Bearer secret-token-value"}}'
            )
        self.assertNotIn("secret-token-value", message)
        self.assertIn("[REDACTED]", message)

    def test_post_json_uses_browser_compatible_user_agent(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"code":200}'
        with mock.patch.object(
            video_intelligence.urllib.request,
            "urlopen",
            return_value=response,
        ) as urlopen:
            payload = video_intelligence.post_json(
                "https://api.example.test/search",
                {"keyword": "test"},
                {"Authorization": "Bearer hidden"},
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(payload["code"], 200)
        self.assertEqual(
            request.get_header("User-agent"),
            video_intelligence.HTTP_USER_AGENT,
        )

    def test_post_json_retries_one_incomplete_response(self):
        incomplete = mock.MagicMock()
        incomplete.__enter__.return_value.read.side_effect = (
            video_intelligence.http.client.IncompleteRead(b'{"code":', 5)
        )
        complete = mock.MagicMock()
        complete.__enter__.return_value.read.return_value = b'{"code":200}'
        with mock.patch.object(
            video_intelligence.urllib.request,
            "urlopen",
            side_effect=(incomplete, complete),
        ) as urlopen:
            payload = video_intelligence.post_json(
                "https://api.example.test/search",
                {"keyword": "test"},
                {"Authorization": "Bearer hidden"},
            )
        self.assertEqual(payload["code"], 200)
        self.assertEqual(urlopen.call_count, 2)

    def test_post_json_retries_one_truncated_json_response(self):
        truncated = mock.MagicMock()
        truncated.__enter__.return_value.read.return_value = b'{"code":200,"data":"unfinished'
        complete = mock.MagicMock()
        complete.__enter__.return_value.read.return_value = b'{"code":200}'
        with mock.patch.object(
            video_intelligence.urllib.request,
            "urlopen",
            side_effect=(truncated, complete),
        ) as urlopen:
            payload = video_intelligence.post_json(
                "https://api.example.test/search",
                {"keyword": "test"},
                {"Authorization": "Bearer hidden"},
            )
        self.assertEqual(payload["code"], 200)
        self.assertEqual(urlopen.call_count, 2)

    def test_curl_transport_keeps_token_out_of_process_arguments(self):
        completed = mock.Mock(returncode=0, stdout='{"code":200}', stderr="")
        with mock.patch.object(video_intelligence.shutil, "which", return_value="/usr/bin/curl"):
            with mock.patch.object(
                video_intelligence.subprocess,
                "run",
                return_value=completed,
            ) as run:
                payload = video_intelligence.post_json_curl(
                    "https://api.example.test/search",
                    {"keyword": "test"},
                    {"Authorization": "Bearer hidden-token"},
                )
        command = run.call_args.args[0]
        config = run.call_args.kwargs["input"]
        self.assertEqual(payload["code"], 200)
        self.assertNotIn("hidden-token", " ".join(command))
        self.assertIn("hidden-token", config.decode("utf-8"))

    def test_curl_transport_retries_one_truncated_json_response(self):
        truncated = mock.Mock(returncode=0, stdout='{"code":200', stderr="")
        complete = mock.Mock(returncode=0, stdout='{"code":200}', stderr="")
        with mock.patch.object(video_intelligence.shutil, "which", return_value="/usr/bin/curl"):
            with mock.patch.object(
                video_intelligence.subprocess,
                "run",
                side_effect=(truncated, complete),
            ) as run:
                payload = video_intelligence.post_json_curl(
                    "https://api.example.test/search",
                    {"keyword": "test"},
                    {"Authorization": "Bearer hidden-token"},
                )
        self.assertEqual(payload["code"], 200)
        self.assertEqual(run.call_count, 2)

    def test_curl_transport_retries_one_truncated_utf8_response(self):
        truncated = mock.Mock(returncode=0, stdout=b'{"name":"\\xe6', stderr=b"")
        complete = mock.Mock(returncode=0, stdout=b'{"code":200}', stderr=b"")
        with mock.patch.object(video_intelligence.shutil, "which", return_value="/usr/bin/curl"):
            with mock.patch.object(
                video_intelligence.subprocess,
                "run",
                side_effect=(truncated, complete),
            ) as run:
                payload = video_intelligence.post_json_curl(
                    "https://api.example.test/search",
                    {"keyword": "test"},
                    {"Authorization": "Bearer hidden-token"},
                )
        self.assertEqual(payload["code"], 200)
        self.assertEqual(run.call_count, 2)

    def test_hero_queries_uses_name_and_nickname(self):
        hero = {
            "name": "厄运小姐",
            "alias": "MissFortune",
            "epithet": "赏金猎人",
            "search": "好运姐,女枪,厄运小姐,赏金猎人",
        }
        queries = video_intelligence.hero_queries(hero)
        self.assertEqual(queries[0], "厄运小姐 海克斯大乱斗")
        self.assertEqual(len(queries), 2)
        self.assertIn("海克斯大乱斗", queries[1])

    def test_candidate_filter_requires_recent_hero_tutorial(self):
        hero = {
            "name": "凯尔",
            "alias": "Kayle",
            "epithet": "正义天使",
            "search": "天使,凯尔,正义天使",
        }
        candidate = {
            "title": "凯尔海克斯大乱斗出装攻略",
            "publishedAt": dt.date.today().isoformat(),
            "durationSeconds": 120,
        }
        self.assertEqual(
            video_intelligence.candidate_rejection_reasons(candidate, hero, 45),
            [],
        )
        candidate["publishedAt"] = (dt.date.today() - dt.timedelta(days=46)).isoformat()
        self.assertIn(
            "视频超过 45 天",
            video_intelligence.candidate_rejection_reasons(candidate, hero, 45),
        )
        candidate["publishedAt"] = dt.date.today().isoformat()
        candidate["title"] = "凯尔海克斯大乱斗五杀集锦"
        self.assertIn(
            "标题不像攻略或教学",
            video_intelligence.candidate_rejection_reasons(candidate, hero, 45),
        )
        self.assertIn(
            "标题明确偏娱乐或集锦",
            video_intelligence.candidate_rejection_reasons(candidate, hero, 45),
        )

    def test_video_publication_window_expires_after_cutoff(self):
        video = {"publishedAt": "2026-06-01"}
        self.assertTrue(
            video_intelligence.video_is_within_publication_window(
                video,
                dt.date(2026, 7, 16),
            )
        )
        self.assertFalse(
            video_intelligence.video_is_within_publication_window(
                video,
                dt.date(2026, 7, 17),
            )
        )

    def test_discover_uses_nickname_only_as_fallback(self):
        hero = {
            "name": "凯尔",
            "alias": "Kayle",
            "epithet": "正义天使",
            "search": "天使,凯尔,正义天使",
        }
        row = {
            "aweme_id": "7667230871719529747",
            "desc": "凯尔海克斯大乱斗出装攻略",
            "create_time": int(dt.datetime.now().timestamp()),
            "author": {"nickname": "攻略作者"},
            "statistics": {"digg_count": 20},
            "video": {"duration": 120000},
        }
        client = mock.Mock()
        client.search.return_value = [row]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidates.json"
            args = argparse.Namespace(
                heroes="Kayle",
                all_heroes=False,
                refresh_limit=1,
                limit_per_hero=1,
                sorts="2",
                publish_time="180",
                max_age_days=45,
                max_duration_seconds=600,
                fallback_queries=True,
                resume=False,
                request_interval=0,
                max_search_requests=2,
                tikhub_base="https://api.example.test",
                output=str(output),
            )
            with mock.patch.object(video_intelligence, "hero_by_alias", return_value=hero):
                with mock.patch.object(video_intelligence, "TikHubClient", return_value=client):
                    with mock.patch("builtins.print"):
                        video_intelligence.discover(args)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(client.search.call_count, 1)
        self.assertEqual(payload["completedHeroes"], ["Kayle"])
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["lastRun"]["searchRequestsAdded"], 1)
        self.assertEqual(
            payload["lastRun"]["estimatedSearchCostUpperBoundUsd"],
            0.01,
        )

    def test_discover_resume_reports_zero_new_requests_without_client(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidates.json"
            output.write_text(
                json.dumps({
                    "schemaVersion": 2,
                    "config": {
                        "maxAgeDays": 45,
                        "maxDurationSeconds": 600,
                        "sorts": "2",
                        "publishTime": "180",
                        "fallbackQueries": True,
                    },
                    "completedHeroes": ["Kayle"],
                    "queries": [{"hero": "Kayle", "query": "历史查询"}],
                    "candidates": [],
                }),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                heroes="Kayle",
                all_heroes=False,
                refresh_limit=1,
                limit_per_hero=1,
                sorts="2",
                publish_time="180",
                max_age_days=45,
                max_duration_seconds=600,
                fallback_queries=True,
                resume=True,
                request_interval=0,
                max_search_requests=1,
                tikhub_base="https://api.example.test",
                output=str(output),
            )
            with mock.patch.object(video_intelligence, "TikHubClient") as client:
                with mock.patch("builtins.print") as printer:
                    video_intelligence.discover(args)
            payload = json.loads(output.read_text(encoding="utf-8"))

        client.assert_not_called()
        self.assertEqual(payload["lastRun"]["mode"], "resume")
        self.assertEqual(payload["lastRun"]["searchRequestsAdded"], 0)
        self.assertEqual(
            payload["lastRun"]["checkpointSearchRequestsTotal"],
            1,
        )
        self.assertEqual(
            payload["lastRun"]["estimatedSearchCostUpperBoundUsd"],
            0,
        )
        self.assertIn("本轮新增搜索 0 次", printer.call_args.args[0])
        self.assertIn("断点累计 1 次", printer.call_args.args[0])

    def test_resume_search_limit_counts_only_requests_added_this_run(self):
        hero = {
            "name": "凯尔",
            "alias": "Kayle",
            "epithet": "正义天使",
            "search": "天使,凯尔,正义天使",
        }
        row = {
            "aweme_id": "7667230871719529747",
            "desc": "凯尔海克斯大乱斗出装攻略",
            "create_time": int(dt.datetime.now().timestamp()),
            "author": {"nickname": "攻略作者"},
            "statistics": {"digg_count": 20},
            "video": {"duration": 120000},
        }
        client = mock.Mock()
        client.search.return_value = [row]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidates.json"
            output.write_text(
                json.dumps({
                    "schemaVersion": 2,
                    "config": {
                        "maxAgeDays": 45,
                        "maxDurationSeconds": 600,
                        "sorts": "2",
                        "publishTime": "180",
                        "fallbackQueries": True,
                    },
                    "completedHeroes": [],
                    "queries": [{"hero": "Other", "query": "历史查询"}],
                    "candidates": [],
                }),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                heroes="Kayle",
                all_heroes=False,
                refresh_limit=1,
                limit_per_hero=1,
                sorts="2",
                publish_time="180",
                max_age_days=45,
                max_duration_seconds=600,
                fallback_queries=True,
                resume=True,
                request_interval=0,
                max_search_requests=1,
                tikhub_base="https://api.example.test",
                output=str(output),
            )
            with mock.patch.object(
                video_intelligence,
                "hero_by_alias",
                return_value=hero,
            ):
                with mock.patch.object(
                    video_intelligence,
                    "TikHubClient",
                    return_value=client,
                ):
                    with mock.patch("builtins.print"):
                        video_intelligence.discover(args)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(client.search.call_count, 1)
        self.assertEqual(payload["lastRun"]["searchRequestsAdded"], 1)
        self.assertEqual(
            payload["lastRun"]["checkpointSearchRequestsTotal"],
            2,
        )

    def test_discover_budget_cap_returns_checkpoint_instead_of_aborting_batch(self):
        hero = {
            "name": "凯尔",
            "alias": "Kayle",
            "epithet": "正义天使",
            "search": "天使,凯尔,正义天使",
        }
        client = mock.Mock()
        client.search.return_value = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidates.json"
            args = argparse.Namespace(
                heroes="Kayle",
                all_heroes=False,
                refresh_limit=1,
                limit_per_hero=3,
                sorts="2,1",
                publish_time="180",
                max_age_days=45,
                max_duration_seconds=600,
                fallback_queries=False,
                resume=False,
                request_interval=0,
                max_search_requests=1,
                tikhub_base="https://api.example.test",
                output=str(output),
            )
            with mock.patch.object(
                video_intelligence,
                "hero_by_alias",
                return_value=hero,
            ), mock.patch.object(
                video_intelligence,
                "TikHubClient",
                return_value=client,
            ), mock.patch("builtins.print"):
                video_intelligence.discover(args)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(client.search.call_count, 1)
        self.assertEqual(payload["completedHeroes"], [])
        self.assertEqual(payload["lastRun"]["status"], "budget-exhausted")
        self.assertEqual(payload["lastRun"]["searchRequestsAdded"], 1)

    def test_refresh_skip_discovery_uses_existing_checkpoint_without_search(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "candidates.json"
            checkpoint.write_text(
                json.dumps({
                    "schemaVersion": 3,
                    "candidates": [],
                    "lastRun": {
                        "searchRequestsAdded": 389,
                        "checkpointSearchRequestsTotal": 443,
                    },
                }),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                all_heroes=False,
                heroes="Kayle",
                candidates_output=str(checkpoint),
                skip_discovery=True,
                videos_per_hero=3,
                refresh_limit=1,
                limit_per_hero=4,
                sorts="2,1",
                publish_time="180",
                tikhub_base="https://api.example.test",
                max_age_days=45,
                max_duration_seconds=600,
                fallback_queries=True,
                resume=True,
                request_interval=0,
                max_search_requests=0,
                max_videos=5,
                publish=False,
            )
            with mock.patch.object(video_intelligence, "discover") as discover:
                with mock.patch("builtins.print"):
                    video_intelligence.refresh(args)
        discover.assert_not_called()

    def test_normalize_candidate_drops_signed_media_url(self):
        row = {
            "data": json.dumps({
                "aweme_info": {
                    "aweme_id": "7602122505302088881",
                    "desc": "机器人海克斯大乱斗攻略",
                    "create_time": 1769990400,
                    "share_url": "https://www.douyin.com/video/7602122505302088881",
                    "author": {"nickname": "测试作者"},
                    "statistics": {"digg_count": 1234},
                    "video": {
                        "duration": 67000,
                        "play_addr": {"url_list": ["https://signed.example/video.mp4"]},
                    },
                }
            }, ensure_ascii=False)
        }
        hero = {"alias": "Blitzcrank", "name": "蒸汽机器人"}
        candidate = video_intelligence.normalize_candidate(row, hero, "蒸汽机器人 海克斯大乱斗")
        self.assertEqual(candidate["id"], "douyin-7602122505302088881")
        self.assertEqual(candidate["durationSeconds"], 67)
        self.assertNotIn("playUrl", candidate)
        self.assertNotIn("play_addr", json.dumps(candidate))

    def test_normalize_candidate_replaces_untrusted_share_url(self):
        row = {
            "aweme_id": "7602122505302088881",
            "desc": "机器人攻略",
            "share_url": "https://example.com/internal-resource",
        }
        hero = {"alias": "Blitzcrank", "name": "蒸汽机器人"}
        candidate = video_intelligence.normalize_candidate(row, hero, "蒸汽机器人 海克斯大乱斗")
        self.assertEqual(
            candidate["url"],
            "https://www.douyin.com/video/7602122505302088881",
        )

    def test_detail_media_url_prefers_h264_without_persisting_it(self):
        response = {
            "data": {
                "aweme_detail": {
                    "video": {
                        "play_addr_h264": {
                            "url_list": ["https://video.example/temporary.mp4"]
                        },
                        "play_addr": {"url_list": ["https://video.example/fallback.mp4"]},
                    }
                }
            }
        }
        self.assertEqual(
            video_intelligence.TikHubClient._media_url(response),
            "https://video.example/temporary.mp4",
        )

    def test_search_rows_flattens_current_tikhub_grouped_response(self):
        response = {
            "code": 200,
            "data": [
                {"business_data": [{"data_id": "1"}, {"data_id": "2"}]},
                {"business_data": [{"data_id": "3"}]},
                {"render_info": []},
            ],
        }
        rows = video_intelligence.TikHubClient._search_rows(response)
        self.assertEqual([row["data_id"] for row in rows], ["1", "2", "3"])

    def test_load_local_env_does_not_override_process_environment(self):
        key = "HAIDOU_TEST_ENV_KEY"
        previous = os.environ.get(key)
        try:
            os.environ[key] = "process-value"
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ".env"
                path.write_text(f"{key}=file-value\nINVALID KEY=nope\n", encoding="utf-8")
                video_intelligence.load_local_env(path)
            self.assertEqual(os.environ[key], "process-value")
            self.assertNotIn("INVALID KEY", os.environ)
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    def test_extract_json_block_prefers_last_schema_block(self):
        markdown = """
        ```json
        {"ignored": true}
        ```
        结果：
        ```json
        {"schemaVersion": 1, "hero": "Blitzcrank", "summary": "测试"}
        ```
        """
        payload = video_intelligence.extract_json_block(markdown)
        self.assertEqual(payload["hero"], "Blitzcrank")

    def test_validate_analysis_requires_frame_and_timestamp(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Blitzcrank",
            "summary": "核心强化配合技能急速。",
            "strategy": {"augments": [{"name": "虚幻武器"}]},
            "evidence": [
                {
                    "timestamp": "00:12",
                    "kind": "subtitle",
                    "claim": "字幕提到虚幻武器强化",
                },
                {"timestamp": "00:21", "kind": "audio", "claim": "语音解释装备"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Blitzcrank")
        self.assertIn("缺少画面证据，不能标记为多模态已读", errors)
        payload["evidence"].append(
            {
                "timestamp": "00:25",
                "kind": "frame",
                "claim": "画面展示布里茨的装备栏",
            }
        )
        self.assertEqual(video_intelligence.validate_analysis(payload, "Blitzcrank"), [])

    def test_validate_analysis_requires_timestamped_hero_identity(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Mel",
            "title": "梅尔玻璃大炮玩法",
            "summary": "标题与模型字段都声称目标是梅尔。",
            "strategy": {"augments": [{"name": "玻璃大炮"}]},
            "evidence": [
                {
                    "timestamp": "00:00",
                    "kind": "frame",
                    "claim": "画面选择了玻璃大炮",
                },
                {
                    "timestamp": "00:06",
                    "kind": "audio",
                    "claim": "口播解释强化效果",
                },
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Mel")
        self.assertIn("英雄身份缺少时间戳证据: 梅尔（Mel）", errors)
        payload["evidence"][0]["claim"] = "画面显示梅尔选择了玻璃大炮"
        self.assertEqual(video_intelligence.validate_analysis(payload, "Mel"), [])

    def test_validate_analysis_rejects_evidence_at_video_end(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Kayn",
            "summary": "视频展示了凯隐的牙仙子强化。",
            "strategy": {"augments": [{"name": "牙仙子"}]},
            "evidence": [
                {
                    "timestamp": "00:09",
                    "kind": "frame",
                    "claim": "画面展示凯隐的牙仙子强化",
                },
                {
                    "timestamp": "00:10",
                    "kind": "audio",
                    "claim": "口播提到牙仙子强化",
                },
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Kayn", "", 10)
        self.assertIn("证据 2 时间戳超出视频时长: 00:10 >= 10s", errors)
        payload["evidence"][1]["timestamp"] = "00:08"
        self.assertEqual(
            video_intelligence.validate_analysis(payload, "Kayn", "", 10),
            [],
        )

    def test_validate_analysis_rejects_template_placeholders(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Blitzcrank",
            "summary": "测试摘要",
            "strategy": {"items": [{"name": "装备名", "order": 1, "reason": "作者未明确指出"}]},
            "evidence": [
                {"timestamp": "00:10", "kind": "frame", "claim": "画面证据"},
                {"timestamp": "00:12", "kind": "audio", "claim": "口播证据"},
            ],
            "confidence": 0.9,
        }
        errors = video_intelligence.validate_analysis(payload, "Blitzcrank")
        self.assertIn("策略里包含模板占位词", errors)

    def test_catalog_record_only_marks_exact_patch_current(self):
        candidate = {
            "id": "douyin-1",
            "url": "https://www.douyin.com/video/1",
            "hero": "Blitzcrank",
            "title": "测试",
            "creator": "作者",
            "publishedAt": dt.date.today().isoformat(),
            "durationSeconds": 60,
            "engagement": {},
        }
        analysis = {
            "title": "测试攻略",
            "summary": "测试摘要",
            "patchMentioned": "26.14",
            "strategy": {"augments": [], "items": [], "runes": [], "skillOrder": [], "playstyle": ["测试"]},
            "evidence": [
                {"timestamp": "00:01", "kind": "frame", "claim": "测试"},
                {"timestamp": "00:02", "kind": "audio", "claim": "测试"},
            ],
            "confidence": 0.75,
            "caveat": "其他内容需要根据画面推测。",
        }
        record = video_intelligence.catalog_record(candidate, analysis, "26.15")
        self.assertEqual(record["patchStatus"], "needs-game-check")
        self.assertEqual(record["analysisLabel"], "AI 提炼 · 证据：画面、语音")
        self.assertIn("26.15", record["caveat"])
        self.assertEqual(
            record["expiresAt"],
            (dt.date.today() + dt.timedelta(days=45)).isoformat(),
        )
        analysis["patchMentioned"] = "26.15"
        analysis["evidence"][0]["claim"] = "画面右下角显示 26.15"
        current = video_intelligence.catalog_record(candidate, analysis, "26.15")
        self.assertEqual(current["patchStatus"], "current")

    def test_evidence_coverage_label_only_lists_published_evidence_kinds(self):
        evidence = [
            {"timestamp": "00:01", "kind": "subtitle", "claim": "字幕证据"},
            {"timestamp": "00:02", "kind": "frame", "claim": "画面证据"},
            {"timestamp": "00:03", "kind": "subtitle", "claim": "另一条字幕证据"},
        ]
        self.assertEqual(
            video_intelligence.evidence_coverage_label(evidence),
            "AI 提炼 · 证据：画面、字幕",
        )

    def test_catalog_record_publishes_only_grounded_strategy_projection(self):
        candidate = {
            "id": "douyin-kayn",
            "url": "https://www.douyin.com/video/2",
            "hero": "Kayn",
            "heroName": "凯隐",
            "title": "夸张原标题",
            "creator": "作者",
            "publishedAt": dt.date.today().isoformat(),
            "durationSeconds": 30,
        }
        analysis = {
            "title": "未经证据支持的团队战神",
            "summary": "红凯可以无敌收割并适用于当前版本。",
            "patchMentioned": None,
            "strategy": {
                "augments": [
                    {
                        "name": "牙仙子",
                        "priority": "核心",
                        "reason": "提供额外伤害",
                    }
                ],
                "items": [],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": ["无敌收割"],
            },
            "evidence": [
                {"timestamp": "00:01", "kind": "frame", "claim": "画面显示牙仙子"},
                {"timestamp": "00:02", "kind": "audio", "claim": "作者解释玩法"},
            ],
            "confidence": 0.8,
            "caveat": "",
        }
        record = video_intelligence.catalog_record(candidate, analysis, "26.15")
        self.assertEqual(record["title"], "凯隐 视频证据：牙仙子")
        self.assertEqual(
            record["summary"],
            "时间戳证据已明确记录强化：牙仙子。"
            "未显示的打法、理由和版本适用性不作推断。",
        )
        self.assertEqual(record["strategy"]["augments"], [{"name": "牙仙子"}])
        self.assertEqual(record["strategy"]["playstyle"], [])
        self.assertNotIn("无敌", json.dumps(record, ensure_ascii=False))

    def test_catalog_record_adds_official_patch_impact_for_prepatch_video(self):
        candidate = {
            "id": "douyin-kindred",
            "url": "https://www.douyin.com/video/3",
            "hero": "Kindred",
            "heroName": "千珏",
            "title": "千珏海克斯大乱斗攻略",
            "creator": "作者",
            "publishedAt": "2026-07-25",
            "durationSeconds": 30,
        }
        analysis = {
            "summary": "视频展示强化搭配。",
            "patchMentioned": None,
            "strategy": {"augments": [{"name": "巨人杀手"}]},
            "evidence": [
                {
                    "timestamp": "00:01",
                    "kind": "frame",
                    "claim": "画面展示巨人杀手",
                },
                {
                    "timestamp": "00:02",
                    "kind": "audio",
                    "claim": "口播提到巨人杀手",
                },
            ],
            "confidence": 0.8,
            "caveat": "",
        }
        record = video_intelligence.catalog_record(candidate, analysis, "26.15")
        self.assertEqual(
            record["patchImpact"]["status"],
            "direct-hero-balance-change",
        )
        self.assertEqual(
            record["patchImpact"]["source"],
            "https://www.leagueoflegends.com/en-us/news/game-updates/league-of-legends-patch-26-15-notes/",
        )
        self.assertIn("伤害输出修正从 110% 下调至 100%", record["caveat"])
        candidate["publishedAt"] = "2026-07-29"
        post_patch_record = video_intelligence.catalog_record(
            candidate,
            analysis,
            "26.15",
        )
        self.assertNotIn("patchImpact", post_patch_record)

    def test_catalog_record_adds_official_item_impact_for_prepatch_video(self):
        candidate = {
            "id": "douyin-rammus",
            "url": "https://www.douyin.com/video/4",
            "hero": "Rammus",
            "heroName": "拉莫斯",
            "title": "拉莫斯海克斯大乱斗攻略",
            "creator": "作者",
            "publishedAt": "2026-06-23",
            "durationSeconds": 30,
        }
        analysis = {
            "summary": "视频展示装备搭配。",
            "patchMentioned": None,
            "strategy": {"items": [{"name": "界弓"}]},
            "evidence": [
                {
                    "timestamp": "00:01",
                    "kind": "frame",
                    "claim": "画面展示界弓",
                },
                {
                    "timestamp": "00:02",
                    "kind": "subtitle",
                    "claim": "字幕提到界弓",
                },
            ],
            "confidence": 0.8,
            "caveat": "",
        }
        record = video_intelligence.catalog_record(candidate, analysis, "26.15")
        self.assertEqual(
            record["patchImpact"]["status"],
            "direct-recommended-item-change",
        )
        self.assertIn("界弓（Terminus）", record["caveat"])

    def test_validate_analysis_rejects_unknown_game_names_and_unproven_patch(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Caitlyn",
            "summary": "测试摘要",
            "patchMentioned": "26.15",
            "strategy": {
                "augments": [{"name": "预射靶"}],
                "items": [{"name": "不存在之剑"}],
            },
            "evidence": [
                {"timestamp": "00:03", "kind": "frame", "claim": "画面显示强化"},
                {"timestamp": "00:12", "kind": "audio", "claim": "口播解释打法"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Caitlyn")
        self.assertIn("强化名称不在游戏词典: 预射靶", errors)
        self.assertIn("装备名称不在游戏词典: 不存在之剑", errors)
        self.assertIn("版本号缺少对应的时间戳证据", errors)

    def test_strategy_vocabulary_rejects_arena_only_item(self):
        _, item_names = video_intelligence.known_strategy_names()
        self.assertIn("兰德里的折磨", item_names)
        self.assertNotIn("兰德里的苦楚", item_names)

        payload = {
            "schemaVersion": 1,
            "hero": "Maokai",
            "summary": "视频展示了术师强化与兰德里装备。",
            "strategy": {
                "augments": [{"name": "纯粹主义者 - 术师"}],
                "items": [{"name": "兰德里的苦楚", "order": 1}],
            },
            "evidence": [
                {
                    "timestamp": "00:03",
                    "kind": "frame",
                    "claim": "选择了纯粹主义者 - 术师",
                },
                {
                    "timestamp": "00:06",
                    "kind": "frame",
                    "claim": "装备栏显示兰德里的苦楚",
                },
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Maokai")
        self.assertIn("装备名称不在游戏词典: 兰德里的苦楚", errors)

    def test_publication_gate_hides_video_when_strategy_becomes_invalid(self):
        video = {
            "publishedAt": "2026-07-20",
            "expiresAt": "2026-09-03",
            "strategy": {
                "augments": [{"name": "现役强化"}],
                "items": [{"name": "现役装备"}],
                "runes": [{"name": "现役符文"}],
                "summonerSpells": [{"name": "闪现"}],
            },
        }
        with (
            mock.patch.object(
                video_intelligence,
                "known_strategy_names",
                return_value=({"现役强化"}, {"现役装备"}),
            ),
            mock.patch.object(
                video_intelligence,
                "known_rune_names",
                return_value={"现役符文"},
            ),
            mock.patch.object(
                video_intelligence,
                "allowed_summoner_spell_names",
                return_value={"闪现"},
            ),
        ):
            self.assertTrue(
                video_intelligence.video_is_currently_publishable(
                    video,
                    reference_date=dt.date(2026, 7, 29),
                )
            )
            video["strategy"]["augments"][0]["name"] = "已移除强化"
            self.assertEqual(
                video_intelligence.video_strategy_errors(video),
                ["强化不再适用于当前海克斯大乱斗: 已移除强化"],
            )
            self.assertFalse(
                video_intelligence.video_is_currently_publishable(
                    video,
                    reference_date=dt.date(2026, 7, 29),
                )
            )

    def test_validate_analysis_rejects_removed_rune(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Kayle",
            "summary": "测试摘要",
            "strategy": {"runes": ["征服者", "贪欲猎手"]},
            "evidence": [
                {"timestamp": "00:03", "kind": "frame", "claim": "画面显示符文页"},
                {"timestamp": "00:12", "kind": "audio", "claim": "作者解释符文"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Kayle")
        self.assertIn("符文名称不在当前游戏词典: 贪欲猎手", errors)

    def test_validate_analysis_rejects_unsupported_current_and_ocr_claims(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Akshan",
            "title": "阿克尚自转教学",
            "summary": "当前版本 26.15 可在豪宫深渊自转并让对手强制掉线。",
            "patchMentioned": None,
            "strategy": {"playstyle": ["利用地形自转"]},
            "evidence": [
                {"timestamp": "00:03", "kind": "frame", "claim": "画面显示自转点"},
                {"timestamp": "00:12", "kind": "audio", "claim": "作者解释操作"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Akshan", "26.15")
        self.assertIn("只有打法概述，缺少可逐项验证的搭配字段", errors)
        self.assertIn("正文未经视频证据声称适用于当前补丁 26.15", errors)
        self.assertIn("正文未经视频证据声称适用于当前或最新版本", errors)
        self.assertIn("正文含疑似夸张或 OCR 错误: 强制掉线, 豪宫深渊", errors)

    def test_validate_analysis_rejects_mana_item_for_manless_hero(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Briar",
            "title": "贝蕾亚单一构筑",
            "summary": "围绕飞升仪式进行输出。",
            "strategy": {
                "augments": [{"name": "飞升仪式"}],
                "items": [{"name": "大天使之杖", "order": 1}],
            },
            "evidence": [
                {"timestamp": "00:03", "kind": "frame", "claim": "画面显示装备"},
                {"timestamp": "00:12", "kind": "audio", "claim": "作者解释出装"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Briar")
        self.assertIn("无蓝英雄不能采用法力装备: 大天使之杖", errors)

    def test_validate_analysis_rejects_summoner_spell_unavailable_in_aram(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Rammus",
            "title": "拉莫斯高攻速玩法",
            "summary": "利用界弓和荆棘之甲持续作战。",
            "strategy": {
                "items": [{"name": "界弓", "order": 1}],
                "summonerSpells": ["闪现", "惩戒"],
            },
            "evidence": [
                {"timestamp": "00:03", "kind": "frame", "claim": "画面显示装备"},
                {"timestamp": "00:12", "kind": "audio", "claim": "作者解释玩法"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Rammus")
        self.assertIn("召唤师技能不适用于海克斯大乱斗: 惩戒", errors)
        self.assertNotIn("召唤师技能不适用于海克斯大乱斗: 闪现", errors)

    def test_validate_analysis_requires_skill_order_timestamp_evidence(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Fiora",
            "title": "菲奥娜单一构筑",
            "summary": "围绕无尽之刃进行输出。",
            "strategy": {
                "items": [{"name": "无尽之刃", "order": 1}],
                "skillOrder": ["主升Q，副升E，最后加满W"],
            },
            "evidence": [
                {"timestamp": "00:03", "kind": "frame", "claim": "装备栏显示无尽之刃"},
                {"timestamp": "00:12", "kind": "audio", "claim": "作者解释出装"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Fiora")
        self.assertIn("技能加点缺少明确的时间戳证据", errors)
        payload["evidence"].append(
            {
                "timestamp": "00:18",
                "kind": "frame",
                "claim": "菲奥娜技能加点主Q副E，最后加满W",
            }
        )
        self.assertEqual(video_intelligence.validate_analysis(payload, "Fiora"), [])

    def test_validate_analysis_requires_each_item_in_item_context_evidence(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Graves",
            "title": "格雷福斯暴击玩法",
            "summary": "视频围绕暴击强化与出装讲解打法。",
            "strategy": {
                "augments": [{"name": "升级：无尽之刃"}],
                "items": [{"name": "无尽之刃", "order": 1}],
            },
            "evidence": [
                {
                    "timestamp": "00:03",
                    "kind": "frame",
                    "claim": "选择了“升级：无尽之刃”强化",
                },
                {
                    "timestamp": "00:12",
                    "kind": "audio",
                    "claim": "作者解释暴击玩法",
                },
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Graves")
        self.assertIn("出装缺少逐件时间戳证据: 无尽之刃", errors)
        payload["evidence"].append(
            {
                "timestamp": "00:18",
                "kind": "frame",
                "claim": "格雷福斯装备栏显示无尽之刃",
            }
        )
        self.assertEqual(video_intelligence.validate_analysis(payload, "Graves"), [])

    def test_validate_analysis_requires_each_augment_and_rune_in_evidence(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Gwen",
            "title": "格温单一玩法",
            "summary": "视频展示强化与符文搭配。",
            "strategy": {
                "augments": [
                    {"name": "吃过路兵"},
                    {"name": "无尽大杀四方"},
                ],
                "runes": [{"name": "征服者"}],
            },
            "evidence": [
                {
                    "timestamp": "00:03",
                    "kind": "frame",
                    "claim": "画面展示吃过路兵",
                },
                {
                    "timestamp": "00:12",
                    "kind": "audio",
                    "claim": "作者解释整体打法",
                },
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Gwen")
        self.assertIn("强化缺少逐项时间戳证据: 无尽大杀四方", errors)
        self.assertIn("符文缺少逐项时间戳证据: 征服者", errors)
        payload["evidence"].extend(
            [
                {
                    "timestamp": "00:18",
                    "kind": "frame",
                    "claim": "强化界面显示无尽大杀四方",
                },
                {
                    "timestamp": "00:21",
                    "kind": "subtitle",
                    "claim": "格温符文页选择征服者",
                },
            ]
        )
        self.assertEqual(video_intelligence.validate_analysis(payload, "Gwen"), [])

    def test_validate_analysis_rejects_flattened_multiple_builds(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Kayle",
            "summary": "测试摘要",
            "strategy": {
                "items": [
                    {"name": "纳什之牙", "order": 1},
                    {"name": "灭世者的死亡之帽", "order": 2},
                    {"name": "斯塔缇克电刃", "order": 1},
                ],
            },
            "evidence": [
                {"timestamp": "00:03", "kind": "frame", "claim": "画面显示装备"},
                {"timestamp": "00:12", "kind": "audio", "claim": "作者解释出装"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Kayle")
        self.assertIn("装备购买顺序重复，疑似混入多套互斥方案", errors)

    def test_validate_analysis_rejects_multi_build_summary(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Kayle",
            "title": "凯尔多种套路",
            "summary": "视频介绍了凯尔三种主要玩法。",
            "strategy": {
                "augments": [{"name": "双重打击"}],
                "items": [{"name": "纳什之牙", "order": 1}],
            },
            "evidence": [
                {"timestamp": "00:03", "kind": "frame", "claim": "画面显示装备"},
                {"timestamp": "00:12", "kind": "audio", "claim": "作者解释出装"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Kayle")
        self.assertIn("视频包含多套互斥玩法，当前单方案结构不能安全发布", errors)

    def test_validate_analysis_accepts_separately_evidenced_multiple_builds(self):
        payload = {
            "schemaVersion": 2,
            "hero": "Kayle",
            "title": "凯尔两套玩法",
            "summary": "视频分别介绍两套玩法，并展示各自出装。",
            "strategies": [
                {
                    "id": "ap",
                    "label": "法强流",
                    "items": [{"name": "纳什之牙", "order": 1}],
                },
                {
                    "id": "on-hit",
                    "label": "电刃流",
                    "items": [{"name": "斯塔缇克电刃", "order": 1}],
                },
            ],
            "evidence": [
                {
                    "timestamp": "00:03",
                    "kind": "frame",
                    "strategyId": "ap",
                    "claim": "凯尔法强流装备界面显示纳什之牙",
                },
                {
                    "timestamp": "00:08",
                    "kind": "audio",
                    "strategyId": "ap",
                    "claim": "作者解释凯尔法强流出装纳什之牙",
                },
                {
                    "timestamp": "00:13",
                    "kind": "frame",
                    "strategyId": "on-hit",
                    "claim": "凯尔电刃流装备界面显示斯塔缇克电刃",
                },
                {
                    "timestamp": "00:18",
                    "kind": "audio",
                    "strategyId": "on-hit",
                    "claim": "作者解释凯尔电刃流出装斯塔缇克电刃",
                },
            ],
            "confidence": 0.8,
        }
        self.assertEqual(
            video_intelligence.validate_analysis(payload, "Kayle"),
            [],
        )
        projected, omissions = video_intelligence.project_grounded_analysis(payload)
        self.assertEqual(omissions, [])
        self.assertEqual(
            [
                strategy["items"][0]["name"]
                for strategy in projected["strategies"]
            ],
            ["纳什之牙", "斯塔缇克电刃"],
        )

    def test_validate_analysis_does_not_cross_assign_multi_build_evidence(self):
        payload = {
            "schemaVersion": 2,
            "hero": "Kayle",
            "title": "凯尔两套玩法",
            "summary": "视频分别介绍两套玩法。",
            "strategies": [
                {
                    "id": "ap",
                    "label": "法强流",
                    "items": [{"name": "纳什之牙", "order": 1}],
                },
                {
                    "id": "on-hit",
                    "label": "电刃流",
                    "items": [{"name": "斯塔缇克电刃", "order": 1}],
                },
            ],
            "evidence": [
                {
                    "timestamp": "00:03",
                    "kind": "frame",
                    "strategyId": "ap",
                    "claim": "凯尔装备界面显示纳什之牙和斯塔缇克电刃",
                },
                {
                    "timestamp": "00:08",
                    "kind": "audio",
                    "strategyId": "ap",
                    "claim": "作者解释凯尔出装纳什之牙和斯塔缇克电刃",
                },
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Kayle")
        self.assertIn("电刃流：出装缺少逐件时间戳证据: 斯塔缇克电刃", errors)
        self.assertIn("电刃流：缺少归属于本方案的画面证据", errors)

    def test_replace_catalog_keeps_multiple_videos_per_hero_with_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.json"
            catalog_path.write_text(
                json.dumps({
                    "schemaVersion": 2,
                    "videos": [
                        {"id": "other", "heroes": ["Gwen"], "publishedAt": "2026-07-01"},
                        {"id": "old-kayle", "heroes": ["Kayle"], "publishedAt": "2026-06-01"},
                    ],
                }),
                encoding="utf-8",
            )
            records = [
                {
                    "id": f"kayle-{index}",
                    "heroes": ["Kayle"],
                    "publishedAt": f"2026-07-0{index}",
                    "confidence": 0.8,
                }
                for index in (1, 2, 3)
            ]
            with mock.patch.object(
                video_intelligence,
                "CATALOG_PATH",
                catalog_path,
            ):
                video_intelligence.replace_catalog_for_heroes(
                    records,
                    {"Kayle"},
                    max_per_hero=2,
                )
            saved = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [row["id"] for row in saved["videos"]],
            ["kayle-3", "kayle-2", "other"],
        )

    def test_normalize_analysis_names_only_applies_safe_mappings(self):
        payload = {
            "summary": "界弓配反甲，青龙刀补技能急速。",
            "strategy": {
                "augments": [
                    {"name": "物法接修", "reason": "物法接修适合混伤。"},
                    {"name": "瞄准镜"},
                ],
                "items": [
                    {"name": "青龙刀"},
                    {"name": "借弓"},
                    {"name": "反甲"},
                    {"name": "轻语"},
                ],
                "playstyle": ["做出反甲后主动贴近普攻英雄。"],
            },
            "evidence": [
                {"timestamp": "00:10", "kind": "audio", "claim": "作者原话：反甲。"}
            ],
        }
        normalized, changes = video_intelligence.normalize_analysis_names(payload)
        self.assertEqual(normalized["strategy"]["augments"][0]["name"], "物法皆修")
        self.assertEqual(
            normalized["strategy"]["augments"][0]["reason"],
            "物法皆修适合混伤。",
        )
        self.assertEqual(normalized["strategy"]["augments"][1]["name"], "瞄准镜")
        self.assertEqual(normalized["strategy"]["items"][0]["name"], "朔极之矛")
        self.assertEqual(normalized["strategy"]["items"][1]["name"], "界弓")
        self.assertEqual(normalized["strategy"]["items"][2]["name"], "荆棘之甲")
        self.assertEqual(normalized["strategy"]["items"][3]["name"], "轻语")
        self.assertEqual(normalized["summary"], "界弓配荆棘之甲，朔极之矛补技能急速。")
        self.assertEqual(
            normalized["strategy"]["playstyle"],
            ["做出荆棘之甲后主动贴近普攻英雄。"],
        )
        self.assertEqual(
            normalized["evidence"][0]["claim"],
            "作者原话：反甲。",
        )
        self.assertEqual(len(changes), 8)
        self.assertEqual(payload["strategy"]["augments"][0]["name"], "物法接修")
        self.assertEqual(payload["summary"], "界弓配反甲，青龙刀补技能急速。")

    def test_normalize_analysis_names_maps_common_item_aliases(self):
        payload = {
            "schemaVersion": 2,
            "hero": "Brand",
            "strategies": [{
                "id": "build-1",
                "label": "双烧",
                "augments": [],
                "items": [
                    {"name": "大面具"},
                    {"name": "冰杖"},
                    {"name": "大帽子"},
                ],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": [],
            }],
            "evidence": [],
        }
        normalized, changes = video_intelligence.normalize_analysis_names(payload)
        self.assertEqual(
            [row["name"] for row in normalized["strategies"][0]["items"]],
            [
                "兰德里的折磨",
                "瑞莱的冰晶节杖",
                "灭世者的死亡之帽",
            ],
        )
        self.assertEqual(len(changes), 3)

    def test_normalize_analysis_names_maps_reviewed_augment_ocr_alias(self):
        payload = {
            "schemaVersion": 2,
            "hero": "Ambessa",
            "summary": "画面显示芽仙子。",
            "strategies": [{
                "id": "build-1",
                "label": "主方案",
                "augments": [{"name": "芽仙子"}],
                "items": [],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": [],
            }],
            "evidence": [{
                "timestamp": "00:06",
                "kind": "frame",
                "strategyId": "build-1",
                "claim": "画面显示芽仙子。",
            }],
        }
        normalized, changes = video_intelligence.normalize_analysis_names(payload)
        self.assertEqual(
            normalized["strategies"][0]["augments"][0]["name"],
            "牙仙子",
        )
        self.assertEqual(normalized["summary"], "画面显示牙仙子。")
        self.assertEqual(
            normalized["evidence"][0]["claim"],
            "画面显示芽仙子。",
        )
        self.assertEqual(len(changes), 2)

    def test_key_frame_ocr_can_ground_hero_identity(self):
        payload = {
            "schemaVersion": 2,
            "hero": "Brand",
            "strategies": [{
                "id": "build-1",
                "label": "主方案",
                "augments": [{"name": "炼狱导管"}],
                "items": [],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": [],
            }],
            "evidence": [{
                "timestamp": "00:12",
                "kind": "subtitle",
                "strategyId": "build-1",
                "claim": "核心强化是炼狱导管",
            }],
        }
        hero = {
            "name": "布兰德",
            "alias": "Brand",
            "epithet": "复仇焰魂",
            "search": "火男,布兰德",
        }
        grounded = video_intelligence.attach_key_frame_identity_evidence(
            payload,
            {
                "frames": [{
                    "timestampSeconds": 0,
                    "ocrText": "海斗攻略 复仇焰魂 布兰德 火男",
                }],
            },
            hero,
        )
        identity = grounded["evidence"][-1]
        self.assertEqual(identity["timestamp"], "00:00")
        self.assertEqual(identity["kind"], "frame")
        self.assertIn("布兰德（Brand）", identity["claim"])
        self.assertEqual(identity["source"], "local-vision-ocr")

    def test_key_frame_identity_accepts_one_character_name_only_in_guide_context(self):
        payload = {
            "hero": "Jhin",
            "strategy": {
                "augments": [{"name": "踢踏舞"}],
                "items": [],
            },
            "evidence": [{
                "timestamp": "00:12",
                "kind": "subtitle",
                "claim": "核心强化是踢踏舞",
            }],
        }
        hero = {
            "name": "烬",
            "alias": "Jhin",
            "epithet": "戏命师",
            "search": "jhin,戏命师,烬",
        }
        grounded = video_intelligence.attach_key_frame_identity_evidence(
            payload,
            {
                "frames": [{
                    "timestampSeconds": 0,
                    "ocrText": "踢踏舞烬的玩法",
                }],
            },
            hero,
        )
        self.assertIn("烬（Jhin）", grounded["evidence"][-1]["claim"])

        unrelated = video_intelligence.attach_key_frame_identity_evidence(
            payload,
            {
                "frames": [{
                    "timestampSeconds": 0,
                    "ocrText": "敌方烬完成击杀",
                }],
            },
            hero,
        )
        self.assertEqual(unrelated["evidence"], payload["evidence"])

    def test_key_frame_grounding_rejects_names_not_seen_near_evidence(self):
        payload = {
            "schemaVersion": 2,
            "hero": "Brand",
            "strategies": [{
                "id": "build-1",
                "label": "双烧",
                "augments": [{"name": "炼狱导管"}],
                "items": [
                    {"name": "兰德里的折磨"},
                    {"name": "饮血剑"},
                ],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": [],
            }],
            "evidence": [{
                "timestamp": "00:42",
                "kind": "frame",
                "claim": "画面显示炼狱导管、兰德里的折磨和饮血剑",
            }],
        }
        errors = video_intelligence.key_frame_grounding_errors(
            payload,
            {
                "status": "captured",
                "frames": [{
                    "timestampSeconds": 42.5,
                    "ocrText": "炼狱导管 兰德里的折磨 影焰",
                    "vocabularyMatches": {
                        "augments": ["炼狱导管"],
                        "items": ["兰德里的折磨", "影焰"],
                        "runes": [],
                    },
                }],
            },
        )
        self.assertEqual(
            errors,
            ["双烧：饮血剑 未被同时间点关键画面 OCR 复核"],
        )

    def test_key_frame_grounding_uses_name_specific_evidence_timestamp(self):
        payload = {
            "schemaVersion": 2,
            "hero": "Galio",
            "strategies": [{
                "id": "build-1",
                "label": "主方案",
                "augments": [],
                "items": [{"name": "大天使之杖"}],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": [],
            }],
            "evidence": [
                {
                    "timestamp": "00:00",
                    "kind": "frame",
                    "strategyId": "build-1",
                    "claim": "目标英雄：加里奥",
                },
                {
                    "timestamp": "00:48",
                    "kind": "subtitle",
                    "strategyId": "build-1",
                    "claim": "装备：大天使之杖",
                },
            ],
        }
        errors = video_intelligence.key_frame_grounding_errors(
            payload,
            {
                "status": "captured",
                "frames": [
                    {
                        "timestampSeconds": 0,
                        "ocrText": "大天使之杖",
                        "vocabularyMatches": {"items": ["大天使之杖"]},
                    },
                    {
                        "timestampSeconds": 48,
                        "ocrText": "心之钢 裂隙制造者",
                        "vocabularyMatches": {
                            "items": ["心之钢", "裂隙制造者"],
                        },
                    },
                ],
            },
        )
        self.assertEqual(
            errors,
            ["主方案：大天使之杖 未被同时间点关键画面 OCR 复核"],
        )

    def test_key_frame_grounding_accepts_repeated_single_character_ocr_errors(self):
        payload = {
            "schemaVersion": 2,
            "hero": "Rell",
            "strategies": [{
                "id": "build-1",
                "label": "坦克流",
                "augments": [{"name": "坦克引擎"}],
                "items": [],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": [],
            }],
            "evidence": [{
                "timestamp": "00:15",
                "kind": "subtitle",
                "strategyId": "build-1",
                "claim": "强化：坦克引擎",
            }],
        }
        errors = video_intelligence.key_frame_grounding_errors(
            payload,
            {
                "status": "captured",
                "frames": [
                    {
                        "timestampSeconds": 14.5,
                        "ocrText": "关键海克斯 坦克引蒙",
                        "vocabularyMatches": {"augments": []},
                    },
                    {
                        "timestampSeconds": 15.0,
                        "ocrText": "关键海克斯 坦克引景",
                        "vocabularyMatches": {"augments": []},
                    },
                ],
            },
        )
        self.assertEqual(errors, [])

    def test_project_grounded_analysis_keeps_only_current_evidenced_subset(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Aatrox",
            "title": "剑魔单一出装证据",
            "summary": "视频展示了一件可以逐项核对的装备。",
            "strategy": {
                "augments": [{"name": "狂徒豪气"}],
                "items": [
                    {"name": "狂妄", "order": 1},
                    {"name": "大穿", "order": 2},
                    {"name": "死亡之舞", "order": 3},
                ],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": ["无证据打法"],
            },
            "evidence": [
                {
                    "timestamp": "00:06",
                    "kind": "frame",
                    "claim": "显示 Aatrox 参与团战",
                },
                {
                    "timestamp": "00:45",
                    "kind": "frame",
                    "claim": "装备图标显示狂妄和大穿",
                },
            ],
            "confidence": 0.85,
        }

        projected, omissions = video_intelligence.project_grounded_analysis(
            payload
        )

        self.assertEqual(
            projected["strategy"]["items"],
            [{"name": "狂妄", "order": 1}],
        )
        self.assertEqual(projected["strategy"]["augments"], [])
        self.assertEqual(projected["strategy"]["playstyle"], [])
        self.assertEqual(
            {row["name"] for row in omissions},
            {"狂徒豪气", "大穿", "死亡之舞"},
        )
        self.assertEqual(len(video_intelligence.validate_analysis(
            projected,
            "Aatrox",
            video_duration_seconds=60,
        )), 0)
        self.assertEqual(len(payload["strategy"]["items"]), 3)

    def test_projection_accepts_safe_item_alias_in_original_evidence(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Rammus",
            "summary": "视频展示了拉莫斯的一件装备。",
            "strategy": {
                "items": [{"name": "荆棘之甲", "order": 1}],
                "playstyle": [],
            },
            "evidence": [
                {
                    "timestamp": "00:03",
                    "kind": "frame",
                    "claim": "拉莫斯进入团战",
                },
                {
                    "timestamp": "00:12",
                    "kind": "subtitle",
                    "claim": "装备选择为反甲",
                },
            ],
            "confidence": 0.8,
        }

        projected, omissions = video_intelligence.project_grounded_analysis(
            payload
        )

        self.assertEqual(omissions, [])
        self.assertEqual(
            video_intelligence.strategy_names(
                projected["strategy"]["items"]
            ),
            ["荆棘之甲"],
        )
        self.assertEqual(
            video_intelligence.validate_analysis(
                projected,
                "Rammus",
                video_duration_seconds=30,
            ),
            [],
        )

    def test_partial_catalog_evidence_does_not_leak_omitted_names(self):
        candidate = {
            "id": "douyin-lillia",
            "url": "https://www.douyin.com/video/4",
            "hero": "Lillia",
            "heroName": "莉莉娅",
            "creator": "作者",
            "publishedAt": dt.date.today().isoformat(),
            "durationSeconds": 30,
        }
        projected = {
            "summary": "视频展示强化。",
            "patchMentioned": None,
            "strategy": {
                "augments": [{"name": "穿针引线"}],
                "items": [],
                "runes": [],
                "skillOrder": [],
                "summonerSpells": [],
                "playstyle": [],
            },
            "evidence": [
                {
                    "timestamp": "00:06",
                    "kind": "frame",
                    "claim": "虚幻行者、穿针引线、水上漫步者强化展示",
                },
                {
                    "timestamp": "00:12",
                    "kind": "frame",
                    "claim": "莉莉娅团战表现展示",
                },
            ],
            "confidence": 0.8,
            "caveat": "",
        }

        record = video_intelligence.catalog_record(
            candidate,
            projected,
            "26.15",
            projection_omissions=[
                {
                    "group": "augments",
                    "name": "虚幻行者",
                    "reason": "not-in-current-dictionary",
                }
            ],
        )
        serialized_evidence = json.dumps(record["evidence"], ensure_ascii=False)

        self.assertIn("穿针引线", serialized_evidence)
        self.assertIn("莉莉娅", serialized_evidence)
        self.assertNotIn("虚幻行者", serialized_evidence)
        self.assertNotIn("水上漫步者", serialized_evidence)
        self.assertIn("已省略", record["caveat"])
        self.assertNotIn("推测", record["caveat"])

    def test_revalidate_cached_draft_applies_current_aliases_and_gate(self):
        candidate = {
            "id": "douyin-123456",
            "videoId": "123456",
            "url": "https://www.douyin.com/video/123456",
            "hero": "Rammus",
            "heroName": "拉莫斯",
            "title": "龙龟海克斯大乱斗出装攻略",
            "creator": "测试作者",
            "publishedAt": dt.datetime.now(dt.timezone.utc).date().isoformat(),
            "durationSeconds": 60,
        }
        draft = {
            "analysisContractVersion": video_intelligence.ANALYSIS_CONTRACT_VERSION,
            "analysis": {
                "schemaVersion": 1,
                "hero": "Rammus",
                "title": "反甲龙龟",
                "summary": "以反伤和坦度为主的单一构筑。",
                "patchMentioned": None,
                "strategy": {
                    "augments": [],
                    "items": [{"name": "反甲", "order": 1, "reason": "反伤"}],
                    "runes": [],
                    "skillOrder": [],
                    "summonerSpells": [],
                    "playstyle": ["正面承伤"],
                },
                "evidence": [
                    {
                        "timestamp": "00:03",
                        "kind": "frame",
                        "claim": "拉莫斯的装备栏显示荆棘之甲",
                    },
                    {"timestamp": "00:12", "kind": "audio", "claim": "作者解释出装"},
                ],
                "confidence": 0.8,
                "caveat": "",
            },
            "normalizations": [],
            "keyFrameIntelligence": {
                "status": "captured",
                "frames": [{
                    "timestampSeconds": 3,
                    "ocrText": "拉莫斯 装备栏 荆棘之甲",
                    "vocabularyMatches": {
                        "augments": [],
                        "items": ["荆棘之甲"],
                        "runes": [],
                    },
                }],
            },
        }
        updated = video_intelligence.revalidate_cached_draft(
            draft,
            candidate,
            current_patch="26.15",
            min_confidence=0.68,
            max_age_days=45,
            max_duration_seconds=600,
        )
        self.assertTrue(updated["qualityGate"]["passed"])
        self.assertEqual(
            updated["analysis"]["strategy"]["items"][0]["name"],
            "荆棘之甲",
        )
        self.assertEqual(
            updated["catalogRecord"]["strategy"]["items"][0]["name"],
            "荆棘之甲",
        )
        self.assertEqual(updated["projectionOmissions"], [])
        self.assertEqual(
            updated["publicationProjection"]["strategy"]["items"][0]["name"],
            "荆棘之甲",
        )

    def test_analysis_prompt_contains_bounded_standard_vocabularies(self):
        prompt = video_intelligence.analysis_prompt(
            {"name": "凯尔", "alias": "Kayle"},
            "26.15",
            118,
        )
        augment_names, _ = video_intelligence.known_strategy_names()
        self.assertIn(f"海克斯强化（{len(augment_names)} 项）", prompt)
        self.assertIn("双重打击", augment_names)
        self.assertIn("物法皆修", prompt)
        self.assertNotIn("附灵飞弹", augment_names)
        self.assertNotIn("？？？", augment_names)
        self.assertIn("可购买装备（", prompt)
        self.assertIn("朔极之矛", prompt)
        self.assertIn("当前符文与符文系（67 项）", prompt)
        self.assertIn("征服者", prompt)
        self.assertIn("海克斯大乱斗可用召唤师技能（9 项）", prompt)
        self.assertIn("标记", prompt)
        self.assertNotIn("惩戒", video_intelligence.allowed_summoner_spell_names())
        self.assertIn("无法唯一匹配时必须留空", prompt)
        self.assertIn("目标英雄的官方中文名“凯尔”", prompt)
        self.assertIn("英文 alias“Kayle”", prompt)
        self.assertIn("0 <= t < 118 秒", prompt)
        self.assertLess(len(prompt), 12_000)

    def test_bilinote_screenshot_manifest_persists_timestamped_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "note_results"
            screenshots = root / "static" / "screenshots"
            cache = root / "cache"
            results.mkdir(parents=True)
            screenshots.mkdir(parents=True)
            task_id = "573b1451-2c36-4c31-a917-405846c21059"
            first = "screenshot_000_11111111-1111-1111-1111-111111111111.jpg"
            second = "screenshot_001_22222222-2222-2222-2222-222222222222.jpg"
            (results / f"{task_id}_markdown.md").write_text(
                "第一帧 *Screenshot-[00:09]\n第二帧 Screenshot-01:02",
                encoding="utf-8",
            )
            (screenshots / first).write_bytes(b"first-frame")
            (screenshots / second).write_bytes(b"second-frame")
            processed = (
                f"第一帧 ![](/static/screenshots/{first})\n"
                f"第二帧 ![](/static/screenshots/{second})"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "BILINOTE_NOTE_RESULTS_DIR": str(results),
                    "BILINOTE_SCREENSHOT_DIR": str(screenshots),
                },
                clear=False,
            ):
                with mock.patch.object(video_intelligence, "CACHE_DIR", cache):
                    manifest = video_intelligence.bilinote_screenshot_manifest(
                        task_id,
                        "7661195910239489323",
                        processed,
                    )

            self.assertEqual(manifest["status"], "captured")
            self.assertEqual(manifest["markerCount"], 2)
            self.assertEqual(manifest["imageCount"], 2)
            self.assertEqual(
                [row["timestamp"] for row in manifest["frames"]],
                ["00:09", "01:02"],
            )
            self.assertEqual(
                [row["timestampSeconds"] for row in manifest["frames"]],
                [9, 62],
            )
            self.assertEqual(
                (cache / manifest["frames"][0]["cachedPath"]).read_bytes(),
                b"first-frame",
            )
            self.assertEqual(
                manifest["frames"][0]["sha256"],
                video_intelligence.sha256_file(screenshots / first),
            )

    def test_bilinote_screenshot_manifest_fails_closed_on_count_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "note_results"
            screenshots = root / "static" / "screenshots"
            results.mkdir(parents=True)
            screenshots.mkdir(parents=True)
            task_id = "task-123"
            (results / f"{task_id}_markdown.md").write_text(
                "一 *Screenshot-[00:09]\n二 *Screenshot-[00:12]",
                encoding="utf-8",
            )
            filename = "screenshot_000_11111111-1111-1111-1111-111111111111.jpg"
            (screenshots / filename).write_bytes(b"frame")
            with mock.patch.dict(
                os.environ,
                {
                    "BILINOTE_NOTE_RESULTS_DIR": str(results),
                    "BILINOTE_SCREENSHOT_DIR": str(screenshots),
                },
                clear=False,
            ):
                manifest = video_intelligence.bilinote_screenshot_manifest(
                    task_id,
                    "video-123",
                    f"![](/static/screenshots/{filename})",
                )

        self.assertEqual(manifest["status"], "unavailable")
        self.assertEqual(manifest["reason"], "count-mismatch")
        self.assertEqual(manifest["markerCount"], 2)
        self.assertEqual(manifest["imageCount"], 1)
        self.assertEqual(manifest["frames"], [])

    def test_bilinote_storage_paths_derive_from_upload_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory) / "backend"
            upload = backend / "uploads"
            with mock.patch.dict(
                os.environ,
                {
                    "BILINOTE_UPLOAD_DIR": str(upload),
                    "BILINOTE_NOTE_RESULTS_DIR": "",
                    "BILINOTE_SCREENSHOT_DIR": "",
                },
                clear=False,
            ):
                self.assertEqual(
                    video_intelligence.bilinote_storage_paths(),
                    (
                        (backend / "note_results").resolve(),
                        (backend / "static" / "screenshots").resolve(),
                    ),
                )

    def test_attach_bilinote_manifest_inherits_v4_processed_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            draft_path = root / "draft-morgana-video-123-v5.json"
            legacy_path = root / "draft-morgana-video-123-v4-bilinote.md"
            legacy_path.write_text("legacy processed markdown", encoding="utf-8")
            draft = {
                "candidate": {"videoId": "video-123"},
                "bilinote": {"taskId": "task-123"},
            }
            with mock.patch.object(
                video_intelligence,
                "bilinote_screenshot_manifest",
                return_value={"status": "captured", "frames": []},
            ) as manifest:
                updated = video_intelligence.attach_bilinote_screenshot_manifest(
                    draft,
                    draft_path,
                )

        manifest.assert_called_once_with(
            "task-123",
            "video-123",
            "legacy processed markdown",
        )
        self.assertEqual(
            updated["bilinote"]["screenshotManifest"][
                "processedMarkdownSource"
            ],
            legacy_path.name,
        )

    def test_bilinote_screenshots_can_supply_offline_frame_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            video_id = "video-123"
            source = cache / "frames" / video_id / "frame.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"persisted-frame")
            draft = {
                "candidate": {"videoId": video_id},
                "bilinote": {
                    "screenshotManifest": {
                        "status": "captured",
                        "frames": [{
                            "cachedPath": f"frames/{video_id}/frame.jpg",
                            "timestampSeconds": 12,
                        }],
                    },
                },
            }
            enriched = [{
                "path": str(source),
                "timestampSeconds": 12,
                "stage": "bilinote-screenshot",
                "ocrText": "坦克引擎",
                "vocabularyMatches": {"augments": ["坦克引擎"]},
                "iconMatches": [],
            }]
            with mock.patch.object(
                video_intelligence,
                "CACHE_DIR",
                cache,
            ), mock.patch(
                "pipeline.frame_intelligence.vision_ocr",
                return_value=[{"path": str(source), "texts": []}],
            ) as ocr, mock.patch(
                "pipeline.frame_intelligence.enrich_frame_rows",
                return_value=enriched,
            ) as enrich:
                updated = (
                    video_intelligence.attach_bilinote_frame_intelligence(
                        draft
                    )
                )

        self.assertEqual(
            updated["keyFrameIntelligence"]["status"],
            "captured",
        )
        self.assertEqual(
            updated["keyFrameIntelligence"]["source"],
            "bilinote-persisted-screenshots",
        )
        self.assertEqual(updated["keyFrameIntelligence"]["frames"], enriched)
        ocr.assert_called_once_with([source.resolve()])
        self.assertFalse(enrich.call_args.kwargs["match_icons"])

    def test_screenshot_manifest_gate_accepts_persisted_frame_timestamp(self):
        draft = {
            "catalogRecord": {
                "confidence": 0.8,
                "evidence": [
                    {"timestamp": "00:15", "kind": "frame", "claim": "画面"},
                    {"timestamp": "00:18", "kind": "audio", "claim": "口播"},
                ],
            },
            "qualityGate": {
                "passed": True,
                "minimumConfidence": 0.68,
                "errors": [],
            },
            "bilinote": {
                "screenshotManifest": {
                    "status": "captured",
                    "frames": [{"timestamp": "00:15"}],
                },
            },
        }
        updated = video_intelligence.apply_screenshot_manifest_gate(draft)
        self.assertTrue(updated["qualityGate"]["passed"])
        self.assertEqual(updated["qualityGate"]["errors"], [])

    def test_screenshot_manifest_gate_rejects_unmapped_frame_timestamp(self):
        draft = {
            "catalogRecord": {
                "confidence": 0.8,
                "evidence": [
                    {"timestamp": "00:06", "kind": "frame", "claim": "画面"},
                ],
            },
            "qualityGate": {
                "passed": True,
                "minimumConfidence": 0.68,
                "errors": [],
            },
            "bilinote": {
                "screenshotManifest": {
                    "status": "captured",
                    "frames": [{"timestamp": "00:09"}],
                },
            },
        }
        updated = video_intelligence.apply_screenshot_manifest_gate(draft)
        self.assertFalse(updated["qualityGate"]["passed"])
        self.assertEqual(
            updated["qualityGate"]["errors"],
            ["公开画面证据没有同时间戳截图: 00:06"],
        )

    def test_local_key_frame_fills_missing_persisted_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            video_id = "7665292087830629651"
            source = cache / "key_frames" / video_id / "keyword-0002.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"verified-local-key-frame")
            draft = {
                "candidate": {"videoId": video_id},
                "catalogRecord": {
                    "confidence": 0.8,
                    "evidence": [
                        {
                            "timestamp": "00:06",
                            "kind": "frame",
                            "claim": "画面显示目标英雄",
                        },
                    ],
                },
                "qualityGate": {
                    "passed": True,
                    "minimumConfidence": 0.68,
                    "errors": [],
                },
                "keyFrameIntelligence": {
                    "status": "captured",
                    "frames": [
                        {
                            "path": str(source),
                            "timestampSeconds": 6.0,
                            "stage": "keyword-dense",
                        },
                    ],
                },
                "bilinote": {
                    "screenshotManifest": {
                        "status": "captured",
                        "frames": [],
                    },
                },
            }
            with mock.patch.object(video_intelligence, "CACHE_DIR", cache):
                updated = video_intelligence.attach_evidence_screenshot_manifest(
                    draft
                )
                gated = video_intelligence.apply_screenshot_manifest_gate(updated)

            manifest = updated["evidenceScreenshotManifest"]
            self.assertEqual(manifest["status"], "captured")
            self.assertEqual(manifest["localFramesAdded"], 1)
            self.assertEqual(manifest["missingFrameTimestamps"], [])
            frame = manifest["frames"][0]
            self.assertEqual(frame["timestamp"], "00:06")
            self.assertEqual(frame["capturedTimestampSeconds"], 6.0)
            self.assertEqual(frame["timestampOffsetSeconds"], 0.0)
            self.assertEqual(frame["source"], "local-key-frame-intelligence")
            self.assertEqual(
                (cache / frame["cachedPath"]).read_bytes(),
                b"verified-local-key-frame",
            )
            self.assertTrue(gated["qualityGate"]["passed"])

    def test_local_key_frame_mapping_fails_closed_outside_half_second(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            video_id = "video-123"
            source = cache / "key_frames" / video_id / "coarse.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"frame")
            draft = {
                "candidate": {"videoId": video_id},
                "catalogRecord": {
                    "confidence": 0.8,
                    "evidence": [
                        {"timestamp": "00:06", "kind": "frame", "claim": "画面"},
                    ],
                },
                "qualityGate": {
                    "passed": True,
                    "minimumConfidence": 0.68,
                    "errors": [],
                },
                "keyFrameIntelligence": {
                    "status": "captured",
                    "frames": [
                        {
                            "path": str(source),
                            "timestampSeconds": 7.0,
                            "stage": "coarse",
                        },
                    ],
                },
                "bilinote": {
                    "screenshotManifest": {
                        "status": "unavailable",
                        "frames": [],
                    },
                },
            }
            with mock.patch.object(video_intelligence, "CACHE_DIR", cache):
                updated = video_intelligence.attach_evidence_screenshot_manifest(
                    draft
                )
                gated = video_intelligence.apply_screenshot_manifest_gate(updated)

            self.assertEqual(
                updated["evidenceScreenshotManifest"]["missingFrameTimestamps"],
                ["00:06"],
            )
            self.assertFalse(gated["qualityGate"]["passed"])
            self.assertEqual(
                gated["qualityGate"]["errors"],
                ["公开画面证据缺少可持久化截图时间映射"],
            )

    def test_registered_frame_review_corrects_claim_and_strategy(self):
        payload = {
            "hero": "Kindred",
            "strategy": {
                "augments": [{"name": "喂呜喂呜"}],
                "items": [],
            },
            "evidence": [
                {
                    "timestamp": "00:00",
                    "kind": "frame",
                    "claim": "展示了利刃华尔兹、喂呜喂呜、巨人杀手",
                },
                {
                    "timestamp": "00:12",
                    "kind": "subtitle",
                    "claim": "字幕保持不变",
                },
            ],
        }
        registry = {
            "reviewedAt": "2026-07-29",
            "reviewType": "human-supervised-pixel-review",
            "videos": {
                "video-123": {
                    "strategyOverrides": {
                        "augments": [{"name": "魄罗蛮冲"}],
                    },
                    "frames": {
                        "00:00": {
                            "verdict": "partial",
                            "sourceClaim": "展示了利刃华尔兹、喂呜喂呜、巨人杀手",
                            "publicClaim": "强化：魄罗蛮冲",
                            "cachedPath": "frames/video-123/frame.jpg",
                            "screenshotSha256": "reviewed-sha",
                        },
                    },
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame_reviews.json"
            path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(video_intelligence, "FRAME_REVIEWS_PATH", path):
                reviewed, metadata = (
                    video_intelligence.apply_registered_frame_reviews(
                        payload,
                        {"videoId": "video-123"},
                    )
                )

        self.assertEqual(
            reviewed["evidence"][0]["claim"],
            "强化：魄罗蛮冲",
        )
        self.assertEqual(
            reviewed["evidence"][1]["claim"],
            "字幕保持不变",
        )
        self.assertEqual(
            video_intelligence.strategy_names(
                reviewed["strategy"]["augments"]
            ),
            ["魄罗蛮冲"],
        )
        self.assertEqual(metadata["status"], "applied")
        self.assertEqual(metadata["errors"], [])

    def test_registered_evidence_review_corrects_type_and_timestamp(self):
        payload = {
            "evidence": [
                {
                    "timestamp": "00:09",
                    "kind": "subtitle",
                    "claim": "生成笔记误绑的结论",
                },
            ],
        }
        transcript = {
            "segments": [
                {
                    "start": 12.92,
                    "end": 14.4,
                    "text": "原始语音分段",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note_results = root / "note_results"
            note_results.mkdir()
            transcript_path = note_results / "task-123_transcript.json"
            transcript_path.write_text(
                json.dumps(transcript, ensure_ascii=False),
                encoding="utf-8",
            )
            transcript_sha = hashlib.sha256(
                transcript_path.read_bytes()
            ).hexdigest()
            registry = {
                "reviewedAt": "2026-07-29",
                "reviewType": "human-supervised-source-review",
                "videos": {
                    "video-123": {
                        "evidence": [
                            {
                                "source": payload["evidence"][0],
                                "verdict": "partial",
                                "public": {
                                    "timestamp": "00:13",
                                    "kind": "audio",
                                    "claim": "语音复核后的结论",
                                },
                                "transcript": {
                                    "taskId": "task-123",
                                    "sha256": transcript_sha,
                                    "segment": transcript["segments"][0],
                                },
                            },
                        ],
                    },
                },
            }
            registry_path = root / "evidence_reviews.json"
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    video_intelligence,
                    "EVIDENCE_REVIEWS_PATH",
                    registry_path,
                ),
                mock.patch.object(
                    video_intelligence,
                    "bilinote_storage_paths",
                    return_value=(note_results, root / "screenshots"),
                ),
            ):
                reviewed, metadata = (
                    video_intelligence.apply_registered_evidence_reviews(
                        payload,
                        {"videoId": "video-123"},
                    )
                )

        self.assertEqual(
            reviewed["evidence"],
            [
                {
                    "timestamp": "00:13",
                    "kind": "audio",
                    "claim": "语音复核后的结论",
                },
            ],
        )
        self.assertEqual(metadata["status"], "applied")
        self.assertEqual(metadata["errors"], [])

    def test_registered_evidence_review_fails_closed_on_transcript_drift(self):
        payload = {
            "evidence": [
                {
                    "timestamp": "00:00",
                    "kind": "subtitle",
                    "claim": "目标英雄",
                },
            ],
        }
        registry = {
            "videos": {
                "video-123": {
                    "evidence": [
                        {
                            "source": payload["evidence"][0],
                            "verdict": "supported",
                            "public": {
                                "timestamp": "00:00",
                                "kind": "audio",
                                "claim": "目标英雄",
                            },
                            "transcript": {
                                "taskId": "task-123",
                                "sha256": "expected-sha",
                                "segment": {
                                    "start": 0.0,
                                    "end": 1.0,
                                    "text": "目标英雄",
                                },
                            },
                        },
                    ],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note_results = root / "note_results"
            note_results.mkdir()
            (note_results / "task-123_transcript.json").write_text(
                '{"segments":[]}',
                encoding="utf-8",
            )
            registry_path = root / "evidence_reviews.json"
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    video_intelligence,
                    "EVIDENCE_REVIEWS_PATH",
                    registry_path,
                ),
                mock.patch.object(
                    video_intelligence,
                    "bilinote_storage_paths",
                    return_value=(note_results, root / "screenshots"),
                ),
            ):
                reviewed, metadata = (
                    video_intelligence.apply_registered_evidence_reviews(
                        payload,
                        {"videoId": "video-123"},
                    )
                )

        self.assertEqual(reviewed["evidence"], payload["evidence"])
        self.assertEqual(metadata["status"], "invalid")
        self.assertEqual(
            metadata["errors"],
            ["语音复核原始转写哈希不匹配 task-123"],
        )

    def test_frame_review_gate_requires_reviewed_screenshot_hash(self):
        draft = {
            "catalogRecord": {
                "confidence": 0.8,
                "evidence": [
                    {"timestamp": "00:15", "kind": "frame", "claim": "画面"},
                ],
            },
            "qualityGate": {
                "passed": True,
                "minimumConfidence": 0.68,
                "errors": [],
            },
            "frameReview": {
                "status": "applied",
                "errors": [],
                "reviewedFrames": [
                    {
                        "timestamp": "00:15",
                        "screenshotSha256": "reviewed-sha",
                    },
                ],
            },
            "bilinote": {
                "screenshotManifest": {
                    "status": "captured",
                    "frames": [
                        {
                            "timestamp": "00:15",
                            "sha256": "different-sha",
                        },
                    ],
                },
            },
        }
        updated = video_intelligence.apply_screenshot_manifest_gate(draft)
        self.assertFalse(updated["qualityGate"]["passed"])
        self.assertEqual(
            updated["qualityGate"]["errors"],
            ["逐帧复核截图哈希不匹配 00:15"],
        )

    def test_validate_analysis_accepts_current_kiwi_augment_without_stats(self):
        payload = {
            "schemaVersion": 1,
            "hero": "Kayle",
            "summary": "视频展示了凯尔的攻击特效玩法。",
            "strategy": {
                "augments": [{"name": "双重打击"}],
                "items": [{"name": "纳什之牙", "order": 1}],
            },
            "evidence": [
                {
                    "timestamp": "00:15",
                    "kind": "frame",
                    "claim": "画面显示凯尔选择双重打击，装备栏显示纳什之牙",
                },
                {"timestamp": "00:18", "kind": "audio", "claim": "作者解释攻击特效"},
            ],
            "confidence": 0.8,
        }
        self.assertEqual(video_intelligence.validate_analysis(payload, "Kayle"), [])

    def test_save_json_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "result.json"
            video_intelligence.save_json(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
