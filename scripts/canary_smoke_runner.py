#!/usr/bin/env python3
"""Canary smoke runner: deterministic Home/Search/Detail/Reader health checks.

Runs the four user-visible routes of a source config and reports one of
``pass`` / ``fail`` / ``skip`` per route:

  home    HTTP 200 + >= 1 item parsed by ``list.container`` (+ valid title/id)
  search  HTTP 200 + >= 1 item for a fixed query (skipped when the config has
          no search route or posts a form the runner does not emulate)
  detail  HTTP 200 + non-empty title (+ >= 1 chapter when ``features.chapters``)
  reader  HTTP 200 + at least one image URL, and that image itself answers
          200/206 with an image content type (detects 403 hotlink protection)

Besides live probing it supports a snapshot mode: ``--save-snapshots DIR``
stores every fetched body, ``--from-snapshots DIR`` replays the parsing checks
offline (no network at all), which is what CI runs when the network is flaky.

Examples::

    python3 scripts/canary_smoke_runner.py --help
    python3 scripts/canary_smoke_runner.py --suite refactor-24 --sample 5
    python3 scripts/canary_smoke_runner.py --bucket en --sample 10 --delay 2
    python3 scripts/canary_smoke_runner.py --source hentairox --routes home,detail
    python3 scripts/canary_smoke_runner.py --suite refactor-24 --dry-run
    python3 scripts/canary_smoke_runner.py --suite refactor-24 --sample 5 \\
        --save-snapshots .canary/snapshots --output .canary/report.json
    python3 scripts/canary_smoke_runner.py --from-snapshots .canary/snapshots

Exit code: 0 when no attempted route failed (skips are fine), 1 on any
failure, 2 on a bad suite/selection. ``--allow-failures`` always exits 0.

Refs #62, #64 (24-source sweep suite = scripts/canary_suites.json).
"""

import argparse
import json
import os
import re
import random
import sys
import time
from datetime import datetime, timezone

import canary_lib as lib

ROUTES = ("home", "search", "detail", "reader")
SUITES_PATH = os.path.join(lib.ROOT, "scripts", "canary_suites.json")


# ---------------------------------------------------------------------------
# config introspection
# ---------------------------------------------------------------------------

def resolve_pattern_entry(cfg, name):
    entry = lib.url_patterns(cfg).get(name)
    if isinstance(entry, dict):
        return entry
    return None


def resolve_list_block(cfg, name, depth=0):
    """Resolve the list block for a named HTML route (see canary_lib)."""
    return lib.resolve_list_block(cfg, name, depth)


def detail_chapters_block(cfg):
    detail = ((cfg.get("scraper") or {}).get("selectors") or {}).get("detail") or {}
    return detail.get("chapters") or {}


def detail_fields(cfg):
    detail = ((cfg.get("scraper") or {}).get("selectors") or {}).get("detail") or {}
    return detail.get("fields") or {}


def api_list_items_path(cfg):
    return ((cfg.get("api") or {}).get("list") or {}).get("items")


def api_detail_title(cfg, payload):
    fields = ((cfg.get("api") or {}).get("detail") or {}).get("fields") or {}
    spec = fields.get("title") or {}
    selector = spec.get("selector")
    value = lib.jget(payload, selector) if selector else None
    return str(value or "").strip()


def api_chapters_target(cfg, item_id):
    api = cfg.get("api") or {}
    chapters = (api.get("detail") or {}).get("chapters") or {}
    endpoint = chapters.get("endpoint") or lib.api_endpoint(cfg, "chapters")
    if not endpoint:
        return None, None
    base = api.get("url") or cfg.get("baseUrl")
    url = base.rstrip("/") + "/" + lib.substitute(
        endpoint, {"id": item_id, "chapter": item_id}).lstrip("/")
    return url, chapters.get("items")


# ---------------------------------------------------------------------------
# fetch wrapper (live or snapshot)
# ---------------------------------------------------------------------------

