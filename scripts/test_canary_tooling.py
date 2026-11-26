#!/usr/bin/env python3
"""Stdlib self-tests for the CI/canary tooling (Refs #62, #64).

Run directly (no pytest needed)::

    python3 scripts/test_canary_tooling.py -v

Covers the parts that are easy to break while editing the tooling:
  * CSS-selector subset used for item counting / first-item extraction
  * JSON Schema positive + negative verdicts (builtin engine)
  * SUPPORT-CONFIG.md row parsing (notes may contain `|`)
  * matrix regression ranking
  * route URL building for HTML and REST configs
"""

import gzip
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import canary_lib as lib  # noqa: E402
import canary_probe  # noqa: E402
import canary_smoke_runner  # noqa: E402
import generate_support_table  # noqa: E402
import matrix_diff  # noqa: E402
import validate_schema  # noqa: E402

HTML = """
<html><body>
<div class="listupd">
  <div class="bs"><div class="bsx">
    <a href="/manga/one-piece/"><img src="/c/1.jpg"></a>
    <div class="tt">One Piece</div>
  </div></div>
  <div class="bs"><div class="bsx">
    <a href="/manga/two/"><img src="/c/2.jpg"></a>
    <div class="tt">  Two   Piece </div>
  </div></div>
</div>
<ul id="chapterlist"><li><a href="/manga/one-piece/ch-1/">Chapter 1</a></li></ul>
</body></html>
"""


class SelectorTest(unittest.TestCase):
    def test_count_and_extract(self):
        self.assertEqual(lib.count_items(HTML, ".listupd .bs .bsx"), 2)
        block = {
            "container": ".listupd .bs .bsx",
            "fields": {
                "id": {"selector": "a", "attribute": "href",
                       "transform": "slug"},
                "title": {"selector": ".tt", "transform": "cleanText"},
            },
        }
        item = lib.extract_first_item(HTML, block)
        self.assertEqual(item["id"], "one-piece")
        self.assertEqual(item["title"], "One Piece")

    def test_child_and_attribute_selectors(self):
        root = lib.parse_html(HTML)
        self.assertEqual(len(lib.select(root, "#chapterlist > li")), 1)
        self.assertEqual(len(lib.select(root, '.tt[class="tt"]')), 2)
        self.assertEqual(len(lib.select(root, "article, .bsx")), 2)

    def test_nth_and_not(self):
        root = lib.parse_html(HTML)
        self.assertEqual(len(lib.select(root, ".bs:nth-child(2)")), 1)
        self.assertEqual(len(lib.select(root, ".bsx:not(.missing)")), 2)
        three = lib.parse_html("<ul><li>a</li><li>b</li><li>c</li><li>d</li></ul>")
        self.assertEqual([n.text() for n in lib.select(three, "li:first-child")], ["a"])
        self.assertEqual([n.text() for n in lib.select(three, "li:last-child")], ["d"])
        self.assertEqual([n.text() for n in lib.select(three, "li:nth-child(2)")], ["b"])
        self.assertEqual([n.text() for n in lib.select(three, "li:nth-child(odd)")], ["a", "c"])
        self.assertEqual([n.text() for n in lib.select(three, "li:nth-child(even)")], ["b", "d"])

    def test_deep_dom_no_recursion_error(self):
        deep = "<div>" * 400 + "<p>leaf</p>" + "</div>" * 400
        root = lib.parse_html(deep)
        self.assertEqual(len(lib.select(root, "p")), 1)
        self.assertEqual(root.text().strip(), "leaf")


