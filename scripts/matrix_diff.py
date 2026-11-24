#!/usr/bin/env python3
"""Regression guard for SUPPORT-CONFIG.md (feature-matrix diff).

Compares the support matrix at ``--base`` (default ``origin/main``) with the
current working tree (or ``--head <ref>``) and fails CI when a status drops
without an explanation, e.g. ``✅`` -> ``⚠️``/``✖️``/``🚫``.

A drop is *explained* (warning, not error) when the head row's Note carries a
verification/regression marker (``live``, ``issue #``, ``regress``, ``turun``,
``degrad``, ``verified`` ...) or when the manifest version for that source was
bumped between base and head — i.e. the config change and the manifest update
are in sync, exactly what AGENTS.md asks for.

Examples::

    python3 scripts/matrix_diff.py --base origin/main
    python3 scripts/matrix_diff.py --base origin/main --fail-on-regression
    python3 scripts/matrix_diff.py --base HEAD~1 --json
    python3 scripts/matrix_diff.py --base main --head HEAD --allow milftoon

Exit codes: 0 clean / waived only, 1 unexplained regression with
``--fail-on-regression``, 2 usage or missing ref/file.

Refs #64.
"""

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATRIX = "SUPPORT-CONFIG.md"
MANIFEST = "manifest.json"
MARKER_PREFIX = "<!-- matrix-columns:"
RANK = {"✅": 3, "⚠️": 2, "✖️": 1, "🚫": 0}
STATUS_ORDER = ("✅", "⚠️", "✖️", "🚫")
ACK_MARKERS = ("live", "issue #", "regress", "turun", "degrad", "downgrade",
               "verified", "re-verify", "reverify", "perbaikan", "fixed")


GIT_TIMEOUT_SECONDS = 30


def git(*args):
    return subprocess.run(("git",) + args, cwd=ROOT, capture_output=True,
                          timeout=GIT_TIMEOUT_SECONDS)


def read_at(ref, path):
    """Return file text at a git ref, or None when absent."""
    if ref is None:
        try:
            with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None
    proc = git("show", "%s:%s" % (ref, path))
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def rev(ref):
    proc = git("rev-parse", "--short", ref)
    return proc.stdout.decode().strip() if proc.returncode == 0 else ref


def status_rank(cell):
    cell = (cell or "").strip()
    for symbol in STATUS_ORDER:
        if cell.startswith(symbol):
            return RANK[symbol]
    return None


def status_symbol(rank):
    for symbol, value in RANK.items():
        if value == rank:
            return symbol
    return "?"


def split_row(line, ncols=10):
    """Split one matrix row; extra `|` characters belong to the Note cell."""
    parts = line.rstrip("\n").split("|")
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    if len(parts) < 3:
        return None
    number, path, rest = parts[0], parts[1], parts[2:]
    # status columns, everything after them is the free-text Note
    statuses = rest[:ncols]
    note = "|".join(rest[ncols:]).replace("\\|", "|")
    match = re.search(r"`([^`]+-config\.json)`", path)
    config_path = match.group(1) if match else path.split("<br>")[0].strip()
    return {
        "number": number.strip(),
        "config": config_path,
        "statuses": statuses,
        "note": note.strip(),
    }


def parse_matrix(text):
    if text is None:
        return None
    columns = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(MARKER_PREFIX):
            inner = stripped[len(MARKER_PREFIX):].strip()
            if inner.endswith("-->"):
                inner = inner[:-3].strip()
            columns = [c.strip() for c in inner.split("|") if c.strip()]
            break
    ncols = len(columns) or 10
    rows = {}
    in_table = False
    for line in text.splitlines():
        if not line.startswith("|"):
            in_table = False
            continue
        if re.match(r"^\|\s*No\s*\|", line) or re.match(
                r"^\|\s*---", line.replace(" ", "")) or "|---" in line:
            in_table = True
            continue
        if not in_table or not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        row = split_row(line, ncols=ncols)
        if row is None:
            continue
        rows[row["config"]] = row
    if not columns and rows:
        columns = ["home", "home_pag", "search", "search_pag", "detail",
                   "reader", "genre", "tag", "author", "artist"]
    return {"columns": columns, "rows": rows}


def manifest_versions(text):
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    versions = {}
    for entry in data.get("installableSources") or []:
        if entry.get("id"):
            versions[entry["id"]] = entry.get("version")
    return versions


def source_from_path(path):
    return os.path.basename(path).replace("-config.json", "")