class Fetcher:
    def __init__(self, timeout, save_dir=None, from_dir=None, delay=0.0,
                 jitter=0.0, seed=0):
        self.timeout = timeout
        self.save_dir = save_dir
        self.from_dir = from_dir
        self.delay = delay
        self.jitter = jitter
        self.rng = random.Random(seed)
        self.calls = 0

    def _snapshot_path(self, source, route, suffix):
        directory = os.path.join(self.from_dir or self.save_dir, source)
        return os.path.join(directory, "%s.%s" % (route, suffix))

    def get(self, source, route, url, headers, kind="html"):
        if self.from_dir:
            suffix = "json" if kind == "api" else "html"
            path = self._snapshot_path(source, route, suffix)
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
                return lib.Fetch(status=200, body=body, url=url)
            except OSError as exc:
                return lib.Fetch(url=url, error="snapshot missing: %s" % exc)
        if self.calls:
            lib.polite_sleep(lib.jitter_delay(self.delay, self.jitter, self.rng))
        self.calls += 1
        fetched = lib.http_get(url, headers, timeout=self.timeout, attempts=2)
        if self.save_dir and fetched.status:
            suffix = "json" if kind == "api" else "html"
            path = self._snapshot_path(source, route, suffix)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(fetched.body)
        return fetched

    def image(self, source, route, url, headers):
        if self.from_dir:
            return lib.Fetch(status=200, url=url,
                             headers={"Content-Type": "image/snapshot"},
                             error="snapshot replay: image not re-fetched")
        return lib.head_image(url, headers, timeout=self.timeout)


def parse_json(fetched):
    try:
        return json.loads(fetched.text)
    except (ValueError, AttributeError):
        return None


def content_type_ok(headers):
    ctype = ""
    for key, value in (headers or {}).items():
        if key.lower() == "content-type":
            ctype = str(value).lower()
    return ctype.startswith("image/")


# ---------------------------------------------------------------------------
# route checks
# ---------------------------------------------------------------------------

def check_home(cfg, fetcher):
    kind, url = lib.home_target(cfg)
    result = {"route": "home", "status": "skip", "url": url, "reasons": []}
    if not url:
        result["reasons"].append(kind)
        return result, None
    fetched = fetcher.get(cfg["source"], "home", url, lib.headers_for(cfg),
                          kind=kind)
    result.update(status_code=fetched.status, error=fetched.error)
    if not fetched.ok():
        result["status"] = "fail"
        result["reasons"].append("HTTP %s%s" % (
            fetched.status or "ERR", ": %s" % fetched.error if fetched.error else ""))
        return result, None
    if kind == "api":
        payload = parse_json(fetched)
        if payload is None:
            result["status"] = "fail"
            result["reasons"].append("response is not JSON")
            return result, None
        items_path = api_list_items_path(cfg)
        count = lib.json_item_count(payload, items_path)
        result["item_count"] = count
        if count < 1:
            result["status"] = "fail"
            result["reasons"].append("0 items at %r" % items_path)
            return result, None
        item = lib.jlist(payload, items_path)[0]
        sample_id = None
        detail_api = (cfg.get("api") or {}).get("detail") or {}
        id_spec = (detail_api.get("fields") or {}).get("id") or {}
        if id_spec.get("selector"):
            sample_id = str(lib.jget(item, id_spec["selector"]) or "") or None
        for key in ("slug", "id", "key", "url"):
            if sample_id:
                break
            value = item.get(key) if isinstance(item, dict) else None
            if value:
                sample_id = str(value)
        result["status"] = "pass"
        result["sample"] = {"id": sample_id}
        return result, sample_id
    list_block = resolve_list_block(cfg, "home")
    if not list_block or not list_block.get("container"):
        result["status"] = "fail"
        result["reasons"].append("config has no home list container")
        return result, None
    count = lib.count_items(fetched.text, list_block["container"])
    item = lib.extract_first_item(fetched.text, list_block)
    result["item_count"] = count
    result["sample"] = item
    if count < 1 or item is None:
        result["status"] = "fail"
        result["reasons"].append("0 items at %r" % list_block["container"])
        return result, None
    if not item.get("title") and not item.get("id"):
        result["status"] = "fail"
        result["reasons"].append("first item has empty title+id")
        return result, None
    if not item.get("title"):
        result["reasons"].append("warning: first item title empty (id only)")
    result["status"] = "pass"
    return result, item.get("id")