class SchemaTest(unittest.TestCase):
    def setUp(self):
        with open(validate_schema.SCHEMA_PATH, encoding="utf-8") as fh:
            self.schema = json.load(fh)
        self.validator = validate_schema.LocalValidator(self.schema)
        self.base = {
            "source": "demo",
            "version": "1.0.0",
            "baseUrl": "https://demo.test",
            "enabled": True,
            "defaultLanguage": "english",
            "idType": "slug",
            "ui": {"displayName": "Demo"},
            "features": {"search": True, "chapters": True},
            "scraper": {"enabled": True,
                        "urlPatterns": {"home": "/"}},
        }

    def test_minimal_config_valid(self):
        self.assertEqual(list(self.validator.iter_errors(self.base)), [])

    def test_schema_version_optional_v2_v3(self):
        for value in (None, 2, "2.0", "2.1", 3, "3.0", "3.2"):
            cfg = dict(self.base)
            if value is not None:
                cfg["schemaVersion"] = value
            self.assertEqual(list(self.validator.iter_errors(cfg)), [],
                             "schemaVersion=%r should pass" % (value,))
        for value in ("v2", "1.0", 1, 4):
            cfg = dict(self.base, schemaVersion=value)
            self.assertTrue(list(self.validator.iter_errors(cfg)),
                            "schemaVersion=%r should fail" % (value,))

    def test_ui_icon_fields(self):
        for icon in ("ic_launcher", {"uri": "images/demo.png"}):
            cfg = json.loads(json.dumps(self.base))
            cfg["ui"].update({"iconPath": "images/demo.png",
                              "iconUrl": "https://demo.test/icon.png",
                              "icon": icon})
            self.assertEqual(list(self.validator.iter_errors(cfg)), [],
                             "icon=%r should pass" % (icon,))

    def test_typo_and_retired_keys_rejected(self):
        cfg = json.loads(json.dumps(self.base))
        cfg["features"]["seach"] = True
        self.assertTrue(list(self.validator.iter_errors(cfg)))
        cfg = json.loads(json.dumps(self.base))
        cfg["scraper"]["urlPatterns"]["home"] = {
            "url": "/", "list": {"secondaryFetch": {"listKey": "items"}}}
        self.assertTrue(list(self.validator.iter_errors(cfg)))
        cfg = json.loads(json.dumps(self.base))
        cfg["version"] = "1.0.0-beta"
        self.assertTrue(list(self.validator.iter_errors(cfg)))

    def test_real_configs_validate(self):
        rows = lib.load_configs()
        self.assertEqual(len(rows), 120)
        for _bucket, rel, cfg in rows:
            errors = list(self.validator.iter_errors(cfg))
            self.assertEqual(errors, [], "%s: %s" % (rel, errors[:2]))

    def test_cyclic_ref_bounded(self):
        cyclic = {"$defs": {"loop": {"$ref": "#/$defs/loop"}},
                  "$ref": "#/$defs/loop"}
        errors = list(validate_schema.LocalValidator(cyclic).iter_errors({}))
        self.assertTrue(errors)
        self.assertIn("depth exceeded", errors[0])


class MatrixTest(unittest.TestCase):
    def test_row_with_pipe_in_note(self):
        line = ("| 7 | `config/en/demo-config.json`<br>https://demo.test "
                "| ✅ `/` | ⚠️ `/p/{page}` | ✖️ | ✖️ | ✅ `/d/{id}` "
                "| ✅ image | ✖️ | ✖️ | ✖️ | ✖️ "
                "| live: /category/a|b/ works, next `a.more` |")
        row = matrix_diff.split_row(line)
        self.assertEqual(row["config"], "config/en/demo-config.json")
        self.assertEqual(len(row["statuses"]), 10)
        self.assertIn("a|b", row["note"])
        self.assertEqual(matrix_diff.status_rank(row["statuses"][0]), 3)
        self.assertEqual(matrix_diff.status_rank(row["statuses"][1]), 2)
        self.assertEqual(matrix_diff.status_rank(row["statuses"][2]), 1)
        self.assertEqual(matrix_diff.status_rank("plain"), None)

    def test_parse_matrix_marker_and_rows(self):
        text = "\n".join([
            "# matrix",
            "<!-- matrix-columns: home|home_pag|search|search_pag|detail"
            "|reader|genre|tag|author|artist -->",
            "",
            "| No | Config / Path | Home | Home + Pag + Total | Search "
            "| Search + Pag + Total | Detail + Chapters | Reader "
            "| Genre + Pag | Tag + Pag | Author + Pag | Artist + Pag | Note |",
            "|---:|---|---|---|---|---|---|---|---|---|---|---|---|",
            "| 1 | `config/id/demo-config.json`<br>https://demo.test "
            "| ✅ `/` | ✅ `/p/{page}` | ✅ `/s?q=` | ✅ `/s?q=&p=` "
            "| ✅ `/d/{id}` | ✅ image | ✖️ | ✖️ | ✖️ | ✖️ | note |",
        ])
        parsed = matrix_diff.parse_matrix(text)
        self.assertEqual(len(parsed["columns"]), 10)
        self.assertIn("config/id/demo-config.json", parsed["rows"])

    def test_regression_ranking(self):
        pairs = [("✅", "⚠️"), ("✅", "✖️"), ("✅", "🚫"), ("⚠️", "✖️")]
        for old, new in pairs:
            self.assertLess(matrix_diff.status_rank(new),
                            matrix_diff.status_rank(old))
        self.assertEqual(
            matrix_diff.status_rank("⚠️ `/page/{page}` (next-only)"), 2)

    def test_support_table_check_sync_allows_absent_gitignored_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = os.path.join(tmp, "AGENTS.md")
            skill = os.path.join(tmp, "SKILL.md")
            with open(agents, "w", encoding="utf-8") as fh:
                fh.write("# rules\n%s\n" % generate_support_table.MARKER)
            # Clean CI checkout: gitignored SKILL.md is absent, AGENTS.md valid.
            with mock.patch.object(generate_support_table, "AGENTS", agents), \
                    mock.patch.object(generate_support_table, "SKILL", skill):
                self.assertEqual(generate_support_table.check_sync(), [])
                # Present SKILL.md with missing/stale marker must still fail.
                with open(skill, "w", encoding="utf-8") as fh:
                    fh.write("# skill without marker\n")
                problems = generate_support_table.check_sync()
                self.assertEqual(len(problems), 1)
                self.assertIn("SKILL.md", problems[0])
                # Present SKILL.md with valid marker passes.
                with open(skill, "w", encoding="utf-8") as fh:
                    fh.write("# skill\n%s\n" % generate_support_table.MARKER)
                self.assertEqual(generate_support_table.check_sync(), [])
            # Missing tracked AGENTS.md must always fail.
            missing_agents = os.path.join(tmp, "MISSING_AGENTS.md")
            with mock.patch.object(generate_support_table, "AGENTS", missing_agents), \
                    mock.patch.object(generate_support_table, "SKILL", skill):
                problems = generate_support_table.check_sync()
                self.assertEqual(len(problems), 1)
                self.assertIn("MISSING_AGENTS.md", problems[0])


