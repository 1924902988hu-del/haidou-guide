import copy
import json
import os
import tempfile
import unittest

import fetch_hexdata


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, separators=(",", ":"))


class HexdataCacheManifestTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = self.tempdir.name
        self.latest = {
            "buildId": "hexdata-2026-07-24-abcdef123456",
            "reportPatch": "16.14",
            "reportDate": "2026-07-24",
            "generatedAt": "2026-07-25T00:00:00Z",
            "heroCount": 2,
            "augmentCount": 1,
        }
        write_json(
            os.path.join(self.root, "meta.json"),
            {
                "buildId": self.latest["buildId"],
                "reportPatch": self.latest["reportPatch"],
                "reportDate": self.latest["reportDate"],
                "heroCount": 2,
                "augmentCount": 1,
                "corePayloadHash": "abcdef123456" + "0" * 52,
            },
        )
        write_json(os.path.join(self.root, "heroes.json"), [{"id": "1"}, {"id": "2"}])
        write_json(os.path.join(self.root, "augments.json"), [{"id": "10"}])
        write_json(
            os.path.join(self.root, "hero_formula_items.json"),
            {"byHeroId": {"1": {}, "2": {}}},
        )
        for hero_id in ("1", "2"):
            write_json(
                os.path.join(self.root, "heroes", f"{hero_id}.json"),
                {"augments": [], "items": [], "trios": []},
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def write_manifest(self):
        ids = fetch_hexdata._validate_payload(self.root, self.latest)
        manifest = fetch_hexdata._make_manifest(self.root, self.latest, ids)
        write_json(os.path.join(self.root, "_buildId.json"), manifest)
        return manifest

    def test_complete_manifest_verifies(self):
        manifest = self.write_manifest()
        self.assertEqual(
            fetch_hexdata.verify_cache_manifest(self.root)["buildId"],
            manifest["buildId"],
        )

    def test_tampered_hero_detail_fails_closed(self):
        self.write_manifest()
        write_json(
            os.path.join(self.root, "heroes", "1.json"),
            {"augments": [{"augmentId": "10"}], "items": [], "trios": []},
        )
        with self.assertRaisesRegex(ValueError, "哈希不一致"):
            fetch_hexdata.verify_cache_manifest(self.root)

    def test_manifest_cannot_omit_a_hero_detail(self):
        manifest = self.write_manifest()
        broken = copy.deepcopy(manifest)
        broken["files"].pop("heroes/2.json")
        write_json(os.path.join(self.root, "_buildId.json"), broken)
        with self.assertRaisesRegex(ValueError, "文件集合"):
            fetch_hexdata.verify_cache_manifest(self.root)

    def test_meta_build_must_match_archive(self):
        write_json(
            os.path.join(self.root, "meta.json"),
            {
                "buildId": "hexdata-other",
                "reportPatch": "16.14",
                "reportDate": "2026-07-24",
                "heroCount": 2,
                "augmentCount": 1,
                "corePayloadHash": "abcdef123456" + "0" * 52,
            },
        )
        with self.assertRaisesRegex(ValueError, "buildId 不一致"):
            fetch_hexdata._validate_payload(self.root, self.latest)


if __name__ == "__main__":
    unittest.main()