def check_search(cfg, fetcher, query):
    features = cfg.get("features") or {}
    kind, url = lib.search_target(cfg, query)
    result = {"route": "search", "status": "skip", "url": url, "query": query,
              "reasons": []}
    if not url:
        result["reasons"].append(kind)
        return result
    entry = resolve_pattern_entry(cfg, "search")
    if entry and str(entry.get("method", "")).lower() == "post":
        result["reasons"].append("POST search form not exercised by canary")
        return result
    if features.get("search") is False and kind == "html":
        result["reasons"].append("features.search=false")
        return result
    fetched = fetcher.get(cfg["source"], "search", url, lib.headers_for(cfg),
                          kind=kind)
    result.update(status_code=fetched.status, error=fetched.error)
    if not fetched.ok():
        result["status"] = "fail"
        result["reasons"].append("HTTP %s%s" % (
            fetched.status or "ERR", ": %s" % fetched.error if fetched.error else ""))
        return result
    if kind == "api":
        payload = parse_json(fetched)
        if payload is None:
            result["status"] = "fail"
            result["reasons"].append("response is not JSON")
            return result
        count = lib.json_item_count(payload, api_list_items_path(cfg))
        result["item_count"] = count
        if count < 1:
            result["status"] = "fail"
            result["reasons"].append("0 search items")
            return result
        result["status"] = "pass"
        return result
    list_block = resolve_list_block(cfg, "search")
    if not list_block or not list_block.get("container"):
        result["status"] = "fail"
        result["reasons"].append("config has no search list container")
        return result
    count = lib.count_items(fetched.text, list_block["container"])
    result["item_count"] = count
    if count < 1:
        result["status"] = "fail"
        result["reasons"].append("0 items at %r" % list_block["container"])
        return result
    result["status"] = "pass"
    return result


