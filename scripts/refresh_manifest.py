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
  - meta.maintenance/meta.maintenanceMessage mirror the config file
    (Refs #60: a stale manual override in manifest.json is drift, not truth)
  - meta.bypassStatus/meta.requiresBypass are derived from
    network.requiresBypass/network.cloudflare (Refs #63); meta keys are
    additive so the minimumAppVersion contract with kuron-mobile is kept
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
BUCKETS = ("id", "en", "ja", "vi", "ch", "global")
# Refs #63: bypass states exported to every manifest meta entry.
BYPASS_NONE = "none"
BYPASS_LAZY = "lazy"
BYPASS_TURNSTILE = "turnstile"


def desired_maintenance(cfg, source):
    """(maintenance, maintenanceMessage|None) the manifest must mirror.

    Refs #60: the config file is the source of truth. A manifest entry
    carrying maintenance=true while the config says otherwise is drift.
    """
    if cfg.get("maintenance") is True:
        msg = cfg.get("maintenanceMessage") or (
            "%s sedang dalam perbaikan, mohon tunggu" % source
        )
        return True, msg
    return False, None


def desired_bypass(cfg):
    """(bypassStatus, requiresBypass) derived from network/cloudflare blocks.

    Refs #63: `requiresBypass: true` (or cloudflare.bypassEnabled) means the
    source needs a lazy bypass before install; an explicit turnstile block
    upgrades that to `turnstile`. Everything else is `none`.
    """
    net = cfg.get("network") or {}
    cf = net.get("cloudflare") or {}
    turnstile = net.get("turnstile") or cf.get("turnstile") or {}
    if isinstance(turnstile, dict) and turnstile.get("enabled") is True:
        return BYPASS_TURNSTILE, True
    if net.get("requiresBypass") is True or cf.get("bypassEnabled") is True:
        return BYPASS_LAZY, True
    return BYPASS_NONE, False
STAGING = os.path.join("config", "new")
BUCKET_ORDER = {b: i for i, b in enumerate(BUCKETS)}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def entry_key(e):
    url = e.get("url", "")
    parts = url.split("/")
    bucket = parts[1] if len(parts) > 1 else ""
    return (BUCKET_ORDER.get(bucket, 99), e.get("id", ""))


def main():
    check_only = "--check" in sys.argv
    errors, warnings = [], []

    try:
        manifest = load_json("manifest.json")
    except (OSError, ValueError) as e:
        print(f"ERROR: cannot read manifest.json: {e}")
        return 2
    entries = {}
    for e in manifest.get("installableSources", []):
        if e.get("id") in entries:
            errors.append(f"manifest: duplicate id {e.get('id')!r} — delete the stale entry, then re-run")
        entries[e["id"]] = e

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
        want_maint, want_msg = desired_maintenance(cfg, source)
        want_bypass, want_requires = desired_bypass(cfg)
        meta = entry["meta"]
        maint_ok = (
            (meta.get("maintenance") is True and
             meta.get("maintenanceMessage") == want_msg)
            if want_maint else
            ("maintenance" not in meta and "maintenanceMessage" not in meta)
        )
        bypass_ok = (
            meta.get("bypassStatus") == want_bypass
            and meta.get("requiresBypass") is want_requires
        )
        if not check_only:
            if (
                entry.get("version") != version
                or entry.get("checksum") != digest
                or meta.get("sizeKb") != size_kb
                or not maint_ok
                or not bypass_ok
            ):
                entry["version"] = version
                entry["checksum"] = digest
                meta["sizeKb"] = size_kb
                if want_maint:
                    meta["maintenance"] = True
                    meta["maintenanceMessage"] = want_msg
                else:
                    meta.pop("maintenance", None)
                    meta.pop("maintenanceMessage", None)
                meta["bypassStatus"] = want_bypass
                meta["requiresBypass"] = want_requires
                synced += 1
        else:
            if entry.get("version") != version:
                errors.append(
                    f"{source}: version drift "
                    f"(manifest {entry.get('version')} != file {version})"
                )
            if entry.get("checksum") != digest:
                errors.append(f"{source}: checksum mismatch (file changed)")
            if meta.get("sizeKb") != size_kb:
                errors.append(
                    f"{source}: sizeKb drift "
                    f"(manifest {meta.get('sizeKb')} != {size_kb})"
                )
            if not maint_ok:
                errors.append(
                    f"{source}: maintenance drift "
                    f"(manifest {meta.get('maintenance')} != file "
                    f"{cfg.get('maintenance', False)})"
                )
            if not bypass_ok:
                errors.append(
                    f"{source}: bypass drift "
                    f"(manifest {meta.get('bypassStatus')}/"
                    f"{meta.get('requiresBypass')} != file "
                    f"{want_bypass}/{want_requires})"
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
    if check_only:
        order = [entry_key(e) for e in manifest.get("installableSources", [])]
        if order != sorted(order):
            errors.append("installableSources not sorted by (bucket, id) — run without --check")

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

    manifest["installableSources"] = sorted(
        manifest.get("installableSources", []), key=entry_key
    )
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
