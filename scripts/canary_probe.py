#!/usr/bin/env python3
"""Canary probe: periodic HTTP + item-count watchdog for every source config.

Unlike ``canary_smoke_runner.py`` (four routes, used before a release) this
probe is the cheap scheduled watchdog: it fetches only Home and Search, counts
the items the config's own selectors/endpoints produce, and compares the
result with a stored baseline to report *drift*:

  critical  HTTP status changed away from 2xx / network error, or item count
            dropped to 0 (selector or API shape most likely changed upstream)
  warning   item count dropped more than ``--drop-threshold`` (default 50%)
  info      Cloudflare challenge on a CI IP, recovered status, count increase
  skip      config has maintenance=true, no such route, or a client-decoded
            ``secondaryFetch`` shell (raw HTML has no items by design)

Politeness: one request at a time, ``--delay`` seconds with ``--jitter``,
retry-on-429/503 inside :mod:`canary_lib`, and a rotating sample (pass a
weekly seed, e.g. ``--seed $(date +%V)``) so 2x/week runs cover all 120 sources
without hammering them all at once.

Examples::

    python3 scripts/canary_probe.py --help
    python3 scripts/canary_probe.py --dry-run --sample 10
    python3 scripts/canary_probe.py --bucket en --sample 8 --delay 2
    python3 scripts/canary_probe.py --sample 30 --seed 47 \\
        --baseline .canary/baseline.json --update-baseline \\
        --output .canary/report.json --markdown canary-report.md \\
        --fail-on-drift

Exit codes: 0 no drift, 1 critical drift (2 with --strict when only warnings),
2 bad arguments/selection.

Refs #62.
"""

import argparse
import hashlib
import json
import os
import random
import sys
from datetime import datetime, timezone

import canary_lib as lib

DEFAULT_BASELINE = os.path.join(".canary", "baseline.json")
ROUTES = ("home", "search")

# Hash-picked generic queries do not match every site (xiutaku: "comic"/"manga"
# return 0 items while "love"/"girl" return results). Register a known-good
# sample query per source; a second one is a bounded fallback if the index shifts.
SAMPLE_QUERIES = {"xiutaku": ("love", "girl")}

# A 403/503 carrying a Cloudflare challenge is a CI/datacenter-IP block, not
# selector drift (seen on ravenscansorg; hentailoop under CI). Plain 403s stay
# critical so real access regressions are still reported.
CF_BLOCK_MARKERS = (
    "just a moment", "cf-turnstile", "challenges.cloudflare.com",
    "attention required! | cloudflare", "checking your browser",
    "enable javascript and cookies to continue", "error code: 1020",
    "cloudflare ray id", "cf-browser-verification",
)


def sample_queries(source):
    """Return a bounded list of search samples for a source."""
    preferred = SAMPLE_QUERIES.get(source)
    if preferred:
        return list(preferred)
    queries = lib.search_queries()
    pick = queries[int(hashlib.sha256(source.encode()).hexdigest(), 16)
                   % len(queries)]
    return [pick]


def classify_block(fetched):
    """Return a Cloudflare-challenge reason for a blocked response, else None."""
    if fetched.status not in (403, 503):
        return None
    body = (fetched.text or "").lower()
    if any(marker in body for marker in CF_BLOCK_MARKERS):
        return "Cloudflare challenge %s (CI-IP block, not selector drift)" % (
            fetched.status)
    return None


# ---------------------------------------------------------------------------
# probing
# ---------------------------------------------------------------------------

