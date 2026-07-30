import importlib.util
import json
import stat
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "pipeline" / "build_site_data.py"
SPEC = importlib.util.spec_from_file_location("build_site_data", MODULE_PATH)
build_site_data = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(build_site_data)

import validate_site_data
import fetch_images


class PublicSiteContractTests(unittest.TestCase):
    def run_common_script(self, expression):
        common_script = (ROOT / "site" / "assets" / "common.js").read_text(
            encoding="utf-8"
        )
        script = (
            "global.document = { getElementById: () => null };\n"
            f"{common_script}\n"
            f"console.log(JSON.stringify({expression}));"
        )
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_public_video_projection_omits_internal_review_metadata(self):
        source = {
            "id": "douyin-test",
            "title": "测试攻略",
            "confidence": 0.8,
            "discoveredAt": "2026-07-29",
            "durationSeconds": 30,
            "engagement": {"plays": 0},
            "evidenceReview": {"status": "source-reviewed"},
            "evidence": [{"timestamp": "00:01", "kind": "frame"}],
            "frameReview": {"status": "pixel-reviewed"},
            "reviewedAt": "2026-07-29",
        }

        projected = build_site_data.public_video_record(source)

        self.assertEqual(set(projected), {"id", "title", "evidence"})
        self.assertEqual(projected["id"], source["id"])
        self.assertEqual(projected["evidence"], source["evidence"])

    def test_preserved_core_items_are_registered_in_image_manifest(self):
        rows = [[
            {
                "id": 1038,
                "name": "暴风之剑",
                "icon": "assets/img/item/1038.png",
            },
            {
                "id": 3097,
                "name": "岚切",
                "icon": "assets/img/item/3097.png",
            },
            {
                "id": 2512,
                "name": "猎魔人弩箭",
                "icon": "assets/img/item/2512.png",
            },
        ]]
        original_manifest = dict(build_site_data.IMG_MANIFEST)
        try:
            build_site_data.IMG_MANIFEST.clear()
            self.assertIs(
                build_site_data.register_preserved_item_assets(rows, "16.14.1"),
                rows,
            )
            self.assertEqual(
                build_site_data.IMG_MANIFEST,
                {
                    "assets/img/item/1038.png": (
                        "https://ddragon.leagueoflegends.com/cdn/"
                        "16.14.1/img/item/1038.png"
                    ),
                    "assets/img/item/3097.png": (
                        "https://ddragon.leagueoflegends.com/cdn/"
                        "16.14.1/img/item/3097.png"
                    ),
                    "assets/img/item/2512.png": (
                        "https://ddragon.leagueoflegends.com/cdn/"
                        "16.14.1/img/item/2512.png"
                    ),
                },
            )
            with self.assertRaisesRegex(ValueError, "图片路径无效"):
                build_site_data.register_preserved_item_assets(
                    [[{
                        "id": 1038,
                        "name": "暴风之剑",
                        "icon": "../1038.png",
                    }]],
                    "16.14.1",
                )
        finally:
            build_site_data.IMG_MANIFEST.clear()
            build_site_data.IMG_MANIFEST.update(original_manifest)

    def test_stale_image_pruning_is_scoped_and_manifest_driven(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            site = Path(temp_dir) / "site"
            image_dir = site / "assets" / "img" / "item"
            image_dir.mkdir(parents=True)
            keep = image_dir / "1001.png"
            stale = image_dir / "1002.png"
            outside = site / "do-not-touch.png"
            keep.write_bytes(b"keep")
            stale.write_bytes(b"stale")
            outside.write_bytes(b"outside")
            manifest = {
                "assets/img/item/1001.png": (
                    "https://ddragon.leagueoflegends.com/cdn/"
                    "16.15.1/img/item/1001.png"
                ),
            }

            self.assertEqual(
                fetch_images.prune_stale_images(manifest, site=str(site)),
                ["assets/img/item/1002.png"],
            )
            self.assertTrue(keep.exists())
            self.assertFalse(stale.exists())
            self.assertTrue(outside.exists())

            with self.assertRaisesRegex(ValueError, "图片清单路径无效"):
                fetch_images.prune_stale_images(
                    {
                        "../do-not-touch.png": (
                            "https://ddragon.leagueoflegends.com/cdn/"
                            "16.15.1/img/item/1001.png"
                        ),
                    },
                    site=str(site),
                )
            self.assertTrue(outside.exists())

    def test_corrupt_image_is_replaced_only_after_atomic_payload_validation(self):
        valid_bytes = (
            ROOT
            / "site"
            / "assets"
            / "img"
            / "champion"
            / "Thresh.png"
        ).read_bytes()
        with tempfile.TemporaryDirectory() as temp_dir:
            site = Path(temp_dir) / "site"
            image_dir = site / "assets" / "img" / "champion"
            image_dir.mkdir(parents=True)
            valid = image_dir / "Valid.png"
            corrupt = image_dir / "Corrupt.png"
            valid.write_bytes(valid_bytes)
            corrupt.write_bytes(b"existing-corruption")
            corrupt.chmod(0o640)
            manifest = {
                "assets/img/champion/Valid.png": (
                    "https://ddragon.leagueoflegends.com/cdn/"
                    "16.15.1/img/champion/Valid.png"
                ),
                "assets/img/champion/Corrupt.png": (
                    "https://ddragon.leagueoflegends.com/cdn/"
                    "16.15.1/img/champion/Corrupt.png"
                ),
                "assets/img/champion/Missing.png": (
                    "https://ddragon.leagueoflegends.com/cdn/"
                    "16.15.1/img/champion/Missing.png"
                ),
            }

            missing, damaged = fetch_images.image_refresh_candidates(
                manifest,
                site=str(site),
                ddragon_version="16.15.1",
            )
            self.assertEqual(
                missing,
                {
                    "assets/img/champion/Missing.png": (
                        "https://ddragon.leagueoflegends.com/cdn/"
                        "16.15.1/img/champion/Missing.png"
                    ),
                },
            )
            self.assertEqual(
                damaged,
                {
                    "assets/img/champion/Corrupt.png": (
                        "https://ddragon.leagueoflegends.com/cdn/"
                        "16.15.1/img/champion/Corrupt.png"
                    ),
                },
            )

            with self.assertRaisesRegex(ValueError, "下载图片不可解码"):
                fetch_images.download_image_atomic(
                    "assets/img/champion/Corrupt.png",
                    (
                        "https://ddragon.leagueoflegends.com/cdn/"
                        "16.15.1/img/champion/Corrupt.png"
                    ),
                    site=str(site),
                    fetcher=lambda *args, **kwargs: b"new-corruption",
                    ddragon_version="16.15.1",
                )
            self.assertEqual(corrupt.read_bytes(), b"existing-corruption")
            self.assertEqual(
                list(image_dir.glob(".image-download-*.tmp")),
                [],
            )

            fetch_images.download_image_atomic(
                "assets/img/champion/Corrupt.png",
                (
                    "https://ddragon.leagueoflegends.com/cdn/"
                    "16.15.1/img/champion/Corrupt.png"
                ),
                site=str(site),
                fetcher=lambda *args, **kwargs: valid_bytes,
                ddragon_version="16.15.1",
            )
            self.assertEqual(corrupt.read_bytes(), valid_bytes)
            self.assertEqual(stat.S_IMODE(corrupt.stat().st_mode), 0o640)
            errors = []
            self.assertEqual(
                validate_site_data.check_png_asset(
                    corrupt,
                    errors,
                    "repaired",
                ),
                (128, 128),
            )
            self.assertEqual(errors, [])

    def test_abandoned_image_download_temps_are_scoped_and_cleaned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            site = Path(temp_dir) / "site"
            image_dir = site / "assets" / "img" / "item"
            image_dir.mkdir(parents=True)
            abandoned = image_dir / ".image-download-abandoned.tmp"
            unrelated = image_dir / "unrelated.tmp"
            outside = site / ".image-download-outside.tmp"
            abandoned.write_bytes(b"partial")
            unrelated.write_bytes(b"keep")
            outside.write_bytes(b"keep")

            self.assertEqual(
                fetch_images.cleanup_abandoned_downloads(site=str(site)),
                ["assets/img/item/.image-download-abandoned.tmp"],
            )
            self.assertFalse(abandoned.exists())
            self.assertTrue(unrelated.exists())
            self.assertTrue(outside.exists())

    def test_image_manifest_sources_match_asset_type_and_snapshot(self):
        valid = {
            "assets/img/champion/Aatrox.png": (
                "https://ddragon.leagueoflegends.com/cdn/"
                "16.15.1/img/champion/Aatrox.png"
            ),
            "assets/img/item/1001.png": (
                "https://ddragon.leagueoflegends.com/cdn/"
                "16.15.1/img/item/1001.png"
            ),
            "assets/img/spell/SummonerFlash.png": (
                "https://ddragon.leagueoflegends.com/cdn/"
                "16.15.1/img/spell/SummonerFlash.png"
            ),
            "assets/img/rune/Styles_7201_Precision.png": (
                "https://ddragon.leagueoflegends.com/cdn/img/"
                "perk-images/Styles/7201_Precision.png"
            ),
            "assets/img/augment/2095.png": (
                "https://raw.communitydragon.org/latest/plugins/"
                "rcp-be-lol-game-data/global/default/assets/ux/kiwi/"
                "augments/icons/highroller_small.png"
            ),
            "assets/img/augment/1133.png": (
                "https://raw.communitydragon.org/latest/plugins/"
                "rcp-be-lol-game-data/global/default/assets/maps/particles/"
                "kiwi/magicmissile_small.png"
            ),
        }
        fetch_images.validate_manifest(valid, ddragon_version="16.15.1")

        invalid_cases = (
            (
                "第三方主机",
                "assets/img/item/1001.png",
                "https://example.com/1001.png",
            ),
            (
                "版本漂移",
                "assets/img/item/1001.png",
                "https://ddragon.leagueoflegends.com/cdn/"
                "16.14.1/img/item/1001.png",
            ),
            (
                "类型错配",
                "assets/img/item/1001.png",
                "https://ddragon.leagueoflegends.com/cdn/"
                "16.15.1/img/champion/1001.png",
            ),
            (
                "文件名漂移",
                "assets/img/champion/Aatrox.png",
                "https://ddragon.leagueoflegends.com/cdn/"
                "16.15.1/img/champion/Ahri.png",
            ),
            (
                "查询参数",
                "assets/img/spell/SummonerFlash.png",
                "https://ddragon.leagueoflegends.com/cdn/"
                "16.15.1/img/spell/SummonerFlash.png?fallback=1",
            ),
            (
                "CommunityDragon 路径漂移",
                "assets/img/augment/2095.png",
                "https://raw.communitydragon.org/latest/game/"
                "highroller_small.png",
            ),
        )
        for label, rel, url in invalid_cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "来源无效"):
                    fetch_images.validate_manifest(
                        {rel: url},
                        ddragon_version="16.15.1",
                    )

    def test_index_video_availability_matches_public_expiry_contract(self):
        source = {
            "expiresAt": "2026-08-05",
            "patchStatus": "needs-game-check",
            "confidence": 0.8,
        }

        self.assertEqual(
            build_site_data.public_video_availability(source),
            {
                "expiresAt": "2026-08-05",
                "patchStatus": "needs-game-check",
            },
        )

    def test_frontend_video_expiry_is_inclusive_without_date_parsing(self):
        results = self.run_common_script(
            "["
            "localCalendarDateISO(new Date(2026, 7, 5, 23, 59)),"
            "calendarAgeLabel('2026-07-24', '2026-07-29'),"
            "calendarAgeLabel('2026-07-29', '2026-07-29'),"
            "calendarAgeLabel('2026-07-30', '2026-07-29'),"
            "calendarAgeLabel('2026-02-29', '2026-07-29'),"
            "videoIsWithinPublicationWindow({expiresAt:'2026-08-05'}, '2026-08-05'),"
            "videoIsWithinPublicationWindow({expiresAt:'2026-08-05'}, '2026-08-06'),"
            "videoIsWithinPublicationWindow({expiresAt:'invalid'}, '2026-08-05'),"
            "[{expiresAt:'2026-08-05'}]"
            ".filter(video => videoIsWithinPublicationWindow(video, '2026-08-05')).length,"
            "[{expiresAt:'2026-08-05'}]"
            ".filter(video => videoIsWithinPublicationWindow(video, '2026-08-06')).length"
            "]"
        )

        self.assertEqual(
            results,
            ["2026-08-05", "5天前", "今天", "", "", True, False, False, 1, 0],
        )

    def test_both_pages_apply_runtime_video_expiry(self):
        hero_script = (ROOT / "site" / "assets" / "hero.js").read_text(
            encoding="utf-8"
        )
        index_script = (ROOT / "site" / "assets" / "index.js").read_text(
            encoding="utf-8"
        )

        expected_filter = ".filter(video => videoIsWithinPublicationWindow(video))"
        self.assertIn(expected_filter, hero_script)
        self.assertIn(expected_filter, index_script)
        self.assertIn("videoAvailability", index_script)

    def test_hero_script_does_not_render_model_confidence(self):
        hero_script = (ROOT / "site" / "assets" / "hero.js").read_text(
            encoding="utf-8"
        )
        index_script = (ROOT / "site" / "assets" / "index.js").read_text(
            encoding="utf-8"
        )
        common_script = (ROOT / "site" / "assets" / "common.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("v.confidence", hero_script)
        self.assertNotIn("可信度", hero_script)
        self.assertNotIn("当前版本", hero_script)
        self.assertNotIn("当前版本", index_script)
        self.assertIn("客户端资料", common_script)
        self.assertIn("OP.GG 攻略", common_script)
        self.assertIn("统计快照", common_script)
        self.assertIn("统计截至", common_script)
        self.assertIn("calendarAgeLabel", common_script)

    def test_search_input_keeps_a_visible_focus_indicator(self):
        stylesheet = (ROOT / "site" / "assets" / "style.css").read_text(
            encoding="utf-8"
        )

        self.assertIn(".searchbox:focus-within", stylesheet)
        self.assertIn("box-shadow: 0 0 0 2px var(--blue)", stylesheet)

    def test_search_rejects_single_latin_letters_and_announces_results(self):
        index_markup = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        index_script = (ROOT / "site" / "assets" / "index.js").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'id="searchStatus" role="status" aria-atomic="true"',
            index_markup,
        )
        self.assertIn('aria-describedby="searchHelp"', index_markup)
        self.assertIn(
            "英文搜索至少输入两个字母；中文昵称可以输入一个字。",
            index_markup,
        )
        self.assertIn(
            "const needsMoreLatinInput = /^[a-z]$/u.test(query)",
            index_script,
        )
        self.assertIn(
            "const scored = needsMoreLatinInput ? []",
            index_script,
        )
        self.assertIn(
            "searchStatus.textContent = needsMoreLatinInput",
            index_script,
        )

    def test_index_load_failure_has_consistent_accessible_recovery(self):
        index_markup = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        index_script = (ROOT / "site" / "assets" / "index.js").read_text(
            encoding="utf-8"
        )
        stylesheet = (ROOT / "site" / "assets" / "style.css").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'id="loadError" role="alert" aria-atomic="true"',
            index_markup,
        )
        self.assertIn("searchInput.disabled = true", index_script)
        self.assertIn("tabs.hidden = true", index_script)
        self.assertIn("resultMeta.textContent = '加载失败'", index_script)
        self.assertIn("patchBadge.textContent = '资料暂不可用'", index_script)
        self.assertIn("loadError.replaceChildren(title, help, retry)", index_script)
        self.assertIn(
            "retry.addEventListener('click', () => window.location.reload())",
            index_script,
        )
        self.assertNotIn("error.textContent = err.message", index_script)
        self.assertIn(".load-error:not(:empty)", stylesheet)
        self.assertIn(".role-tabs[hidden] { display: none; }", stylesheet)
        self.assertIn("min-height: 44px", stylesheet)

    def test_shared_json_loader_aborts_stalled_requests(self):
        common_script = (ROOT / "site" / "assets" / "common.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("const JSON_LOAD_TIMEOUT_MS = 20000", common_script)
        self.assertIn("new AbortController()", common_script)
        self.assertIn("setTimeout(() => controller.abort(), timeoutMs)", common_script)
        self.assertIn("signal: controller.signal", common_script)
        self.assertIn("cache: 'default'", common_script)
        self.assertNotIn("cache: 'no-cache'", common_script)
        self.assertNotIn("cache: 'force-cache'", common_script)
        self.assertNotIn("cache: 'no-store'", common_script)
        self.assertIn("return await response.json()", common_script)
        self.assertIn("clearTimeout(timeoutId)", common_script)
        self.assertNotIn("AbortSignal.timeout(", common_script)

        script = (
            "global.document = { getElementById: () => null };\n"
            f"{common_script}\n"
            "const nativeClearTimeout = global.clearTimeout;\n"
            "let clearedTimers = 0;\n"
            "global.clearTimeout = timer => {"
            " clearedTimers += 1; nativeClearTimeout(timer);"
            "};\n"
            "(async () => {\n"
            "  global.fetch = async () => ({"
            " ok: true, json: async () => ({ready: true})"
            " });\n"
            "  const success = await loadJSON('success.json', 1000);\n"
            "  global.fetch = (url, options) => new Promise((resolve, reject) => {"
            " options.signal.addEventListener('abort', () => reject(new Error('aborted')));"
            " });\n"
            "  const startedAt = Date.now();\n"
            "  let timeoutMessage = '';\n"
            "  try { await loadJSON('stalled.json', 25); }"
            " catch (error) { timeoutMessage = error.message; }\n"
            "  console.log(JSON.stringify({"
            " success, clearedTimers, timeoutMessage,"
            " elapsed: Date.now() - startedAt"
            " }));\n"
            "})();"
        )
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        behavior = json.loads(result.stdout)
        self.assertEqual(behavior["success"], {"ready": True})
        self.assertEqual(behavior["clearedTimers"], 2)
        self.assertEqual(behavior["timeoutMessage"], "加载超时 stalled.json")
        self.assertGreaterEqual(behavior["elapsed"], 20)
        self.assertLess(behavior["elapsed"], 500)

    def test_role_filter_updates_buttons_without_replacing_focused_node(self):
        index_script = (ROOT / "site" / "assets" / "index.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("if (!tabs.childElementCount)", index_script)
        self.assertIn("button.classList.toggle('active', isActive)", index_script)
        self.assertIn(
            "button.setAttribute('aria-pressed', String(isActive))",
            index_script,
        )

    def test_role_filter_exposes_related_toggle_buttons_as_a_named_group(self):
        index_markup = (ROOT / "site" / "index.html").read_text(encoding="utf-8")

        self.assertIn(
            'id="roleTabs" role="group" aria-label="按定位筛选"',
            index_markup,
        )

    def test_hero_alias_and_load_errors_do_not_reach_html_injection_sinks(self):
        hero_script = (ROOT / "site" / "assets" / "hero.js").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "/^[A-Za-z][A-Za-z0-9]{0,31}$/.test(alias)",
            hero_script,
        )
        self.assertIn("encodeURIComponent(alias)", hero_script)
        self.assertIn("title.textContent = canRetry", hero_script)
        self.assertIn("help.textContent = canRetry", hero_script)
        self.assertIn("loadError.replaceChildren(...children)", hero_script)
        self.assertNotIn("err.message", hero_script)
        self.assertNotIn("${err.message}", hero_script)
        for hero_file in (ROOT / "site" / "data" / "heroes").glob("*.json"):
            self.assertRegex(hero_file.stem, r"^[A-Za-z][A-Za-z0-9]{0,31}$")

    def test_hero_load_failure_has_consistent_accessible_recovery(self):
        hero_markup = (ROOT / "site" / "hero.html").read_text(encoding="utf-8")
        hero_script = (ROOT / "site" / "assets" / "hero.js").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'id="loadError" role="alert" aria-atomic="true"',
            hero_markup,
        )
        self.assertIn(
            '<div class="patch-badge" id="patchBadge">加载中…</div>',
            hero_markup,
        )
        self.assertNotIn('id="content" aria-live=', hero_markup)
        self.assertIn("patchBadge.textContent = '资料暂不可用'", hero_script)
        self.assertIn("'英雄攻略加载失败'", hero_script)
        self.assertIn("'未找到要查看的英雄'", hero_script)
        self.assertIn("'请检查网络后重新加载，或返回英雄列表。'", hero_script)
        self.assertIn("'请返回英雄列表重新选择。'", hero_script)
        self.assertIn("if (canRetry)", hero_script)
        self.assertIn("retry.type = 'button'", hero_script)
        self.assertIn("window.location.reload()", hero_script)
        self.assertIn("showUnavailableState(false)", hero_script)
        self.assertIn("showUnavailableState(true)", hero_script)

    def test_index_card_data_and_errors_do_not_reach_html_injection_sinks(self):
        index_script = (ROOT / "site" / "assets" / "index.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("const name = escapeHTML(h.name)", index_script)
        self.assertIn("const icon = escapeHTML(h.icon)", index_script)
        self.assertIn("const tier = escapeHTML(h.tier ?? '?')", index_script)
        self.assertIn("encodeURIComponent(h.alias)", index_script)
        self.assertIn("title.textContent = '英雄数据加载失败'", index_script)
        self.assertIn("help.textContent = '请检查网络后重新加载。'", index_script)
        self.assertIn("loadError.replaceChildren(title, help, retry)", index_script)
        self.assertNotIn("err.message", index_script)
        self.assertNotIn("${err.message}", index_script)

        for alias in ("Thresh", "AurelionSol", "KSante", "R2D2"):
            self.assertTrue(validate_site_data.is_valid_hero_alias(alias))
        for alias in ("", "../Thresh", "Thresh.png", "<img>", "锤石", None, []):
            self.assertFalse(validate_site_data.is_valid_hero_alias(alias))

    def test_hero_guide_plain_text_fields_are_escaped_before_inner_html(self):
        hero_script = (ROOT / "site" / "assets" / "hero.js").read_text(
            encoding="utf-8"
        )

        for marker in (
            "escapeHTML(p.primaryStyle?.name ?? '')",
            "escapeHTML(r.name)",
            "escapeHTML(runes.source)",
            "escapeHTML(s.name)",
            "escapeHTML(a.name)",
            "escapeHTML(a.desc)",
            "escapeHTML(i.name)",
            "escapeHTML(r.item.name)",
            "escapeHTML(heroName)",
            "escapeHTML(h.name)",
            "escapeHTML(h.epithet)",
        ):
            self.assertIn(marker, hero_script)

        for raw_interpolation in (
            "${a.name}",
            "${a.desc}",
            "${i.name}",
            "${r.item.name}",
            "「${heroName} 海克斯大乱斗」",
            '<h1>${h.name} <span class="ep">${h.epithet}</span></h1>',
            'src="${h.icon}" alt="${h.name}"',
        ):
            self.assertNotIn(raw_interpolation, hero_script)

    def test_public_image_references_are_strict_local_asset_paths(self):
        errors = []
        validate_site_data.check_asset(
            "assets/img/champion/Thresh.png",
            errors,
            "valid",
        )
        self.assertEqual(errors, [])

        for path in (
            "https://evil.example/x.png",
            'x" onerror="alert(1)',
            "../outside.png",
            "assets/../outside.png",
            "assets/img/x.png?redirect=evil",
            "javascript:alert(1)",
        ):
            errors = []
            validate_site_data.check_asset(path, errors, "probe")
            self.assertEqual(len(errors), 1, path)
            self.assertIn("图片路径无效", errors[0])

    def test_png_gate_decodes_pixels_and_rejects_corruption(self):
        source = ROOT / "site" / "assets" / "img" / "champion" / "Thresh.png"
        valid_bytes = source.read_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            valid_path = Path(temp_dir) / "valid.png"
            valid_path.write_bytes(valid_bytes)
            errors = []
            self.assertEqual(
                validate_site_data.check_png_asset(valid_path, errors, "valid"),
                (128, 128),
            )
            self.assertEqual(errors, [])

            corrupt = bytearray(valid_bytes)
            offset = 8
            while offset < len(corrupt):
                length = struct.unpack(">I", corrupt[offset:offset + 4])[0]
                chunk_type = bytes(corrupt[offset + 4:offset + 8])
                data_start = offset + 8
                data_end = data_start + length
                if chunk_type == b"IDAT" and length:
                    corrupt[data_start + (length // 2)] ^= 0x01
                    corrupt[data_end:data_end + 4] = struct.pack(
                        ">I",
                        zlib.crc32(chunk_type + corrupt[data_start:data_end])
                        & 0xFFFFFFFF,
                    )
                    break
                offset = data_end + 4

            corrupt_path = Path(temp_dir) / "corrupt.png"
            corrupt_path.write_bytes(corrupt)
            errors = []
            self.assertIsNone(
                validate_site_data.check_png_asset(
                    corrupt_path,
                    errors,
                    "corrupt",
                )
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("PNG 无法完整解码", errors[0])

            zero_size = bytearray(valid_bytes)
            ihdr_data_start = 16
            zero_size[ihdr_data_start:ihdr_data_start + 4] = b"\0\0\0\0"
            ihdr_data_end = ihdr_data_start + 13
            zero_size[ihdr_data_end:ihdr_data_end + 4] = struct.pack(
                ">I",
                zlib.crc32(b"IHDR" + zero_size[ihdr_data_start:ihdr_data_end])
                & 0xFFFFFFFF,
            )
            zero_path = Path(temp_dir) / "zero.png"
            zero_path.write_bytes(zero_size)
            errors = []
            self.assertIsNone(
                validate_site_data.check_png_asset(zero_path, errors, "zero")
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("PNG 尺寸必须大于零", errors[0])

    def test_redundant_icons_use_empty_alternatives(self):
        index_script = (ROOT / "site" / "assets" / "index.js").read_text(
            encoding="utf-8"
        )
        hero_script = (ROOT / "site" / "assets" / "hero.js").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            '<img src="${icon}" alt="" loading="lazy" width="44" height="44">',
            index_script,
        )
        for marker in (
            '<img src="${escapeHTML(p.primaryStyle?.icon)}" alt="">',
            '<img src="${escapeHTML(p.subStyle?.icon)}" alt="">',
            '<img src="${escapeHTML(s.icon)}" alt="">',
            '<img src="${icon}" alt="" loading="lazy">',
            '<img src="${escapeHTML(a.icon)}" alt="">',
            '<img src="${escapeHTML(i.icon)}" alt="" loading="lazy">',
            '<img src="${escapeHTML(r.item.icon)}" alt="" loading="lazy">',
            '<img class="avatar" src="${escapeHTML(h.icon)}" alt="">',
        ):
            self.assertIn(marker, hero_script)

        self.assertEqual(
            hero_script.count('alt="${escapeHTML(i.name)}"'),
            1,
            "只有没有邻近文字的核心三件套图标应保留装备名替代文本",
        )
        self.assertNotIn('title="${escapeHTML(s.name)}"', hero_script)
        self.assertNotIn('title="${escapeHTML(a.name)}"', hero_script)

    def test_hero_stats_require_finite_values_and_business_ranges(self):
        valid = {
            "tier": 3,
            "winRate": 0.5,
            "pickRate": 0.01,
            "games": 100,
            "kda": 3.2,
        }
        errors = []
        validate_site_data.check_hero_stats(valid, errors, "valid")
        self.assertEqual(errors, [])

        invalid_cases = (
            ("tier", 0, "段位必须是 1–5 的整数"),
            ("winRate", "0.5", "胜率必须是 0–1 的有限数值"),
            ("winRate", float("inf"), "胜率必须是 0–1 的有限数值"),
            ("pickRate", -0.1, "登场率必须是 0–1 的有限数值"),
            ("games", 1.5, "样本场次必须是非负整数"),
            ("kda", float("nan"), "KDA 必须是非负有限数值"),
        )
        for field, value, message in invalid_cases:
            with self.subTest(field=field, value=value):
                errors = []
                validate_site_data.check_hero_stats(
                    {**valid, field: value},
                    errors,
                    "probe",
                )
                self.assertEqual(errors, [f"probe: {message}"])

    def test_fixed_guide_fields_use_complete_allowlisted_enums(self):
        errors = []
        validate_site_data.check_hero_roles(
            ["support", "tank"],
            errors,
            "valid",
        )
        validate_site_data.check_skill_plan(
            {
                "priority": ["Q", "E", "W"],
                "sequence": list("QEWQQRQEQEREEWW"),
            },
            errors,
            "valid",
        )
        validate_site_data.check_augment_rarity("黄金", errors, "valid")
        self.assertEqual(errors, [])

        role_cases = (
            [],
            ["support", "support"],
            ["support", "unknown"],
            "support",
        )
        for roles in role_cases:
            with self.subTest(roles=roles):
                errors = []
                validate_site_data.check_hero_roles(roles, errors, "probe")
                self.assertEqual(
                    errors,
                    ["probe: 定位必须为 1–3 个互不重复的已知值"],
                )

        skill_cases = (
            (
                {"priority": ["Q", "Q", "W"], "sequence": list("Q" * 15)},
                "probe: 技能优先级必须是 3 个不重复的 Q/W/E/R",
            ),
            (
                {"priority": [{"Q": True}, "E", "W"], "sequence": list("Q" * 15)},
                "probe: 技能优先级必须是 3 个不重复的 Q/W/E/R",
            ),
            (
                {"priority": ["Q", "E", "W"], "sequence": list("Q" * 14 + "X")},
                "probe: 技能序列必须是 15 个 Q/W/E/R",
            ),
        )
        for skills, message in skill_cases:
            with self.subTest(skills=skills):
                errors = []
                validate_site_data.check_skill_plan(skills, errors, "probe")
                self.assertEqual(errors, [message])

        for rarity in ("", "金", None):
            with self.subTest(rarity=rarity):
                errors = []
                validate_site_data.check_augment_rarity(rarity, errors, "probe")
                self.assertEqual(errors, ["probe: 强化稀有度无效"])

    def test_item_sections_require_renderable_distinct_item_records(self):
        items = json.loads(
            (ROOT / "site" / "data" / "heroes" / "Thresh.json").read_text(
                encoding="utf-8"
            )
        )["items"]
        errors = []
        validate_site_data.check_item_sections(items, errors, "valid")
        self.assertEqual(errors, [])

        invalid = json.loads(json.dumps(items))
        invalid["opggCores"][0][2] = invalid["opggCores"][0][0]
        errors = []
        validate_site_data.check_item_sections(invalid, errors, "probe")
        self.assertIn(
            "probe/opggCores: 每行必须由 3 件不同合法装备组成且组合不重复",
            errors,
        )

        invalid = json.loads(json.dumps(items))
        invalid["starter"][0]["name"] = ""
        errors = []
        validate_site_data.check_item_sections(invalid, errors, "probe")
        self.assertIn("probe/starter: 装备名称不能为空", errors)

        invalid = json.loads(json.dumps(items))
        invalid["hexTop"][0]["hexScore"] = 95.1
        errors = []
        validate_site_data.check_item_sections(invalid, errors, "probe")
        self.assertIn(
            "probe/hexTop/1: 单件榜记录包含未公开字段 ['hexScore']",
            errors,
        )

        errors = []
        validate_site_data.check_item_sections([], errors, "probe")
        self.assertEqual(errors, ["probe: 出装数据必须是对象"])

    def test_augment_sections_hide_win_rate_tiers_and_require_complete_combos(self):
        detail = json.loads(
            (ROOT / "site" / "data" / "heroes" / "Thresh.json").read_text(
                encoding="utf-8"
            )
        )
        augments = detail["augments"]
        combos = detail["combos"]
        errors = []
        validate_site_data.check_augment_sections(
            augments,
            combos,
            errors,
            "valid",
        )
        self.assertEqual(errors, [])

        invalid_augments = json.loads(json.dumps(augments))
        invalid_augments[1]["id"] = invalid_augments[0]["id"]
        invalid_augments[1]["name"] = invalid_augments[0]["name"]
        invalid_augments[1]["desc"] = ""
        invalid_augments[1]["opgg"] = "yes"
        invalid_augments[1]["hexScore"] = 99.9
        errors = []
        validate_site_data.check_augment_sections(
            invalid_augments,
            combos,
            errors,
            "probe",
        )
        self.assertIn("probe/augments: 强化推荐存在重复 ID", errors)
        self.assertIn("probe/augments: 强化推荐存在重复名称", errors)
        self.assertIn("probe/augments/2: 强化描述不能为空", errors)
        self.assertIn("probe/augments/2: OP.GG 推荐标记必须是布尔值", errors)
        self.assertIn(
            "probe/augments/2: 强化包含未公开字段 ['hexScore']",
            errors,
        )

        invalid_combos = json.loads(json.dumps(combos))
        invalid_combos[0]["tier"] = 1
        invalid_combos[0]["games"] = 0
        invalid_combos[0]["augments"][2] = invalid_combos[0]["augments"][0]
        invalid_combos[1] = json.loads(json.dumps(invalid_combos[0]))
        invalid_combos[1].pop("tier")
        invalid_combos[1]["games"] = 10
        errors = []
        validate_site_data.check_augment_sections(
            augments,
            invalid_combos,
            errors,
            "probe",
        )
        self.assertIn(
            "probe/combos/1: 不应公开由胜率派生的组合字段 tier",
            errors,
        )
        self.assertIn("probe/combos/1: 组合内强化 ID 不得重复", errors)
        self.assertIn("probe/combos/1: 样本场次必须是正整数", errors)
        self.assertIn("probe/combos: 存在重复的三强化组合", errors)

        errors = []
        validate_site_data.check_augment_sections([], combos, errors, "probe")
        self.assertIn("probe/augments: 强化推荐必须是非空数组", errors)

        build_source = (ROOT / "pipeline" / "build_site_data.py").read_text(
            encoding="utf-8"
        )
        hero_script = (ROOT / "site" / "assets" / "hero.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('"tier": t.get("winRateTier")', build_source)
        self.assertNotIn("c.tier", hero_script)
        self.assertIn(
            "样本 ${escapeHTML(c.games ?? '?')} 场",
            hero_script,
        )

    def test_public_recommendations_omit_internal_numeric_ranking_signals(self):
        augment_fields = {
            "id",
            "name",
            "rarity",
            "desc",
            "needsGameCheck",
            "icon",
            "hexLabel",
            "opgg",
        }
        item_rank_fields = {"item", "hexLabel"}
        checked_augments = 0
        checked_items = 0
        for path in sorted((ROOT / "site" / "data" / "heroes").glob("*.json")):
            detail = json.loads(path.read_text(encoding="utf-8"))
            for augment in detail["augments"]:
                self.assertLessEqual(set(augment), augment_fields, path.name)
                self.assertNotIn("hexScore", augment, path.name)
                checked_augments += 1
            for ranked in detail["items"]["hexTop"]:
                self.assertEqual(set(ranked), item_rank_fields, path.name)
                self.assertNotIn("hexScore", ranked, path.name)
                checked_items += 1

        self.assertEqual(checked_augments, 3369)
        self.assertEqual(checked_items, 1384)

    def test_public_hero_details_match_exact_frontend_field_contract(self):
        paths = sorted((ROOT / "site" / "data" / "heroes").glob("*.json"))
        self.assertEqual(len(paths), 173)
        for path in paths:
            detail = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(detail),
                validate_site_data.PUBLIC_HERO_DETAIL_FIELDS,
                path.name,
            )
            self.assertTrue({"id", "alias", "splash", "sources"}.isdisjoint(detail))

        detail = json.loads(paths[0].read_text(encoding="utf-8"))
        invalid = {**detail, "splash": "https://example.com/unused.jpg"}
        invalid.pop("name")
        errors = []
        validate_site_data.check_public_hero_detail_fields(
            invalid,
            errors,
            "probe",
        )
        self.assertEqual(
            errors,
            [
                "probe: 英雄详情缺少公开字段 ['name']",
                "probe: 英雄详情包含未公开字段 ['splash']",
            ],
        )

    def test_source_snapshot_contract_is_exact_valid_and_consistent(self):
        index = json.loads(
            (ROOT / "site" / "data" / "index.json").read_text(encoding="utf-8")
        )
        patch = index["patch"]
        self.assertEqual(set(patch), validate_site_data.PUBLIC_PATCH_FIELDS)
        errors = []
        validate_site_data.check_public_patch_fields(patch, errors, "valid")
        self.assertEqual(errors, [])

        for path in sorted((ROOT / "site" / "data" / "heroes").glob("*.json")):
            detail = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(detail["patch"], patch, path.name)
            self.assertIn(detail["patch"]["opggRunes"], detail["runes"]["source"])

        invalid = {**patch, "ddragon": "16.14.1", "builtAt": "2026-07-30"}
        invalid.pop("opggRunes")
        errors = []
        validate_site_data.check_public_patch_fields(invalid, errors, "probe")
        self.assertEqual(
            errors,
            [
                "probe: 来源快照缺少公开字段 ['opggRunes']",
                "probe: 来源快照包含未公开字段 ['builtAt']",
                "probe: OP.GG 符文版本无效 None",
                "probe: Data Dragon 与客户端资料版本不一致 "
                "'16.14.1' -> '26.14'",
            ],
        )

        errors = []
        validate_site_data.check_public_patch_fields(
            {**patch, "hexdataDate": "2026-02-30"},
            errors,
            "probe",
        )
        self.assertEqual(errors, ["probe: 统计日期无效 '2026-02-30'"])

        errors = []
        validate_site_data.check_public_patch_fields("26.15", errors, "probe")
        self.assertEqual(errors, ["probe: 来源快照必须是对象"])

    def test_public_index_matches_exact_frontend_field_contract(self):
        index = json.loads(
            (ROOT / "site" / "data" / "index.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(index), validate_site_data.PUBLIC_INDEX_FIELDS)
        self.assertEqual(len(index["heroes"]), 173)
        removed_fields = {
            "id",
            "pickRate",
            "games",
            "kda",
            "videoCount",
            "currentVideoCount",
            "latestVideoAt",
        }
        for hero in index["heroes"]:
            self.assertEqual(
                set(hero),
                validate_site_data.PUBLIC_INDEX_HERO_FIELDS,
                hero.get("alias"),
            )
            self.assertTrue(removed_fields.isdisjoint(hero), hero.get("alias"))
            errors = []
            validate_site_data.check_public_index_hero_fields(
                hero,
                errors,
                f"index/{hero['alias']}",
            )
            self.assertEqual(errors, [], hero["alias"])

        invalid = {
            **index["heroes"][0],
            "id": 1,
            "videoAvailability": [
                {
                    "expiresAt": "2026-02-30",
                    "patchStatus": "unknown",
                    "videoCount": 1,
                }
            ],
        }
        invalid.pop("search")
        errors = []
        validate_site_data.check_public_index_hero_fields(invalid, errors, "probe")
        self.assertEqual(
            errors,
            [
                "probe: 首页英雄缺少公开字段 ['search']",
                "probe: 首页英雄包含未公开字段 ['id']",
                "probe: 英雄搜索词不能为空",
                "probe/videoAvailability/1: 包含未公开字段 ['videoCount']",
                "probe/videoAvailability/1: 到期日期无效 '2026-02-30'",
                "probe/videoAvailability/1: 版本状态无效",
            ],
        )

        errors = []
        validate_site_data.check_public_index_fields(
            {"patch": index["patch"], "heroes": [], "builtAt": "2026-07-30"},
            errors,
        )
        self.assertEqual(errors, ["首页数据包含未公开字段 ['builtAt']"])

    def test_index_search_terms_are_unambiguous_and_normalized(self):
        index = json.loads(
            (ROOT / "site" / "data" / "index.json").read_text(encoding="utf-8")
        )
        errors = []
        validate_site_data.check_index_search_terms(index["heroes"], errors)
        self.assertEqual(errors, [])

        all_terms = [
            term
            for hero in index["heroes"]
            for term in hero["search"].split(",")
        ]
        self.assertEqual(len(all_terms), len(set(all_terms)))
        self.assertFalse(
            any(len(term) == 1 and term.isascii() for term in all_terms)
        )
        miss_fortune = next(
            hero for hero in index["heroes"]
            if hero["alias"] == "MissFortune"
        )
        self.assertTrue(
            {"mf", "女枪", "missfortune"}.issubset(
                set(miss_fortune["search"].split(","))
            )
        )

        rows = [
            {
                "alias": "One",
                "name": "英雄甲",
                "epithet": "甲称号",
                "search": "ｍｆ,英雄甲,甲称号,one,sn,a",
            },
            {
                "alias": "Two",
                "name": "英雄乙",
                "epithet": "乙称号",
                "search": "two,英雄乙,乙称号,sn",
            },
        ]
        summary = build_site_data.finalize_index_search_terms(rows)
        self.assertEqual(
            summary,
            {
                "ambiguousTerms": 1,
                "singleLetterTerms": 1,
                "removedOccurrences": 3,
            },
        )
        self.assertEqual(
            set(rows[0]["search"].split(",")),
            {"mf", "one", "甲称号", "英雄甲"},
        )
        self.assertNotIn("sn", rows[1]["search"].split(","))

        invalid = [
            {
                "alias": "One",
                "name": "英雄甲",
                "epithet": "甲称号",
                "search": "one,英雄甲,甲称号,sn,a",
            },
            {
                "alias": "Two",
                "name": "英雄乙",
                "epithet": "乙称号",
                "search": "ＴＷＯ,英雄乙,sn",
            },
        ]
        errors = []
        validate_site_data.check_index_search_terms(invalid, errors)
        self.assertIn(
            "index/One: 搜索词不能使用单字母英文缩写",
            errors,
        )
        self.assertIn(
            "index/Two: 搜索词必须使用 NFKC 小写规范",
            errors,
        )
        self.assertIn(
            "index/Two: 搜索词缺少必要身份 ['two', '乙称号']",
            errors,
        )
        self.assertIn(
            "首页搜索词跨英雄冲突 'sn': ['One', 'Two']",
            errors,
        )

        index_script = (ROOT / "site" / "assets" / "index.js").read_text(
            encoding="utf-8"
        )
        self.assertIn(".normalize('NFKC')", index_script)
        self.assertIn("query = normalizeSearchTerm(e.target.value)", index_script)
        self.assertIn("query && bestScore === 3 ? 3", index_script)

    def test_rune_sections_require_complete_distinct_renderable_pages(self):
        runes = json.loads(
            (ROOT / "site" / "data" / "heroes" / "Thresh.json").read_text(
                encoding="utf-8"
            )
        )["runes"]
        errors = []
        validate_site_data.check_rune_sections(runes, errors, "valid")
        self.assertEqual(errors, [])

        invalid = json.loads(json.dumps(runes))
        invalid["pages"][0]["primary"] = []
        errors = []
        validate_site_data.check_rune_sections(invalid, errors, "probe")
        self.assertIn(
            "probe/runes/1: 主系符文必须恰好有 4 项",
            errors,
        )

        invalid = json.loads(json.dumps(runes))
        invalid["pages"][0]["subStyle"] = invalid["pages"][0]["primaryStyle"]
        errors = []
        validate_site_data.check_rune_sections(invalid, errors, "probe")
        self.assertIn("probe/runes/1: 主系与副系不能相同", errors)

        invalid = json.loads(json.dumps(runes))
        invalid["pages"][0]["primary"][1] = invalid["pages"][0]["primary"][0]
        errors = []
        validate_site_data.check_rune_sections(invalid, errors, "probe")
        self.assertIn("probe/runes/1: 同一符文页存在重复符文", errors)

        invalid = json.loads(json.dumps(runes))
        invalid["pages"][0]["shards"] = ["技能急速", "成长生命值"]
        invalid["pages"][0]["pickRate"] = "0.5"
        errors = []
        validate_site_data.check_rune_sections(invalid, errors, "probe")
        self.assertIn(
            "probe/runes/1: 属性碎片必须恰好有 3 个非空名称",
            errors,
        )
        self.assertIn(
            "probe/runes/1: 符文采用率必须是 0–1 的有限数值",
            errors,
        )

        errors = []
        validate_site_data.check_rune_sections([], errors, "probe")
        self.assertEqual(errors, ["probe: 符文数据必须是对象"])

    def test_spell_sections_require_two_distinct_current_pairs(self):
        spells = json.loads(
            (ROOT / "site" / "data" / "heroes" / "Thresh.json").read_text(
                encoding="utf-8"
            )
        )["spells"]
        errors = []
        validate_site_data.check_spell_sections(spells, errors, "valid")
        self.assertEqual(errors, [])

        errors = []
        validate_site_data.check_spell_sections([], errors, "probe")
        self.assertEqual(
            errors,
            ["probe: 召唤师技能必须恰好有主流、备选 2 组"],
        )

        invalid = json.loads(json.dumps(spells))
        invalid[0] = invalid[0][:1]
        errors = []
        validate_site_data.check_spell_sections(invalid, errors, "probe")
        self.assertIn(
            "probe/spells/1: 每组召唤师技能必须恰好有 2 项",
            errors,
        )

        invalid = json.loads(json.dumps(spells))
        invalid[0][1] = invalid[0][0]
        errors = []
        validate_site_data.check_spell_sections(invalid, errors, "probe")
        self.assertIn(
            "probe/spells/1: 同组不能重复召唤师技能",
            errors,
        )

        invalid = json.loads(json.dumps(spells))
        invalid[1] = list(reversed(invalid[0]))
        errors = []
        validate_site_data.check_spell_sections(invalid, errors, "probe")
        self.assertIn(
            "probe: 主流与备选召唤师技能组合不能重复",
            errors,
        )

        invalid = json.loads(json.dumps(spells))
        invalid[0][0]["name"] = "惩戒"
        errors = []
        validate_site_data.check_spell_sections(invalid, errors, "probe")
        self.assertIn(
            "probe/spells/1/1: 召唤师技能不在 ARAM/KIWI 允许列表",
            errors,
        )

        invalid = json.loads(json.dumps(spells))
        invalid[0][0]["icon"] = "assets/img/spell/SummonerHeal.png"
        errors = []
        validate_site_data.check_spell_sections(invalid, errors, "probe")
        self.assertIn(
            "probe/spells/1/1: 召唤师技能名称与图标不匹配",
            errors,
        )

    def test_douyin_links_use_runtime_and_publication_allowlists(self):
        hero_script = (ROOT / "site" / "assets" / "hero.js").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'class="douyin-btn" href="${safeDouyinURL(searchUrl)}"',
            hero_script,
        )
        self.assertNotIn('class="douyin-btn" href="${searchUrl}"', hero_script)

        results = self.run_common_script(
            "["
            "safeDouyinURL('https://www.douyin.com/search/%E9%94%A4%E7%9F%B3'),"
            "safeDouyinURL('https://v.douyin.com/abc123/'),"
            "safeDouyinURL('javascript:alert(1)'),"
            "safeDouyinURL('https://evil.example/search/test'),"
            "safeDouyinURL('https://user@www.douyin.com/search/test')"
            "]"
        )
        self.assertEqual(
            results,
            [
                "https://www.douyin.com/search/%E9%94%A4%E7%9F%B3",
                "https://v.douyin.com/abc123/",
                "#",
                "#",
                "#",
            ],
        )

    def test_douyin_search_urls_are_semantically_validated(self):
        errors = []
        validate_site_data.check_douyin_search_url(
            "https://www.douyin.com/search/%E9%94%A4%E7%9F%B3",
            errors,
            "valid",
        )
        self.assertEqual(errors, [])

        for value in (
            "javascript:alert(1)",
            "https://evil.example/search/test",
            "https://www.douyin.com/video/123",
            "https://user@www.douyin.com/search/test",
            "https://www.douyin.com/search/test#fragment",
        ):
            errors = []
            validate_site_data.check_douyin_search_url(value, errors, "probe")
            self.assertEqual(errors, ["probe: 抖音搜索链接无效"], value)

    def test_source_versions_are_not_collapsed_into_ddragon_patch(self):
        self.assertEqual(build_site_data.public_patch("16.14.1"), "26.14")
        self.assertEqual(build_site_data.public_patch("16.15"), "26.15")
        with self.assertRaises(ValueError):
            build_site_data.public_patch("unknown")

    def test_mixed_opgg_cache_versions_are_rejected(self):
        expected = {"mayhem": "16.14.1", "aram": "16.14.1"}
        build_site_data.require_opgg_source_versions(
            {"sourceVersions": expected},
            expected,
            "Aatrox",
        )
        with self.assertRaisesRegex(ValueError, "缓存版本不一致"):
            build_site_data.require_opgg_source_versions(
                {"sourceVersions": {"mayhem": "16.13.1", "aram": "16.14.1"}},
                expected,
                "Aatrox",
            )


if __name__ == "__main__":
    unittest.main()
