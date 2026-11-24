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

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import canary_lib as lib  # noqa: E402
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

    def test_schema_version_optional_and_string(self):
        for value in (None, "2.0", 2):
            cfg = dict(self.base)
            if value is not None:
                cfg["schemaVersion"] = value
            self.assertEqual(list(self.validator.iter_errors(cfg)), [],
                             "schemaVersion=%r should pass" % (value,))
        cfg = dict(self.base, schemaVersion="v2")
        self.assertTrue(list(self.validator.iter_errors(cfg)))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
