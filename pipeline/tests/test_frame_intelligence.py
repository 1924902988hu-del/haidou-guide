import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from pipeline import frame_intelligence


class FrameIntelligenceTests(unittest.TestCase):
    def test_exact_icon_crop_matches_reference(self):
        reference_path = next(
            (frame_intelligence.ROOT / "site" / "assets" / "img" / "item")
            .glob("*.png")
        )
        reference = next(
            row
            for row in frame_intelligence.icon_references()
            if row["group"] == "items" and row["id"] == reference_path.stem
        )
        with Image.open(reference_path) as image:
            match = frame_intelligence.match_icon_crop(
                image.convert("RGBA"),
                [reference],
            )
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], reference_path.stem)
        self.assertEqual(match["hashDistance"], 0)

    def test_crop_vision_rectangle_converts_bottom_left_coordinates(self):
        image = Image.new("RGB", (100, 200), "black")
        crop = frame_intelligence.crop_vision_rectangle(
            image,
            [0.1, 0.7, 0.2, 0.1],
        )
        self.assertIsNotNone(crop)
        self.assertEqual(crop.size, (20, 20))

    def test_text_matches_current_strategy_vocabularies(self):
        vocabulary = {
            "augments": {"无限循环往复"},
            "items": {"纳什之牙"},
            "runes": {"征服者"},
        }
        matches = frame_intelligence.text_matches(
            "强化：无限循环往复\n装备 纳什之牙",
            vocabulary,
        )
        self.assertEqual(matches["augments"], ["无限循环往复"])
        self.assertEqual(matches["items"], ["纳什之牙"])
        self.assertEqual(matches["runes"], [])

    def test_prompt_context_is_explicitly_non_authoritative(self):
        context = frame_intelligence.prompt_context({
            "frames": [{
                "timestampSeconds": 12.5,
                "ocrTerms": ["装备推荐"],
                "vocabularyMatches": {"items": ["纳什之牙"]},
                "iconMatches": [
                    {"name": "纳什之牙", "confidence": 0.9},
                ],
            }],
        })
        self.assertIn("12.5s", context)
        self.assertIn("OCR/图标候选可能误识别", context)
        self.assertIn("不能直接当作 evidence", context)

    def test_prompt_context_samples_the_full_timeline(self):
        context = frame_intelligence.prompt_context(
            {
                "frames": [
                    {
                        "timestampSeconds": float(index),
                        "ocrTerms": ["装备推荐"],
                        "vocabularyMatches": {"items": [f"装备{index}"]},
                        "iconMatches": [],
                    }
                    for index in range(25)
                ],
            },
            max_rows=3,
        )
        self.assertIn("0.0s", context)
        self.assertIn("12.0s", context)
        self.assertIn("24.0s", context)
        self.assertNotIn("1.0s", context)

    def test_extract_sequence_uses_stable_stage_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with mock.patch.object(
                frame_intelligence.shutil,
                "which",
                return_value="/usr/local/bin/ffmpeg",
            ), mock.patch.object(
                frame_intelligence.subprocess,
                "run",
            ):
                (output / "coarse-0000.jpg").write_bytes(b"x")
                (output / "coarse-0001.jpg").write_bytes(b"x")
                rows = frame_intelligence.extract_sequence(
                    Path("/tmp/video.mp4"),
                    output,
                    prefix="coarse",
                    start=3,
                    duration=6,
                    fps=0.5,
                    stage="coarse",
                )
        self.assertEqual(
            [row["timestampSeconds"] for row in rows],
            [3.0, 5.0],
        )
        self.assertEqual({row["stage"] for row in rows}, {"coarse"})


if __name__ == "__main__":
    unittest.main()