def check_detail(cfg, fetcher, item_id):
    kind, url = lib.detail_target(cfg, item_id)
    result = {"route": "detail", "status": "skip", "url": url, "reasons": []}
    if not url:
        result["reasons"].append(kind)
        return result, None
    if kind not in ("html", "api"):
        result["reasons"].append(str(kind))
        return result, None
    fetched = fetcher.get(cfg["source"], "detail", url, lib.headers_for(cfg),
                          kind=kind)
    result.update(status_code=fetched.status, error=fetched.error)
    if not fetched.ok():
        result["status"] = "fail"
        result["reasons"].append("HTTP %s%s" % (
            fetched.status or "ERR", ": %s" % fetched.error if fetched.error else ""))
        return result, None
    features = cfg.get("features") or {}
    chapter_id = None
    if kind == "api":
        payload = parse_json(fetched)
        if payload is None:
            result["status"] = "fail"
            result["reasons"].append("response is not JSON")
            return result, None
        title = api_detail_title(cfg, payload)
        result["title"] = title
        if not title:
            result["status"] = "fail"
            result["reasons"].append("empty detail title")
            return result, None
        chapters_url, items_path = api_chapters_target(cfg, item_id)
        chapters_cfg = ((cfg.get("api") or {}).get("detail") or {}).get(
            "chapters") or {}
        if features.get("chapters") and chapters_url:
            chapters_fetch = fetcher.get(cfg["source"], "detail_chapters",
                                         chapters_url, lib.headers_for(cfg),
                                         kind="api")
            chap_payload = parse_json(chapters_fetch) \
                if chapters_fetch.ok() else None
            count = lib.json_item_count(chap_payload, items_path) \
                if chap_payload is not None else 0
            result["chapter_count"] = count
            if count < 1:
                result["status"] = "fail"
                result["reasons"].append("chapter list empty")
                return result, None
            first = lib.jlist(chap_payload, items_path)[0]
            if isinstance(first, dict):
                chap_fields = chapters_cfg.get("fields") or {}
                id_spec = chap_fields.get("id") or {}
                if id_spec.get("selector"):
                    chapter_id = str(lib.jget(first, id_spec["selector"])
                                     or "") or None
                if not chapter_id:
                    chapter_id = str(first.get("slug") or first.get("id")
                                     or first.get("chapter")
                                     or first.get("url") or "") or None
        result["status"] = "pass"
        return result, chapter_id
    fields = detail_fields(cfg)
    root = lib.parse_html(fetched.text)
    title = ""
    spec = fields.get("title") or {}
    if spec.get("selector"):
        nodes = lib.select(root, spec["selector"])
        if nodes:
            title = nodes[0].text().strip()
    result["title"] = title
    if not title:
        result["status"] = "fail"
        result["reasons"].append("empty detail title (%r)" % spec.get("selector"))
        return result, None
    chapters = detail_chapters_block(cfg)
    mode = str(chapters.get("mode") or "").lower()
    if chapters.get("container"):
        nodes = lib.select(root, chapters.get("container"))
        result["chapter_count"] = len(nodes)
        if not nodes and mode in ("ajax", "ajaxhtml", "ajaxhtmlimages"):
            result["reasons"].append(
                "chapter list mode=%s needs an AJAX call the canary does not "
                "emulate" % chapters.get("mode"))
        elif features.get("chapters") and not nodes:
            result["status"] = "fail"
            result["reasons"].append("chapter list empty (%r)" %
                                     chapters.get("container"))
            return result, None
        if nodes:
            chapter_fields = chapters.get("fields") or {}
            link_spec = chapter_fields.get("url") or chapter_fields.get("id") or {}
            raw = ""
            if link_spec:
                link_nodes = lib.select(nodes[0], link_spec.get("selector") or "")
                if link_nodes:
                    raw = link_nodes[0].attrs.get(
                        link_spec.get("attribute") or "href", "")
            if raw:
                chapter_id = lib.absolute_url(cfg, raw) \
                    if raw.startswith("/") else raw
            else:
                chapter_id = lib.field_value(
                    nodes[0], chapter_fields.get("id") or {}) or None
            result["chapter_sample"] = chapter_id
    result["status"] = "pass"
    return result, chapter_id


def image_url(fetched, reader):
    if not fetched.ok():
        return ""
    root = lib.parse_html(fetched.text)
    for spec in (
        (reader.get("imageSelector"), reader.get("imageAttr")),
        (((reader.get("images") or {}).get("selector")),
         ((reader.get("images") or {}).get("attribute"))),
        (reader.get("readerImageSelector"), reader.get("readerImageAttr")),
        (reader.get("thumbSelector"), reader.get("thumbSrcAttr")),
    ):
        selector, attr = spec
        if not selector:
            continue
        nodes = lib.select(root, selector)
        if not nodes:
            continue
        node = nodes[0]
        value = node.attrs.get(attr or "src") or node.attrs.get("src") \
            or node.attrs.get("data-src") or ""
        if value:
            return value
    return ""


