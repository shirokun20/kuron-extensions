#!/usr/bin/env python3
"""Terapkan laporan verifikasi live ke baris matrix di SUPPORT-CONFIG.md.

Laporan masuk sebagai JSON supaya integrator bisa memverifikasi ulang setiap
perubahan sel sebelum menulisnya, dan baris matrix yang panjang tetap aman
disentuh lewat satu tempat (bukan edit manual per baris).

Input:
```json
{
  "date": "2026-09-30",
  "configs": [
    {
      "source": "komiktap",
      "path": "config/id/komiktap-config.json",
      "columns": {
        "tag": {"emoji": "✖️", "route": "tak ada route tag", "note": "/tag/action/ 404 di situs"},
        "author": {"emoji": "✖️", "route": "-"}
      },
      "note": "ringkasan live untuk kolom Note",
      "defects": [
        {"column": "Tag + Pag", "from": "⚠️", "to": "✖️", "root": "...", "evidence": "...", "fix": "..."}
      ]
    }
  ]
}
```

Kolom yang tidak disebut tidak disentuh (verifikasi sebagian diperbolehkan).

python3 scripts/apply_live_report.py --report report.json [--dry-run]
python3 scripts/apply_live_report.py --report report.json --defects-out defects.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORT_MD = REPO_ROOT / "SUPPORT-CONFIG.md"

# Urutan kolom tabel Support Matrix (setelah No dan Config / Path).
COLUMNS = [
    "home",
    "home_pag",
    "search",
    "search_pag",
    "detail",
    "reader",
    "genre",
    "tag",
    "author",
    "artist",
]

VALID_EMOJI = ("✅", "⚠️", "✖️", "🚫", "⚠")


def normalize_emoji(value: str) -> str:
    """Samakan bentuk emoji: buang variation selector dulu, lalu pasang yang baku."""
    base = value.replace("️", "").replace("🚫", "🚫").replace("✖", "✖").replace("⚠", "⚠")
    return base + "️" if base in ("⚠", "✖") else base


def cell_text(emoji: str, route: str) -> str:
    """Sel matrix = ` <emoji> `route` `. Backtick di dalam route dibuang supaya
    format markdown sel tidak rusak."""
    emoji = normalize_emoji(emoji)
    route = (route or "").replace("`", "").strip()
    if route in ("", "-"):
        return f" {emoji} "
    return f" {emoji} `{route}` "


def parse_row(line: str) -> list[str] | None:
    """Pecah baris matrix jadi sel: No, path, 10 kolom, Note.

    Baris ditulis sebagai `| a | b | … | note |`, jadi sel terakhir bisa
    menyatu dengan pipa penutup bila nota pernah berisi spasi-pipi. Karena itu
    baris dinormalkan dulu (pipa pembuka/penutup dilepas) lalu digabung kembali
    dengan format yang sama — hasilnya byte-identik untuk baris yang utuh.
    """
    if not line.startswith("| ") or "`config/" not in line:
        return None
    cells = [c for c in line.strip().strip("|").split(" | ")]
    if len(cells) == 14 and not cells[-1].strip():
        cells.pop()  # baris yang pernah acquiring satu pipa berlebih
    return cells if len(cells) == 13 else None


def join_row(cells: list[str]) -> str:
    return "| " + " | ".join(c.strip() for c in cells) + " |"


def apply_report(report: dict, dry_run: bool) -> tuple[int, list[dict]]:
    lines = SUPPORT_MD.read_text(encoding="utf-8").split("\n")
    date = report.get("date", "live")
    changed, defects_out, problems = 0, [], []

    for entry in report["configs"]:
        source = entry["source"]
        path = entry["path"]
        index = next(
            (
                n
                for n, line in enumerate(lines)
                if line.startswith("| ") and f"`{path}`" in line and parse_row(line)
            ),
            None,
        )
        if index is None:
            problems.append(f"{source}: baris matrix untuk `{path}` tidak ditemukan")
            continue

        cells = parse_row(lines[index])
        for column, value in entry.get("columns", {}).items():
            if column not in COLUMNS:
                problems.append(f"{source}: kolom tidak dikenal `{column}`")
                continue
            position = COLUMNS.index(column) + 2
            before = cells[position]
            after = cell_text(value["emoji"], value.get("route", ""))
            cells[position] = after
            if before.strip() != after.strip():
                changed += 1
                print(f"  {source} · {column}: {before.strip()} -> {after.strip()}")

        note = (entry.get("note") or "").strip()
        if note:
            live_note = f"**live {date}**: {note}"
            if live_note not in cells[12]:
                cells[12] = cells[12].rstrip(" |") + "<br>" + live_note
                changed += 1

        lines[index] = join_row(cells)
        defects_out.extend({**d, "source": source, "path": path} for d in entry.get("defects", []))

    if changed and not dry_run:
        SUPPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    return changed, defects_out, problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", required=True, help="path JSON laporan verifikasi live")
    parser.add_argument("--dry-run", action="store_true", help="tampilkan perubahan tanpa menulis")
    parser.add_argument("--defects-out", help="tulis defect ke file JSON untuk pengajuan issue")
    args = parser.parse_args(argv)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    changed, defects, problems = apply_report(report, args.dry_run)
    for problem in problems:
        print(f"✗ {problem}")

    if args.defects_out:
        Path(args.defects_out).write_text(
            json.dumps(defects, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"defect ditulis: {args.defects_out} ({len(defects)} item)")

    print(f"{'DRY-RUN' if args.dry_run else 'diterapkan'}: {changed} sel berubah, {len(defects)} defect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
