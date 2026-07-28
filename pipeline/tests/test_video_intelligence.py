import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "video_intelligence.py"
SPEC = importlib.util.spec_from_file_location("video_intelligence", MODULE_PATH)
video_intelligence = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(video_intelligence)


class VideoIntelligenceTests(unittest.TestCase):
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
                {"timestamp": "00:12", "kind": "subtitle", "claim": "字幕提到强化"},
                {"timestamp": "00:21", "kind": "audio", "claim": "语音解释装备"},
            ],
            "confidence": 0.8,
        }
        errors = video_intelligence.validate_analysis(payload, "Blitzcrank")
        self.assertIn("缺少画面证据，不能标记为多模态已读", errors)
        payload["evidence"].append(
            {"timestamp": "00:25", "kind": "frame", "claim": "画面展示装备栏"}
        )
        self.assertEqual(video_intelligence.validate_analysis(payload, "Blitzcrank"), [])

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
            "caveat": "",
        }
        record = video_intelligence.catalog_record(candidate, analysis, "26.15")
        self.assertEqual(record["patchStatus"], "needs-game-check")
        self.assertIn("26.15", record["caveat"])
        analysis["patchMentioned"] = "26.15"
        current = video_intelligence.catalog_record(candidate, analysis, "26.15")
        self.assertEqual(current["patchStatus"], "current")

    def test_save_json_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "result.json"
            video_intelligence.save_json(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
