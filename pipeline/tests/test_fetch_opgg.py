import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "pipeline" / "fetch_opgg.py"
SPEC = importlib.util.spec_from_file_location("fetch_opgg", MODULE_PATH)
fetch_opgg = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(fetch_opgg)


class FetchOpggVersionTests(unittest.TestCase):
    def test_extracts_page_asset_version(self):
        html = (
            '<img src="https://opgg-static.akamaized.net/meta/images/'
            'lol/16.14.1/item/1001.png">'
        )
        self.assertEqual(fetch_opgg.extract_asset_version(html), "16.14.1")

    def test_rejects_missing_or_mixed_page_versions(self):
        with self.assertRaises(ValueError):
            fetch_opgg.extract_asset_version("<html></html>")
        with self.assertRaises(ValueError):
            fetch_opgg.extract_asset_version(
                "https://opgg-static.akamaized.net/meta/images/lol/16.14.1/item/1.png "
                "https://opgg-static.akamaized.net/meta/images/lol/16.15.1/item/2.png"
            )

    def test_filters_duplicate_and_malformed_core_item_rows(self):
        rows = [
            [6676, 3031, 3072],
            [3031, 6676, 3031],
            [3134, 126697, 6676],
            [6676, 3031, 3072],
            [1, 2],
            ["1", 2, 3],
        ]

        self.assertEqual(
            fetch_opgg.valid_item_core_rows(rows),
            [
                [6676, 3031, 3072],
                [3134, 126697, 6676],
            ],
        )


if __name__ == "__main__":
    unittest.main()