def probe_route(cfg, route, query, fetcher_cfg, rate_limit=None):
    """Return a probe record for one route; never raises on network errors."""
    record = {"route": route, "status_code": None, "item_count": None,
              "error": None, "url": None, "engine": None}
    features = cfg.get("features") or {}
    if route == "home":
        kind, url = lib.home_target(cfg)
    else:
        if features.get("search") is False and not cfg.get("api"):
            record["error"] = "features.search=false"
            record["skipped"] = True
            return record
        entry = lib.url_patterns(cfg).get("search")
        if isinstance(entry, dict) and \
                str(entry.get("method", "")).lower() == "post":
            record["error"] = "POST search form not probed"
            record["skipped"] = True
            return record
        kind, url = lib.search_target(cfg, query)
    # *_target returns (None, reason) when a route is absent; the reason must
    # not be handed to http_get as if it were a URL (beauty3600000: no search).
    if kind not in ("html", "api") or not url:
        record["error"] = url if isinstance(url, str) and url else "no route"
        record["skipped"] = True
        return record
    record["url"] = url
    record["engine"] = kind
    if fetcher_cfg.get("dry_run"):
        record["skipped"] = True
        record["error"] = "dry-run"
        return record
    if rate_limit is not None:
        rate_limit()
    fetched = lib.http_get(url, lib.headers_for(cfg),
                           timeout=fetcher_cfg["timeout"], attempts=2)
    record["status_code"] = fetched.status
    record["latency_ms"] = int(fetched.elapsed * 1000)
    block = classify_block(fetched)
    if block:
        record["error"] = block
        record["blocked"] = True
        return record
    if not fetched.ok():
        record["error"] = fetched.error or ("HTTP %s" % fetched.status)
        return record
    if kind == "api":
        try:
            payload = json.loads(fetched.text)
        except ValueError:
            record["error"] = "response is not JSON"
            return record
        items_path = ((cfg.get("api") or {}).get("list") or {}).get("items")
        record["item_count"] = lib.json_item_count(payload, items_path)
    else:
        name = "home" if route == "home" else "search"
        list_block = lib.resolve_list_block(cfg, name)
        container = (list_block or {}).get("container")
        if not container:
            record["error"] = "config has no list container"
            return record
        count = lib.count_items(fetched.text, container)
        secondary = (list_block or {}).get("secondaryFetch") or {}
        if secondary.get("enabled") and not count:
            # nicomanga (Next.js): the shell HTML has no cards; the real list
            # arrives from secondaryFetch and is decoded client-side.
            record["error"] = "secondaryFetch shell (items decoded client-side)"
            record["skipped"] = True
            return record
        record["item_count"] = count
    return record


