#!/usr/bin/env python3
"""Conformance gate: static config-vs-engine contract checks.

Rules (no network):
  R1  every `urlPatterns.*Search*` URL carries a `{tag}`/`{query}`/`{page}`
      placeholder it claims (no dead routes by construction).
  R2  detail `author`/`artists` fields must set `multi: true` (single-match
      extraction silently drops co-authors).
  R3  no `{slug}` placeholder in urlPatterns (engine forwards it raw → 404).
  R4  searchForm fields must declare a non-empty `queryParam` (empty emits
      garbage `=value` pairs the server ignores).
  R5  `navigation.tagQueryMapping` entries must be well-formed
      (`mode: name` needs nothing; `rawParam` needs `param`).

Live-route coverage belongs to the generator smoke 6th screen (taxonomy
probe through the real adapter), not to this static gate.

Waivers: `--waive=R1,R3` (comma-separated rule ids). Waivers are printed
in the report; `--waive-expiry YYYY-MM-DD` stamps them (advisory only).

Exit code: 1 on any unwaived violation, unless `--warn-only` (report only).
Usage:
  python3 scripts/check_conformance.py [--warn-only]
      [--waive=R1,R3] [--waive-expiry YYYY-MM-DD] [--json]
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_GLOB = os.path.join(ROOT, "config", "*", "*-config.json")

SEARCH_KEYS = (
    "genreSearch",
    "genreSearchPage",
    "tagSearch",
    "tagSearchPage",
    "authorSearch",
    "authorSearchPage",
    "artistSearch",
    "artistSearchPage",
)

# ruleId -> (check(config, path) -> list[str] violations)


def _patterns(cfg):
    scraper = cfg.get("scraper") or {}
    return scraper.get("urlPatterns") or {}


def _rule_r1(cfg, path):
    out = []
    for key in SEARCH_KEYS:
        entry = _patterns(cfg).get(key)
        if entry is None:
            continue
        url = entry if isinstance(entry, str) else entry.get("url", "")
        if "{tag}" not in url and "{query}" not in url:
            out.append(f"{path}: R1 {key} has no {{tag}}/{{query}} placeholder: {url}")
    return out


def _rule_r2(cfg, path):
    out = []
    try:
        fields = cfg["scraper"]["selectors"]["detail"]["fields"]
    except (KeyError, TypeError):
        return out
    if not isinstance(fields, dict):
        return out
    for name in ("author", "artists", "artist"):
        field = fields.get(name)
        if isinstance(field, dict) and field.get("multi") is not True:
            out.append(f"{path}: R2 detail field `{name}` lacks multi:true")
    return out


def _rule_r3(cfg, path):
    out = []
    for key, entry in _patterns(cfg).items():
        url = entry if isinstance(entry, str) else (
            entry.get("url", "") if isinstance(entry, dict) else "")
        if "{slug}" in url:
            out.append(f"{path}: R3 {key} uses unsupported {{slug}}: {url}")
    return out


def _rule_r4(cfg, path):
    out = []
    params = (cfg.get("searchForm") or {}).get("params") or {}
    for name, field in params.items():
        if not isinstance(field, dict):
            continue
        qp = field.get("queryParam")
        if qp is not None and not str(qp).strip():
            out.append(
                f"{path}: R4 searchForm field `{name}` has empty queryParam")
    return out


def _rule_r5(cfg, path):
    out = []
    nav = cfg.get("navigation") or {}
    mapping = nav.get("tagQueryMapping") or {}
    for ttype, rule in mapping.items():
        if not isinstance(rule, dict):
            out.append(f"{path}: R5 tagQueryMapping.{ttype} is not an object")
            continue
        mode = str(rule.get("mode", "rawParam")).strip()
        if mode == "rawParam" and not str(rule.get("param", "")).strip():
            out.append(
                f"{path}: R5 tagQueryMapping.{ttype} rawParam needs `param`")
        elif mode not in ("rawParam", "name"):
            out.append(
                f"{path}: R5 tagQueryMapping.{ttype} unknown mode `{mode}`")
    return out


STATIC_RULES = (
    ("R1", _rule_r1),
    ("R2", _rule_r2),
    ("R3", _rule_r3),
    ("R4", _rule_r4),
    ("R5", _rule_r5),
)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Config conformance gate.")
    ap.add_argument("--warn-only", action="store_true",
                    help="report only, always exit 0")
    ap.add_argument("--waive", default="",
                    help="comma-separated rule ids to waive (e.g. R1,R3)")
    ap.add_argument("--waive-expiry", default="",
                    help="advisory expiry date recorded in the report")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable report on stdout")
    args = ap.parse_args(argv)

    waived = {w.strip() for w in args.waive.split(",") if w.strip()}

    files = sorted(glob.glob(CONFIG_GLOB))
    violations = []  # (rule, message)
    checked = 0
    for path in files:
        rel = os.path.relpath(path, ROOT)
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:  # noqa: BLE001
            violations.append(("R0", f"{rel}: unreadable config: {e}"))
            continue
        checked += 1
        for rule_id, fn in STATIC_RULES:
            if rule_id in waived:
                continue
            for msg in fn(cfg, rel):
                violations.append((rule_id, msg))

    blocking = [v for _, v in violations]
    report = {
        "schemaVersion": "1.0",
        "checked": checked,
        "waived": sorted(waived),
        "waiveExpiry": args.waive_expiry,
        "violations": [{"rule": r, "message": m} for r, m in violations],
    }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        if not violations:
            print(f"OK: {checked} configs conformant"
                  + (f" (waived: {sorted(waived)})" if waived else ""))
        for _, msg in violations:
            print(f"VIOLATION: {msg}")
        if waived:
            print(f"waived rules: {sorted(waived)}"
                  + (f" (expiry {args.waive_expiry})"
                     if args.waive_expiry else ""))

    if args.warn_only or not blocking:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