class TargetTest(unittest.TestCase):
    def test_html_home_and_search(self):
        cfg = {
            "source": "demo", "baseUrl": "https://demo.test",
            "scraper": {"urlPatterns": {
                "home": {"url": "/manga/"},
                "search": {"url": "/?s={query}"},
                "detail": "/manga/{id}/",
            }},
        }
        self.assertEqual(lib.home_target(cfg), ("html", "https://demo.test/manga/"))
        self.assertEqual(lib.search_target(cfg, "love"),
                         ("html", "https://demo.test/?s=love"))
        self.assertEqual(lib.detail_target(cfg, "abc"),
                         ("html", "https://demo.test/manga/abc/"))
        self.assertEqual(lib.resolve_list_block(cfg, "home"), None)

    def test_rest_home_substitutes_page(self):
        cfg = {
            "source": "demo", "baseUrl": "https://demo.test",
            "api": {"url": "https://demo.test/api",
                    "endpoints": {"latest": "/catalog?page={page}"}},
            "features": {},
        }
        self.assertEqual(lib.home_target(cfg),
                         ("api", "https://demo.test/api/catalog?page=1"))


class HardeningTest(unittest.TestCase):
    def test_http_get_rejects_non_http_scheme(self):
        fetched = lib.http_get("file:///etc/hosts", timeout=1, attempts=1)
        self.assertIsNone(fetched.status)
        self.assertIn("disallowed URL scheme", fetched.error or "")

    def test_decompress_bounded_caps_gzip_bomb(self):
        compressed = gzip.compress(b"A" * 100000)
        out = lib._decompress_bounded(compressed, "gzip", max_bytes=1024)
        self.assertEqual(len(out), 1024)

    def test_default_baseline_is_relative(self):
        self.assertFalse(os.path.isabs(canary_probe.DEFAULT_BASELINE))

    def test_smoke_search_does_not_retry_on_http_403(self):
        class FakeFetcher:
            from_dir = None
            calls = 0

            def get(self, source, route, url, headers, kind="html"):
                self.calls += 1
                return lib.Fetch(status=403, url=url, error="HTTP 403")

        cfg = {
            "source": "demo", "baseUrl": "https://demo.test",
            "features": {"search": True},
            "scraper": {"enabled": True,
                        "urlPatterns": {"search": {"url": "/?s={query}"}},
                        "selectors": {"list": {"container": ".item"}}},
        }
        fetcher = FakeFetcher()
        entry = canary_smoke_runner.run_source(
            "en", "config/en/demo-config.json", cfg, fetcher,
            ["search"], "love", dry_run=False)
        self.assertEqual(entry["routes"]["search"]["status"], "fail")
        self.assertEqual(fetcher.calls, 1)