def load_baseline(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "sources" in data else None


def save_baseline(path, baseline):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(baseline, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")


def merge_baseline(existing, report):
    baseline = {"schemaVersion": 1,
                "generatedAt": report["generatedAt"],
                "sources": dict((existing or {}).get("sources") or {})}
    for entry in report["sources"]:
        source = entry["source"]
        row = baseline["sources"].setdefault(source, {})
        for route in ROUTES:
            record = entry["routes"].get(route) or {}
            if record.get("skipped") or record.get("status_code") is None:
                continue  # keep last known good values on transient failure
            if not (200 <= (record.get("status_code") or 0) < 300):
                continue  # a 404/5xx is drift, not a new baseline
            if not record.get("item_count"):
                continue  # 0 items is drift too
            row["%sStatus" % route] = record["status_code"]
            row["%sItems" % route] = record["item_count"]
            row["%sUrl" % route] = record.get("url")
        row["probedAt"] = report["generatedAt"]
    return baseline


def detect_drift(entry, baseline, drop_threshold):
    drifts = []
    prior = ((baseline or {}).get("sources") or {}).get(entry["source"])
    for route in ROUTES:
        current = entry["routes"].get(route) or {}
        if current.get("skipped"):
            continue
        if current.get("blocked"):
            drifts.append({"route": route, "severity": "info",
                           "detail": current.get("error") or "blocked"})
            continue
        old_status = (prior or {}).get("%sStatus" % route)
        old_items = (prior or {}).get("%sItems" % route)
        status = current.get("status_code")
        items = current.get("item_count")
        if status is None or not (200 <= status < 300):
            if old_status and 200 <= old_status < 300:
                drifts.append({
                    "route": route, "severity": "critical",
                    "detail": "HTTP %s (was %s)" % (
                        status or current.get("error") or "ERR", old_status)})
            else:
                drifts.append({
                    "route": route, "severity": "critical",
                    "detail": "unreachable: %s" % (
                        current.get("error") or status or "unknown")})
            continue
        if items == 0:
            drifts.append({"route": route, "severity": "critical",
                           "detail": "0 items parsed (selector/API shape drift?)"})
            continue
        if prior is None:
            continue  # first run: --update-baseline seeds silently
        if isinstance(old_items, int) and old_items > 0 and isinstance(items, int):
            ratio = items / float(old_items)
            if ratio < (1.0 - drop_threshold):
                sev = "critical" if items <= old_items * 0.1 else "warning"
                drifts.append({
                    "route": route, "severity": sev,
                    "detail": "items %d -> %d (-%d%%)" % (
                        old_items, items, round((1 - ratio) * 100))})
            elif items > old_items:
                drifts.append({"route": route, "severity": "info",
                               "detail": "items %d -> %d" % (old_items, items)})
    return drifts


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def write_markdown(path, report):
    lines = ["# Canary drift report", "",
             "Generated: %s (mode: %s, seed: %s)" % (
                 report["generatedAt"], report["mode"], report["seed"]),
             ""]
    totals = report["totals"]
    lines.append("Probed %d source(s): %d ok, %d critical, %d warning, "
                 "%d info, %d skipped." % (
                     totals["sources"], totals["ok"], totals["critical"],
                     totals["warning"], totals["info"], totals["skipped"]))
    lines.append("")
    drifting = [e for e in report["sources"] if e["drifts"]]
    if not drifting:
        lines.append("No drift detected against the baseline. ✅")
    else:
        lines.append("| Source | Route | Severity | Detail |")
        lines.append("|---|---|---|---|")
        order = {"critical": 0, "warning": 1, "info": 2}
        for entry in drifting:
            for drift in sorted(entry["drifts"],
                                key=lambda d: order.get(d["severity"], 3)):
                lines.append("| `%s` | %s | %s | %s |" % (
                    entry["source"], drift["route"],
                    {"critical": "🚫 critical", "warning": "⚠️ warning",
                     "info": "ℹ️ info"}.get(drift["severity"], drift["severity"]),
                    drift["detail"].replace("|", "\\|")))
    lines.append("")
    lines.append("Baseline: `%s`%s" % (
        report["baselinePath"] or "(none)",
        "" if report["baselinePath"] else " — first run only builds it"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scheduled canary probe: HTTP status + Home/Search item "
                    "counts, drift against a baseline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Default baseline: .canary/baseline.json (CI caches this path).")
    parser.add_argument("--bucket", choices=lib.BUCKETS,
                        help="only probe one language bucket")
    parser.add_argument("--source", action="append", metavar="ID",
                        help="source id (repeatable)")
    parser.add_argument("--sample", type=int, default=0,
                        help="probe N randomly chosen sources (seeded)")
    parser.add_argument("--seed", type=int, default=0,
                        help="sampling seed; pass the ISO week to rotate "
                             "(e.g. --seed $(date +%%V))")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--delay", type=float, default=2.0,
                        help="seconds between requests (default 2.0)")
    parser.add_argument("--jitter", type=float, default=0.5,
                        help="relative delay jitter 0..1 (default 0.5)")
    parser.add_argument("--drop-threshold", type=float, default=0.5,
                        help="item-count drop ratio that counts as drift "
                             "(default 0.5 = 50%%)")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE,
                        help="baseline JSON path (default: %(default)s)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="merge successful probes into the baseline")
    parser.add_argument("--output", metavar="PATH",
                        help="write the JSON report to PATH")
    parser.add_argument("--markdown", metavar="PATH",
                        help="write a Markdown drift report (for $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--fail-on-drift", action="store_true",
                        help="exit 1 when critical drift is found")
    parser.add_argument("--strict", action="store_true",
                        help="with --fail-on-drift, warnings also fail")
    parser.add_argument("--dry-run", action="store_true",
                        help="print planned probes without network calls")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.sample < 0:
        parser.error("--sample must be >= 0")
    if not (0 < args.timeout <= lib.MAX_TIMEOUT_SECONDS):
        parser.error("--timeout must be in (0, %g]" % lib.MAX_TIMEOUT_SECONDS)
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    if not (0.0 <= args.jitter <= 1.0):
        parser.error("--jitter must be in [0, 1]")
    if not (0.0 < args.drop_threshold <= 1.0):
        parser.error("--drop-threshold must be in (0, 1]")

    rows = lib.load_configs(bucket=args.bucket)
    if args.source:
        wanted = set(args.source)
        rows = [r for r in rows if r[2].get("source") in wanted]
    rows.sort(key=lambda r: r[1])
    if args.sample:
        rng = random.Random(args.seed)
        rows = sorted(rng.sample(rows, min(args.sample, len(rows))),
                      key=lambda r: r[1])
    if not rows:
        print("ERROR: no config matched the selection", file=sys.stderr)
        return 2

    baseline = load_baseline(args.baseline)
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run" if args.dry_run else "live",
        "seed": args.seed,
        "baselinePath": args.baseline if baseline else None,
        "dropThreshold": args.drop_threshold,
        "totals": {"sources": 0, "ok": 0, "critical": 0, "warning": 0,
                   "info": 0, "skipped": 0},
        "sources": [],
    }
    rng = random.Random(args.seed)
    fetcher_cfg = {"timeout": args.timeout, "dry_run": args.dry_run}
    probed_any = [False]

    def pace():
        if probed_any[0]:
            lib.polite_sleep(lib.jitter_delay(args.delay, args.jitter, rng))
        probed_any[0] = True

    def probe_search(cfg, queries):
        """Probe search; retry once with a registered sample if items == 0."""
        record = None
        for q in queries:
            record = probe_route(cfg, "search", q, fetcher_cfg,
                                 rate_limit=pace)
            record["query"] = q
            if record.get("item_count") or record.get("skipped") or \
                    record.get("blocked"):
                break
        return record

    for bucket, rel, cfg in rows:
        source = cfg.get("source")
        queries = sample_queries(source)
        query = queries[0]
        entry = {"source": source, "bucket": bucket, "config": rel,
                 "engine": "rest" if cfg.get("api") and not
                 (cfg.get("scraper") or {}).get("enabled") else "scraper",
                 "routes": {}, "drifts": []}
        if cfg.get("maintenance") is True:
            entry["skipped"] = "maintenance=true"
            report["totals"]["skipped"] += 1
            report["sources"].append(entry)
            continue
        for route in ROUTES:
            if args.dry_run:
                kind, url = (lib.home_target(cfg) if route == "home"
                             else lib.search_target(cfg, query))
                entry["routes"][route] = {
                    "route": route, "url": url,
                    "engine": kind if isinstance(kind, str) else None,
                    "skipped": True, "error": "dry-run"}
                continue
            if route == "search":
                entry["routes"][route] = probe_search(cfg, queries)
            else:
                entry["routes"][route] = probe_route(
                    cfg, route, query, fetcher_cfg, rate_limit=pace)
            if args.verbose:
                record = entry["routes"][route]
                print("  %-7s %s %s items=%s" % (
                    route, record.get("status_code") or "-",
                    record.get("error") or "ok", record.get("item_count")),
                    file=sys.stderr)
        if not args.dry_run:
            entry["drifts"] = detect_drift(entry, baseline,
                                           args.drop_threshold)
        severities = {d["severity"] for d in entry["drifts"]}
        for sev in ("critical", "warning", "info"):
            if sev in severities:
                report["totals"][sev] += 1
        if args.dry_run:
            report["totals"]["skipped"] += 1
        elif not severities and not entry.get("skipped"):
            report["totals"]["ok"] += 1
        report["totals"]["sources"] += 1
        report["sources"].append(entry)
        if args.verbose:
            print("→ %s: %s" % (source, ", ".join(
                "%s %s" % (d["route"], d["severity"]) for d in entry["drifts"])
                or "ok"), file=sys.stderr)

    if args.update_baseline and not args.dry_run:
        save_baseline(args.baseline, merge_baseline(baseline, report))
        report["baselineUpdated"] = args.baseline
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    if args.markdown:
        write_markdown(args.markdown, report)

    totals = report["totals"]
    print("%s  sources=%d ok=%d critical=%d warning=%d info=%d skipped=%d" % (
        "✅" if totals["critical"] == 0 else "✖️",
        totals["sources"], totals["ok"], totals["critical"],
        totals["warning"], totals["info"], totals["skipped"]))
    for entry in report["sources"]:
        critical = [d for d in entry["drifts"] if d["severity"] == "critical"]
        warning = [d for d in entry["drifts"] if d["severity"] == "warning"]
        if critical or warning:
            print("  %s %s" % (
                "✖️" if critical else "⚠️",
                entry["source"] + ": " + "; ".join(
                    "%s %s (%s)" % (d["severity"], d["route"], d["detail"])
                    for d in critical + warning)))
    if args.fail_on_drift:
        if totals["critical"]:
            return 1
        if args.strict and totals["warning"]:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