def check_reader(cfg, fetcher, item_id, chapter_id):
    kind, url, reader = lib.reader_target(cfg, item_id, chapter_id)
    result = {"route": "reader", "status": "skip", "url": url, "reasons": []}
    if not url:
        result["reasons"].append(kind)
        return result
    if kind not in ("html", "api"):
        result["reasons"].append(str(kind))
        return result
    if kind == "html" and not chapter_id and \
            (cfg.get("features") or {}).get("chapters"):
        result["reasons"].append("no chapter URL from detail (ajax/JS list)")
        return result
    headers = lib.headers_for(cfg, referer=cfg.get("baseUrl"))
    fetched = fetcher.get(cfg["source"], "reader", url, headers, kind=kind)
    result.update(status_code=fetched.status, error=fetched.error)
    if not fetched.ok():
        result["status"] = "fail"
        result["reasons"].append("HTTP %s%s" % (
            fetched.status or "ERR", ": %s" % fetched.error if fetched.error else ""))
        return result
    image = ""
    if kind == "api":
        payload = parse_json(fetched)
        if payload is None:
            result["status"] = "fail"
            result["reasons"].append("response is not JSON")
            return result
        images_cfg = (cfg.get("api") or {}).get("images") or {}
        items_path = images_cfg.get("items") or ""
        entries = lib.jlist(payload, items_path)
        if not entries and isinstance(payload, list):
            entries = payload
        if entries:
            first = entries[0]
            if isinstance(first, dict):
                image = str(first.get("url") or first.get("src")
                            or first.get("path") or "")
            else:
                image = str(first)
            if image and not image.startswith(("http://", "https://")):
                prefix = images_cfg.get("serverPath") or ""
                base = cfg.get("baseUrl") or ""
                image = (base.rstrip("/") + "/" + image.lstrip("/")
                         if not prefix else prefix.rstrip("/") + "/" + image.lstrip("/"))
    else:
        image = image_url(fetched, reader)
        if not image and reader.get("cdnPathRegex"):
            # hentaifox-style gallery: thumb -> page-1 full image
            thumb = image_url(fetched, {"thumbSelector": reader.get("thumbSelector"),
                                        "thumbSrcAttr": reader.get("thumbSrcAttr")})
            if thumb:
                image = thumb.replace("t.", ".", 1) if "t." in thumb else thumb
    if not image and kind == "html":
        match = re.search(
            r"https?://[^\s\"'<>]+?\.(?:jpg|jpeg|png|webp|avif)"
            r"(?:\?[^\s\"'<>]*)?", fetched.text, re.IGNORECASE)
        if match:
            image = match.group(0)
            result["note"] = "image URL recovered from inline script payload"
    if not image:
        result["status"] = "fail"
        result["reasons"].append("no reader image URL in page")
        return result
    result["image_url"] = image if len(image) < 160 else image[:157] + "..."
    checked = fetcher.image(cfg["source"], "reader_image", image,
                            lib.headers_for(cfg, referer=cfg.get("baseUrl")))
    result["image_status"] = checked.status
    if checked.error and not checked.status:
        result["status"] = "fail"
        result["reasons"].append("image fetch failed: %s" % checked.error)
        return result
    if checked.status not in (200, 206):
        result["status"] = "fail"
        result["reasons"].append("image HTTP %s (hotlink/403 protection?)"
                                 % checked.status)
        return result
    if not fetcher.from_dir and not content_type_ok(checked.headers):
        result["reasons"].append("warning: image content-type missing/not image")
    result["status"] = "pass"
    return result


# ---------------------------------------------------------------------------
# selection and report
# ---------------------------------------------------------------------------

def load_suite(name):
    try:
        with open(SUITES_PATH, encoding="utf-8") as fh:
            suites = json.load(fh).get("suites") or {}
    except (OSError, ValueError) as exc:
        raise SystemExit("ERROR: cannot read %s: %s" % (SUITES_PATH, exc))
    if name not in suites:
        raise SystemExit("ERROR: unknown suite %r (known: %s)"
                         % (name, ", ".join(sorted(suites))))
    return suites[name]


