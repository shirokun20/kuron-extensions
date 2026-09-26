#!/usr/bin/env python3
"""Sync kuron-extensions manifest.json from config/ files.

Run from the repo root::

    python3 scripts/refresh_manifest.py          # rewrite checksum/sizeKb/version/lastUpdated
    python3 scripts/refresh_manifest.py --check  # report only, exit 1 on issues

Rules enforced (see .agents/skills/kuron-source-config/SKILL.md):
  - one source id lives in exactly one language bucket (config/new/ is staging)
  - filename <id>-config.json matches the "source" field inside
  - file version is strict semver and is synced INTO the manifest entry
  - checksum/sizeKb/lastUpdated are generated, never hand-edited
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
BUCKETS = ("id", "en", "ja", "vi", "ch", "global")
STAGING = os.path.join("config", "new")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    check_only = "--check" in sys.argv
    errors, warnings = [], []

    try:
        manifest = load_json("manifest.json")
    except (OSError, ValueError) as e:
        print(f"ERROR: cannot read manifest.json: {e}")
        return 2
    entries = {e["id"]: e for e in manifest.get("installableSources", [])}

    # Collect config files per bucket (staging excluded from manifest).
    files = {}
    for bucket in BUCKETS:
        d = os.path.join("config", bucket)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            files[os.path.join(d, name)] = bucket

    seen_ids = {}
    synced = 0
    for path, bucket in sorted(files.items()):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            cfg = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as e:
            errors.append(f"{path}: unreadable ({e})")
            continue

        source = cfg.get("source")
        if os.path.basename(path) != f"{source}-config.json":
            errors.append(f"{path}: filename does not match \"source\"={source!r}")
        if source in seen_ids:
            errors.append(
                f"{path}: duplicate id {source!r} "
                f"(also in config/{seen_ids[source]}/) — keep one, delete the other"
            )
        seen_ids[source] = bucket

        version = str(cfg.get("version", ""))
        if not SEMVER.match(version):
            errors.append(f"{path}: version {version!r} is not MAJOR.MINOR.PATCH")
            continue

        entry = entries.get(source)
        if entry is None:
            errors.append(
                f"{path}: no installableSources entry for {source!r} "
                "(add it to manifest.json manually, then re-run)"
            )
            continue

        if entry.get("url", "") != path:
            errors.append(
                f"{path}: orphan path — {source!r} is registered as "
                f"{entry.get('url')!r} (duplicate across buckets?)"
            )
            continue

        digest = hashlib.sha256(raw).hexdigest()
        size_kb = round(len(raw) / 1024)
        if not check_only:
            if (
                entry.get("version") != version
                or entry.get("checksum") != digest
                or entry["meta"].get("sizeKb") != size_kb
            ):
                entry["version"] = version
                entry["checksum"] = digest
                entry["meta"]["sizeKb"] = size_kb
                synced += 1
        else:
            if entry.get("version") != version:
                errors.append(
                    f"{source}: version drift "
                    f"(manifest {entry.get('version')} != file {version})"
                )
            if entry.get("checksum") != digest:
                errors.append(f"{source}: checksum mismatch (file changed)")
            if entry["meta"].get("sizeKb") != size_kb:
                errors.append(
                    f"{source}: sizeKb drift "
                    f"(manifest {entry['meta'].get('sizeKb')} != {size_kb})"
                )

        if entry["meta"].get("language") != bucket:
            errors.append(
                f"{source}: meta.language {entry['meta'].get('language')!r} "
                f"!= bucket {bucket!r}"
            )
        icon = entry["meta"].get("iconUrl")
        if icon and not os.path.exists(icon):
            errors.append(f"{source}: missing icon {icon!r}")

    on_disk = {p for p in files}
    for entry in manifest.get("installableSources", []):
        if entry.get("url") not in on_disk:
            errors.append(
                f"{entry.get('id')}: manifest url {entry.get('url')!r} "
                "has no file on disk"
            )

    staged = []
    if os.path.isdir(STAGING):
        staged = sorted(
            f for f in os.listdir(STAGING) if f.endswith(".json")
        )
    for name in staged:
        warnings.append(f"config/new/{name}: staging only, not in manifest")

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if check_only:
        if errors:
            print(f"{len(errors)} error(s), {len(warnings)} warning(s).")
            return 1
        print(f"OK: {len(entries)} entries consistent, {len(warnings)} warning(s).")
        return 0

    manifest["lastUpdated"] = datetime.now(
        timezone(timedelta(hours=7))
    ).isoformat()
    with open("manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
        f.write("\n")
    print(f"OK: manifest.json refreshed ({synced} entries synced).")
    if errors:
        print(f"{len(errors)} error(s) still need manual fixing (see above).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
