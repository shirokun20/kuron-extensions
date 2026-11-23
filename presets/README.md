# Pattern Bank (`presets/`)

Family base presets for the `config/*/*-config.json` sources. A preset captures
the DOM/API contract shared by one website family so per-site configs only
carry what is actually site-specific. Preset files are **authoring-time only**:
built configs stay fully self-contained (the nhasixapp runtime needs zero
extra HTTP calls and ignores the unknown `extends` key, as it does any other
unknown key).

## Families

| Preset | Family signature | Members |
|---|---|---|
| `madara-wpmanga` | Madara / WP-Manga theme: `.page-item-detail` cards, `li.wp-manga-chapter` ajax chapter list, `.reading-content img.wp-manga-chapter-img` reader (`chapterDataScript`), `/manga-genre/{tag}/` routes | `allporncomicio`, `apcomicsorg`, `bunmangacom`, `cocomicco` (+23 configs share the selector block) |
| `themesia` | Themesia / Tsuki-reader theme: `.listupd .bsx` cards, `.tt` titles, `img.ts-post-image` covers, `#chapterlist li` chapters, `#readerarea img` + `ts_reader.run({...})` reader, `/genres/{tag}/` routes | `arenascancom`, `scythescanscom`, `madarascans` (name is Madara, DOM is Themesia) |
| `nextjs-adframe` | Next.js App Router + ad-frame JSON: pages are shells with a one-time `payloadKey`; data comes from `GET {origin}/ad-frame/data?k={key}` (CJK scheme). The adapter reads `secondaryFetch` ONLY at `urlPatterns.<name>.list` (search/genreSearch/tagSearch inherit home's list block), `selectors.detail`, and `selectors.reader` — the preset materializes all three (lists: `payloadPath`+`itemsPath`=`items`, `decodeKey` NicoManga, `itemFields` ur→id/n→title/c→coverUrl, `pagination` page/total/lastPage; detail/reader: `payloadPath`=`chaotic_payload`, `decodeKey` NicoMangaX2, reader adds `imagesPath`). Retired `listKey`/`detailKey`/`readerKey`/`surfaces` shapes are rejected by the build. DOM selectors are static/SEO fallbacks | `nicomanga` |

Not yet banked (intentionally): the `api` + `adapterConfig` family (9 configs,
all with different endpoint shapes — no clean core) and one-off sites
(`rawkuma` admin-ajax POST, `nicomanga`-style singletons outside the bank).

## How `extends` merges

- A config opts in with a top-level `"extends": "<preset-name>"` key placed
  right after `"source"`. The preset's family defaults live under its
  `"defaults"` key (currently `scraper`-subtree only).
- Merge rule (implemented by `scripts/build_from_presets.py`): **local wins**.
  `dict` + `dict` merges recurse; for anything else the config file's own
  value wins. Files may add extra keys and override any preset value.
- Consequence: `--check` fails **only** when an extended file is *missing*
  preset paths (the preset evolved and the file was not rebuilt) or names an
  unknown preset. Value drift is impossible by construction — rebuilds only
  *add* missing keys, never change existing values, so nothing regresses.

## Build / check commands (run from repo root)

```sh
python3 scripts/build_from_presets.py          # backfill missing preset paths
python3 scripts/build_from_presets.py --check  # report only, exit 1 on drift
```

Write mode preserves each file's key order (new keys appended at the end of
their enclosing object) and dumps canonical JSON (`indent=2`,
`ensure_ascii=False`, trailing newline), so re-running the build is
byte-identical and adding `extends` to a conformant file diffs as one line.

## How to add a preset

1. Survey the family: cluster `config/*/*-config.json` by identical
   `scraper.selectors` blocks, then by container/route shapes. A preset needs
   a path-intersection shared by every intended member.
2. Verify coverage before writing: every preset path must already exist in
   every member file (values may differ — local wins).
3. Create `presets/<name>.json`:
   `{"preset": "<name>", "description": "...", "defaults": {"scraper": {...}}}`.
4. Add `"extends": "<name>"` after `"source"` in each member file.
5. Run the build, then `--check`, then the full gate:
   `refresh_manifest.py`, `check_conformance.py`,
   `generate_support_table.py --check`. A PATCH bump + manifest refresh is
   required (the `extends` line changes checksums).