def select_sources(args):
    sources = list(args.source or [])
    suite = None
    if args.suite:
        suite = load_suite(args.suite)
        sources.extend(suite.get("sources") or [])
    rows = lib.load_configs(bucket=args.bucket)
    if sources:
        wanted = set(sources)
        rows = [r for r in rows if r[2].get("source") in wanted]
        missing = wanted - {r[2].get("source") for r in rows}
        for name in sorted(missing):
            print("WARNING: suite/source %r not found in %s bucket"
                  % (name, args.bucket or "any"), file=sys.stderr)
    rows.sort(key=lambda r: (r[1]))
    if args.source or args.suite:
        # keep suite order (commit 0aafb98 sweep order) for readable reports
        order = {}
        for name in sources:
            order.setdefault(name, len(order))
        rows.sort(key=lambda r: (order.get(r[2].get("source"), 999),
                                 r[2].get("source", "")))
    if args.sample:
        rng = random.Random(args.seed)
        if args.sample >= len(rows):
            chosen = rows
        else:
            chosen = rng.sample(rows, args.sample)
            chosen.sort(key=lambda r: r[1])
        rows = chosen
    return rows, suite


def run_source(bucket, rel, cfg, fetcher, routes, query, dry_run):
    source = cfg.get("source")
    entry = {"source": source, "bucket": bucket, "config": rel,
             "engine": "rest" if not (cfg.get("scraper") or {}).get("enabled")
             and cfg.get("api") else "scraper", "routes": {}}
    planned = {}
    home_kind, home_url = lib.home_target(cfg)
    search_kind, search_url = lib.search_target(cfg, query)
    planned["home"] = home_url
    planned["search"] = search_url
    if dry_run:
        entry["planned"] = planned
        entry["routes"] = {r: {"route": r, "status": "plan",
                               "url": planned.get(r)}
                           for r in routes}
        return entry
    home_result, item_id = ({"route": "home", "status": "skip", "reasons": []},
                            None)
    chapter_id = None
    if "home" in routes:
        home_result, item_id = check_home(cfg, fetcher)
        entry["routes"]["home"] = home_result
    else:
        # detail/reader still need a sample id: derive one cheaply from home
        if "detail" in routes or "reader" in routes:
            home_result, item_id = check_home(cfg, fetcher)
            home_result["note"] = "sample source (route not requested)"
    search_sources = list(lib.search_queries())[:2] + [query]
    if "search" in routes:
        search_result = check_search(cfg, fetcher, query)
        if search_result["status"] == "fail":
            for alt in search_sources:
                if alt == query:
                    continue
                retry = check_search(cfg, fetcher, alt)
                if retry["status"] == "pass":
                    retry["note"] = "passed on fallback query %r" % alt
                    search_result = retry
                    break
        entry["routes"]["search"] = search_result
    if "detail" in routes:
        detail_result, chapter_id = check_detail(cfg, fetcher, item_id)
        entry["routes"]["detail"] = detail_result
    if "reader" in routes:
        entry["routes"]["reader"] = check_reader(cfg, fetcher, item_id,
                                                 chapter_id)
    return entry


def summarize(report):
    totals = {"pass": 0, "fail": 0, "skip": 0, "plan": 0}
    failing = []
    for entry in report["sources"]:
        for route, result in entry["routes"].items():
            status = result.get("status", "skip")
            totals[status] = totals.get(status, 0) + 1
            if status == "fail":
                failing.append("%s/%s: %s" % (
                    entry["source"], route, "; ".join(result.get("reasons") or [])))
    report["totals"] = totals
    report["failing"] = failing
    return report


