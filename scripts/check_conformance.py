#!/usr/bin/env python3
"""Conformance gate: static config-vs-engine contract checks.

Rules (no network):
  R1  every `urlPatterns.*Search*` URL carries a `{tag}`/`{query}`/`{page}`
      placeholder it claims (no dead routes by construction). `method: post`
      patterns may carry it in the `form` body instead of the URL.
  R2  detail `author`/`artists` fields must set `multi: true` (single-match
      extraction silently drops co-authors).
  R3  no `{slug}` placeholder in urlPatterns (engine forwards it raw → 404).
  R4  searchForm fields must declare a non-empty `queryParam` (empty emits
      garbage `=value` pairs the server ignores). Checked in both homing
      spots: top-level `searchForm` and `scraper.searchForm` (Refs #61).
  R8  no retired authoring key `listKey` / `detailKey` / `readerKey` /
      `surfaces` inside any `secondaryFetch` block, on every config (not
      just `extends` files): the adapter never reads them (Refs #61).
  R9  `features.search` / `features.chapters` / `features.contentByTag`
      set to `true` must have a supporting route/selector: a matching
      `urlPatterns` entry, a `searchForm`, an enabled `api` block, an
      `adapterConfig`, or a `navigation.tagQueryMapping` entry —
      whichever family the config belongs to (Refs #61).
  R5  `navigation.tagQueryMapping` entries must be well-formed
      (`mode: name` needs nothing; `rawParam` needs `param`).
  R6  every `title` field object under a `fields` mapping declares
      `transform: cleanText` (trim + collapse internal whitespace to one
      space) so titles never leak raw newlines/repeated spaces to the app.
  R7  top-level `idType` is present and is exactly `real` or `slug`, so the
      consumer app can tell machine IDs from URL slugs without guessing.

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
        if "{tag}" in url or "{query}" in url:
            continue
        # POST patterns carry the placeholder in the form body, not the URL
        # (e.g. Rawkuma admin-ajax advanced_search with genre: ["{tag}"]).
        if isinstance(entry, dict) and \
                str(entry.get("method", "")).lower() == "post":
            form = entry.get("form") or {}
            blob = " ".join(str(v) for v in form.values())
            if "{tag}" in blob or "{query}" in blob:
                continue
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


def _search_forms(cfg):
    """Yield (location, searchForm) for both R4 homing spots (Refs #61)."""
    top = cfg.get("searchForm")
    if isinstance(top, dict):
        yield "searchForm", top
    nested = (cfg.get("scraper") or {}).get("searchForm")
    if isinstance(nested, dict):
        yield "scraper.searchForm", nested


def _rule_r4(cfg, path):
    out = []
    for location, form in _search_forms(cfg):
        params = form.get("params") or {}
        for name, field in params.items():
            if not isinstance(field, dict):
                continue
            qp = field.get("queryParam")
            if qp is not None and not str(qp).strip():
                out.append(
                    f"{path}: R4 {location} field `{name}` "
                    f"has empty queryParam")
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


def _title_field_defs(obj, parent_key=None):
    """Yield every field object named `title` that lives under a `fields` map."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "title" and parent_key == "fields" and isinstance(value, dict):
                yield value
            yield from _title_field_defs(value, key)
    elif isinstance(obj, list):
        for value in obj:
            yield from _title_field_defs(value, parent_key)


def _rule_r6(cfg, path):
    out = []
    for field in _title_field_defs(cfg):
        if field.get("transform") != "cleanText":
            out.append(f"{path}: R6 title field lacks transform:cleanText"
                       f": {field.get('selector', '<no selector>')}")
    return out


def _rule_r7(cfg, path):
    id_type = cfg.get("idType")
    if id_type not in ("real", "slug"):
        return [f"{path}: R7 idType must be 'real' or 'slug', got {id_type!r}"]
    return []


# Refs #61: retired authoring keys no live adapter read site understands.
# Checked on every config here (build_from_presets.py only sees `extends`).
LEGACY_SECONDARY_KEYS = ("listKey", "detailKey", "readerKey", "surfaces")


def _legacy_secondary_blocks(node, prefix=""):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = "%s/%s" % (prefix, key)
            if key == "secondaryFetch" and isinstance(value, dict):
                bad = [k for k in value if k in LEGACY_SECONDARY_KEYS]
                if bad:
                    found.append((path, bad))
            found.extend(_legacy_secondary_blocks(value, path))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(
                _legacy_secondary_blocks(value, "%s[%d]" % (prefix, i)))
    return found


def _rule_r8(cfg, path):
    out = []
    for block, bad in _legacy_secondary_blocks(cfg):
        out.append(
            f"{path}: R8 {block} carries retired key(s) "
            f"{', '.join(sorted(bad))} the adapter never reads")
    return out


def _api_endpoints(cfg):
    api = cfg.get("api") or {}
    if not isinstance(api, dict) or api.get("enabled") is not True:
        return {}
    endpoints = api.get("endpoints") or {}
    return endpoints if isinstance(endpoints, dict) else {}


def _rule_r9(cfg, path):
    """Cross-check features.*=true against a supporting route/selector."""
    out = []
    features = cfg.get("features") or {}
    if not isinstance(features, dict):
        return out
    patterns = _patterns(cfg)
    selectors = (cfg.get("scraper") or {}).get("selectors") or {}
    api = _api_endpoints(cfg)
    api_blob = json.dumps(api) if api else ""
    has_adapter = isinstance(cfg.get("adapterConfig"), dict)

    def search_ok():
        if "search" in patterns:
            return True
        for _, form in _search_forms(cfg):
            if isinstance(form.get("params"), dict) and form["params"]:
                return True
        if api and any(k in api_blob for k in ("{query}", "keyword", "/search",
                                               "/books", "browse")):
            return True
        return has_adapter

    def chapters_ok():
        if "chapter" in patterns:
            return True
        detail = selectors.get("detail") or {}
        if isinstance(detail, dict) and "chapters" in detail:
            return True
        if api and any(k in api_blob for k in ("chapter", "/books",
                                               "detail", "images", "pages")):
            return True
        return bool(cfg.get("requiresSpecialAdapter"))

    def content_by_tag_ok():
        if any(k in patterns for k in ("genreSearch", "tagSearch",
                                       "authorSearch", "artistSearch")):
            return True
        nav = cfg.get("navigation") or {}
        mapping = nav.get("tagQueryMapping") or {}
        if isinstance(mapping, dict) and mapping:
            return True
        if cfg.get("genreMapping") or cfg.get("languageTagMap"):
            return True
        if api and any(k in api_blob for k in ("tag", "search", "genre",
                                               "/books", "browse")):
            return True
        return has_adapter

    checks = (
        ("search", "search route (urlPatterns.search/searchForm/api)",
         search_ok),
        ("chapters", "chapter route (urlPatterns.chapter/detail.chapters/api)",
         chapters_ok),
        ("contentByTag", "tag route (genreSearch/tagSearch/mapping/api)",
         content_by_tag_ok),
    )
    for feat, need, ok_fn in checks:
        if features.get(feat) is True and not ok_fn():
            out.append(
                f"{path}: R9 features.{feat}=true but no supporting {need}")
    return out


STATIC_RULES = (
    ("R1", _rule_r1),
    ("R2", _rule_r2),
    ("R3", _rule_r3),
    ("R4", _rule_r4),
    ("R5", _rule_r5),
    ("R6", _rule_r6),
    ("R7", _rule_r7),
    ("R8", _rule_r8),
    ("R9", _rule_r9),
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
            print(f"OK: {checked} config(s) passed R1-R9 conformance, "
                  f"0 violation(s)."
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