class ProbeClassificationTest(unittest.TestCase):
    fetcher_cfg = {"timeout": 1, "dry_run": False}

    def test_sample_queries_override_and_hash_fallback(self):
        self.assertEqual(canary_probe.sample_queries("xiutaku"),
                         ["love", "girl"])
        picked = canary_probe.sample_queries("cocomicco")
        self.assertEqual(len(picked), 1)
        self.assertIn(picked[0], lib.search_queries())

    def test_probe_route_without_search_route_is_skipped(self):
        cfg = {"source": "beauty3600000", "baseUrl": "https://3600000.xyz",
               "scraper": {"urlPatterns": {"home": {"url": "/"}}}}
        with mock.patch.object(lib, "http_get") as get:
            record = canary_probe.probe_route(
                cfg, "search", "love", self.fetcher_cfg)
        self.assertTrue(record["skipped"])
        self.assertEqual(record["error"], "no search route in config")
        self.assertIsNone(record["url"])
        get.assert_not_called()

    def test_cloudflare_challenge_is_info_not_critical(self):
        body = ("<title>Just a moment...</title>"
                "<script src='https://challenges.cloudflare.com/x'></script>")
        fetched = lib.Fetch(status=403, body=body.encode(), error="HTTP 403")
        self.assertIn("Cloudflare", canary_probe.classify_block(fetched) or "")
        self.assertIsNone(canary_probe.classify_block(
            lib.Fetch(status=403, body=b"forbidden")))
        cfg = {"source": "ravenscansorg", "baseUrl": "https://ravenscans.org",
               "scraper": {"urlPatterns": {"home": {"url": "/"}}}}
        with mock.patch.object(lib, "http_get", return_value=fetched):
            record = canary_probe.probe_route(
                cfg, "home", "love", self.fetcher_cfg)
        self.assertTrue(record["blocked"])
        entry = {"source": "ravenscansorg", "routes": {
            "home": record, "search": {"route": "search", "skipped": True}}}
        baseline = {"sources": {"ravenscansorg": {"homeStatus": 200}}}
        drifts = canary_probe.detect_drift(entry, baseline, 0.5)
        self.assertEqual([d["severity"] for d in drifts], ["info"])

    def test_search_falls_back_to_second_known_query(self):
        cfg = {"source": "xiutaku", "baseUrl": "https://xiutaku.com",
               "scraper": {"urlPatterns": {
                   "home": {"url": "/"},
                   "search": {"url": "/?search={query}"}}}}
        calls = []

        def fake_probe(_cfg, route, query, _fetcher_cfg, rate_limit=None):
            calls.append((route, query))
            if route == "home":
                return {"route": route, "status_code": 200, "item_count": 20}
            return {"route": route, "status_code": 200,
                    "item_count": 12 if query == "girl" else 0}

        with mock.patch.object(lib, "load_configs", return_value=[
                ("global", "config/global/xiutaku-config.json", cfg)]), \
                mock.patch.object(canary_probe, "probe_route",
                                  side_effect=fake_probe), \
                mock.patch("sys.stdout"):
            self.assertEqual(canary_probe.main(
                ["--source", "xiutaku", "--timeout", "1", "--delay", "0"]), 0)
        self.assertEqual([q for route, q in calls if route == "search"],
                         ["love", "girl"])

    def test_secondary_fetch_shell_is_skipped(self):
        cfg = {"source": "nicomanga", "baseUrl": "https://nicomanga.com",
               "scraper": {"urlPatterns": {"home": {
                   "url": "/manga-list.html",
                   "list": {"container": ".manga-grid .manga-card",
                            "secondaryFetch": {"enabled": True}}}}}}
        fetched = lib.Fetch(status=200, body=b"<html><body></body></html>")
        with mock.patch.object(lib, "http_get", return_value=fetched):
            record = canary_probe.probe_route(
                cfg, "home", "love", self.fetcher_cfg)
        self.assertTrue(record["skipped"])
        self.assertIn("secondaryFetch", record["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