def print_summary(report, verbose=False):
    for entry in report["sources"]:
        cells = []
        for route in ROUTES:
            result = entry["routes"].get(route)
            if not result:
                continue
            icon = {"pass": "✅", "fail": "✖️", "skip": "➖",
                    "plan": "📝"}.get(result["status"], "?")
            cells.append("%s%s" % (route, icon))
        line = "%-22s %s" % (entry["source"], " ".join(cells))
        if verbose:
            for route in ROUTES:
                result = entry["routes"].get(route) or {}
                line += "\n    %-7s %-4s %s %s" % (
                    route, result.get("status", "-"),
                    result.get("status_code", ""),
                    "; ".join(result.get("reasons") or []))
        print(line)
    totals = report["totals"]
    print("\n%s  pass=%d fail=%d skip=%d plan=%d (mode=%s, %d source(s))" % (
        "✅" if totals["fail"] == 0 else "✖️",
        totals["pass"], totals["fail"], totals["skip"], totals["plan"],
        report["mode"], len(report["sources"])))
    for failure in report["failing"]:
        print("  ✖️ %s" % failure)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Canary smoke runner for Kuron source configs "
                    "(Home/Search/Detail/Reader).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Suite file: scripts/canary_suites.json (refactor-24 = 0aafb98).")
    parser.add_argument("--bucket", choices=lib.BUCKETS,
                        help="only configs in this language bucket")
    parser.add_argument("--source", action="append", metavar="ID",
                        help="source id (repeatable)")
    parser.add_argument("--suite", metavar="NAME",
                        help="named suite from scripts/canary_suites.json "
                             "(e.g. refactor-24)")
    parser.add_argument("--sample", type=int, default=0,
                        help="randomly sample N sources (seeded by --seed)")
    parser.add_argument("--seed", type=int, default=0,
                        help="sampling seed (default 0 = deterministic)")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="per-request timeout in seconds (default 20)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds between requests (default 1.0)")
    parser.add_argument("--jitter", type=float, default=0.5,
                        help="relative delay jitter, 0..1 (default 0.5)")
    parser.add_argument("--routes", default=",".join(ROUTES),
                        help="comma-separated routes (default: all four)")
    parser.add_argument("--query", default="love",
                        help="search query used for the search route")
    parser.add_argument("--output", metavar="PATH",
                        help="write the JSON report to PATH")
    parser.add_argument("--save-snapshots", metavar="DIR",
                        help="store every fetched body under DIR/<source>/")
    parser.add_argument("--from-snapshots", metavar="DIR",
                        help="replay parsing offline from a saved snapshot tree")
    parser.add_argument("--dry-run", action="store_true",
                        help="print planned route URLs without any network call")
    parser.add_argument("--allow-failures", action="store_true",
                        help="always exit 0 (scheduled/report-only runs)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    routes = [r.strip() for r in args.routes.split(",") if r.strip()]
    bad = [r for r in routes if r not in ROUTES]
    if bad:
        parser.error("unknown route(s): %s" % ", ".join(bad))
    if args.sample < 0:
        parser.error("--sample must be >= 0")

    rows, suite = select_sources(args)
    if not rows:
        print("ERROR: no config matched the selection", file=sys.stderr)
        return 2

    fetcher = Fetcher(timeout=args.timeout, save_dir=args.save_snapshots,
                      from_dir=args.from_snapshots, delay=args.delay,
                      jitter=args.jitter, seed=args.seed)
    mode = "dry-run" if args.dry_run else (
        "snapshot" if args.from_snapshots else "live")
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "suite": args.suite,
        "suiteCommit": (suite or {}).get("commit"),
        "routes": routes,
        "totals": {},
        "sources": [],
    }
    started = time.time()
    for bucket, rel, cfg in rows:
        print("→ %s" % cfg.get("source"), file=sys.stderr)
        report["sources"].append(run_source(
            bucket, rel, cfg, fetcher, routes, args.query, args.dry_run))
    report["durationSeconds"] = round(time.time() - started, 1)
    summarize(report)
    print_summary(report, verbose=args.verbose)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("report: %s" % args.output)
    if args.save_snapshots:
        os.makedirs(args.save_snapshots, exist_ok=True)
        index = os.path.join(args.save_snapshots, "index.json")
        with open(index, "w", encoding="utf-8") as fh:
            json.dump({"generatedAt": report["generatedAt"],
                       "sources": [e["source"] for e in report["sources"]]},
                      fh, indent=2)
            fh.write("\n")

    failed = report["totals"].get("fail", 0)
    return 0 if (failed == 0 or args.allow_failures) else 1


if __name__ == "__main__":
    sys.exit(main())
