#!/usr/bin/env python3
"""Pattern Bank: merge ``presets/`` defaults into ``config/*/*-config.json``.

A config opts in with a top-level ``"extends": "<preset-name>"`` key, naming
``presets/<name>.json``. The preset carries family defaults under its
``"defaults"`` key (currently ``scraper``-subtree only).

Merge rule (deterministic, local-wins, never rewrites site values):

  expected = deep_merge(preset_defaults, config_without_extends)

``dict`` + ``dict`` merges recurse; for anything else the config's own value
wins. Files stay fully self-contained: the nhasixapp runtime needs zero extra
HTTP calls and ignores the unknown ``extends`` key (standard
lenient-JSON parsing).

Modes::

    python3 scripts/build_from_presets.py          # backfill missing preset
                                                   # paths into extended files
    python3 scripts/build_from_presets.py --check  # report only, exit 1 on
                                                   # drift (missing preset
                                                   # paths / unknown preset)

``--check`` fails only when an extended file is *missing* preset paths (the
preset evolved and the file was not rebuilt) or names an unknown preset.
Per-site overrides are always allowed: a file may add extra keys or use
different values anywhere — those are never touched.

Adapter-contract guard: the nhasixapp ``GenericScraperAdapter`` reads
``secondaryFetch`` ONLY at ``urlPatterns.<name>.list`` (search /
genreSearch / tagSearch inherit home's list block), ``selectors.detail``,
and ``selectors.reader``, and ``SecondaryFetchSpec.tryParse`` understands
only ``enabled`` / ``tokenPattern`` (alias ``tokenRegex``) / ``url`` (alias
``secondaryUrlTemplate``) / ``referer`` / ``payloadPath`` / ``decodeKey`` /
``itemsPath`` / ``imagesPath`` / ``itemFields`` / ``pagination`` /
``timeoutMs``. A ``secondaryFetch`` block carrying the retired authoring
keys ``listKey`` / ``detailKey`` / ``readerKey`` / ``surfaces`` is dead
through the real adapter, so the build reports it as an error (in both
modes) until removed.

Write mode preserves each file's key order and only appends missing preset
paths at the end of their enclosing object, then dumps canonical JSON
(``indent=2``, ``ensure_ascii=False``, trailing newline). Re-running the
build is byte-identical. Adding ``extends`` to an already-conformant file
therefore diffs as one inserted line.
"""

import copy
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_DIR = os.path.join(ROOT, "presets")
CONFIG_GLOB = os.path.join(ROOT, "config", "*", "*-config.json")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def backfill(dst, src):
    """Recursively add preset paths missing from dst (in place).

    Existing values are never changed. Returns the list of added
    slash-separated paths (empty when the file already covers the preset).
    """
    added = []

    def rec(d, s, prefix):
        for key, sval in s.items():
            path = "%s/%s" % (prefix, key)
            if key not in d:
                d[key] = copy.deepcopy(sval)
                added.append(path)
            elif isinstance(d[key], dict) and isinstance(sval, dict):
                rec(d[key], sval, path)

    rec(dst, src, "")
    return added


def missing_paths(dst, src):
    """Slash-separated preset paths absent from dst (pure check)."""
    missing = []

    def rec(d, s, prefix):
        for key, sval in s.items():
            path = "%s/%s" % (prefix, key)
            if not isinstance(d, dict) or key not in d:
                missing.append(path)
            elif isinstance(sval, dict) and isinstance(d[key], dict):
                rec(d[key], sval, path)

    rec(dst, src, "")
    return missing


# Authoring keys that no adapter read site understands. A `secondaryFetch`
# block carrying any of these is dead config (the two-step path is never
# reached) and must be replaced with the per-surface materialized shape.
LEGACY_SECONDARY_KEYS = ("listKey", "detailKey", "readerKey", "surfaces")


def legacy_secondary_blocks(node, prefix=""):
    """Yield (path, legacy_keys) for every `secondaryFetch` block holding
    retired authoring keys."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = "%s/%s" % (prefix, key)
            if key == "secondaryFetch" and isinstance(value, dict):
                bad = [k for k in value if k in LEGACY_SECONDARY_KEYS]
                if bad:
                    found.append((path, bad))
            found.extend(legacy_secondary_blocks(value, path))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(legacy_secondary_blocks(value, "%s[%d]" % (prefix, i)))
    return found


def ordered_with_extends_first(cfg):
    """File key order, but ``extends`` pinned right after ``source``."""
    out = {}
    if "source" in cfg:
        out["source"] = cfg["source"]
    if "extends" in cfg:
        out["extends"] = cfg["extends"]
    for key, value in cfg.items():
        if key not in out:
            out[key] = value
    return out


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in args

    presets = {}
    if os.path.isdir(PRESETS_DIR):
        for path in sorted(glob.glob(os.path.join(PRESETS_DIR, "*.json"))):
            name = os.path.splitext(os.path.basename(path))[0]
            try:
                presets[name] = load_json(path)
            except ValueError as exc:
                print("ERROR: presets/%s.json is not valid JSON: %s" % (name, exc))
                return 2

    files = sorted(glob.glob(CONFIG_GLOB))
    extended, ok_count, drifted, errors = 0, 0, 0, 0

    for path in files:
        rel = os.path.relpath(path, ROOT)
        try:
            cfg = load_json(path)
        except ValueError as exc:
            print("ERROR: %s is not valid JSON: %s" % (rel, exc))
            errors += 1
            continue
        preset_name = cfg.get("extends")
        if not preset_name:
            continue
        extended += 1
        preset = presets.get(preset_name)
        if preset is None:
            print("DRIFT: %s extends unknown preset %r" % (rel, preset_name))
            drifted += 1
            continue
        defaults = preset.get("defaults")
        if not isinstance(defaults, dict):
            print("ERROR: presets/%s.json has no object `defaults`" % preset_name)
            errors += 1
            continue
        body = {k: v for k, v in cfg.items() if k != "extends"}
        legacy = legacy_secondary_blocks(body)
        if legacy:
            errors += 1
            print("ERROR: %s carries non-contract secondaryFetch key(s) "
                  "the adapter never reads:" % rel)
            for path_, bad in legacy:
                print("    - %s: %s" % (path_, ", ".join(sorted(bad))))
            continue
        missing = missing_paths(body, defaults)
        if missing:
            drifted += 1
            print("DRIFT: %s is missing %d preset path(s) from %r:"
                  % (rel, len(missing), preset_name))
            for path_ in missing[:10]:
                print("    - %s" % path_)
            if len(missing) > 10:
                print("    ... and %d more" % (len(missing) - 10))
            if not check_only:
                merged = ordered_with_extends_first(cfg)
                body_out = {k: v for k, v in merged.items() if k != "extends"}
                backfill(body_out, defaults)
                merged = ordered_with_extends_first(
                    {"source": merged.get("source"), "extends": preset_name,
                     **body_out})
                text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                print("    rebuilt %s" % rel)
        else:
            ok_count += 1
            if not check_only:
                # Normalize key order/formatting so re-runs are byte-identical.
                merged = ordered_with_extends_first(cfg)
                text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
                with open(path, "r", encoding="utf-8") as f:
                    current = f.read()
                if current != text:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text)
                    print("OK (reordered): %s" % rel)
                else:
                    print("OK: %s" % rel)

    print("presets: %d file(s), %d extended, %d up to date, %d drifted, %d error(s)"
          % (len(files), extended, ok_count, drifted, errors))
    if drifted or errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