def file_exists_at(ref, path):
    if ref is None:
        return os.path.exists(os.path.join(ROOT, path))
    return git("cat-file", "-e", "%s:%s" % (ref, path)).returncode == 0


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Diff SUPPORT-CONFIG.md against a base ref and flag "
                    "unexplained status regressions (Refs #64).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main",
                        help="base ref (default: origin/main)")
    parser.add_argument("--head", default=None,
                        help="head ref (default: current working tree)")
    parser.add_argument("--file", default=MATRIX,
                        help="matrix file (default: %(default)s)")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="exit 1 on an unexplained status drop")
    parser.add_argument("--strict", action="store_true",
                        help="treat removed/renamed rows as failures too")
    parser.add_argument("--allow", action="append", metavar="SOURCE",
                        help="waive one source id (repeatable)")
    parser.add_argument("--json", action="store_true",
                        help="emit a JSON report")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    allowed = set(args.allow or [])

    if args.base.startswith("-") or git("rev-parse", "--verify", args.base).returncode != 0:
        print("ERROR: base ref %r not found (fetch it first, e.g. "
              "`git fetch origin`)" % args.base, file=sys.stderr)
        return 2
    if args.head and (args.head.startswith("-")
                      or git("rev-parse", "--verify", args.head).returncode != 0):
        print("ERROR: head ref %r not found" % args.head, file=sys.stderr)
        return 2

    base_text = read_at(args.base, args.file)
    head_text = read_at(args.head, args.file)
    if base_text is None:
        print("ERROR: %s missing at %s" % (args.file, args.base), file=sys.stderr)
        return 2
    if head_text is None:
        print("ERROR: %s missing at %s" % (
            args.file, args.head or "working tree"), file=sys.stderr)
        return 2

    base = parse_matrix(base_text)
    head = parse_matrix(head_text)
    columns = head["columns"] or base["columns"]
    base_versions = manifest_versions(read_at(args.base, MANIFEST))
    head_versions = manifest_versions(read_at(args.head, MANIFEST))

    regressions, waived, removed, added = [], [], [], []
    for path, base_row in sorted(base["rows"].items()):
        head_row = head["rows"].get(path)
        if head_row is None:
            if file_exists_at(args.head, path):
                removed.append({"config": path,
                                "detail": "row dropped but config file still exists"})
            continue
        source = source_from_path(path)
        note = head_row.get("note") or ""
        note_changed = note != (base_row.get("note") or "")
        manifest_bumped = (source not in allowed
                           and base_versions.get(source)
                           and head_versions.get(source)
                           and base_versions.get(source) != head_versions.get(source))
        for idx, column in enumerate(columns):
            old_cell = base_row["statuses"][idx] if idx < len(base_row["statuses"]) else ""
            new_cell = head_row["statuses"][idx] if idx < len(head_row["statuses"]) else ""
            old_rank, new_rank = status_rank(old_cell), status_rank(new_cell)
            if old_rank is None or new_rank is None or new_rank >= old_rank:
                continue
            lowered = note.lower()
            acknowledged = note_changed and any(
                marker in lowered for marker in ACK_MARKERS)
            entry = {
                "config": path,
                "source": source,
                "column": column,
                "from": status_symbol(old_rank),
                "to": status_symbol(new_rank),
                "note": note[:240],
                "reason": ("allowed" if source in allowed else
                           "note explains drop" if acknowledged else
                           "manifest version bumped" if manifest_bumped else
                           "unexplained"),
            }
            if entry["reason"] == "unexplained":
                regressions.append(entry)
            else:
                waived.append(entry)
    for path in sorted(set(head["rows"]) - set(base["rows"])):
        added.append({"config": path, "detail": "new matrix row"})

    errors = list(regressions)
    if args.strict:
        errors.extend(removed)

    if args.json:
        print(json.dumps({
            "base": args.base,
            "baseRev": rev(args.base),
            "head": args.head or "working-tree",
            "headRev": rev(args.head or "HEAD"),
            "columns": columns,
            "baseRows": len(base["rows"]),
            "headRows": len(head["rows"]),
            "regressions": regressions,
            "waived": waived,
            "removed": removed,
            "added": added,
            "failed": bool(errors) and args.fail_on_regression,
        }, indent=2, ensure_ascii=False))
    else:
        print("matrix diff: %s (%s) -> %s (%s)" % (
            args.base, rev(args.base), args.head or "working tree",
            rev(args.head or "HEAD")))
        print("rows: %d base, %d head, %d added, %d removed" % (
            len(base["rows"]), len(head["rows"]), len(added), len(removed)))
        for entry in added:
            print("  ➕ %s" % entry["config"])
        for entry in removed:
            print("  ➖ %s (%s)" % (entry["config"], entry["detail"]))
        for entry in waived:
            print("  ⚠️ waived: %s %s %s -> %s (%s)" % (
                entry["config"], entry["column"], entry["from"], entry["to"],
                entry["reason"]))
        for entry in regressions:
            print("  ✖️ REGRESSION: %s %s %s -> %s (%s)" % (
                entry["config"], entry["column"], entry["from"], entry["to"],
                entry["reason"]))
        if not regressions and not removed and not added:
            print("✅ no feature-status regressions")
        elif regressions:
            print("✖️ %d unexplained regression(s)" % len(regressions))
        else:
            print("✅ no unexplained regressions (%d waived)" % len(waived))

    if errors and args.fail_on_regression:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
