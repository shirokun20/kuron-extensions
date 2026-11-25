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
| `madara-wpmanga` | Madara / WP-Manga theme: `.page-item-detail` cards, `li.wp-manga-chapter` ajax chapter list, `.reading-content img.wp-manga-chapter-img` reader (`chapterDataScript`), `/manga-genre/{tag}/` routes | 46 members: `allporncomicio`, `anisascansin`, `apcomicsorg`, `bunmangacom`, `cocomicco`, `gdscanscom`, `gedecomixcom`, `hentaixcomic`, `hentaixdickgirl`, `hentaixyuri`, `hm2d`, `kmansin09`, `ksgroupscans`, `lhtranslation`, `likemangain`, `lilymanga`, `manga18free`, `manga18x`, `mangacrazy`, `mangadistrict`, `mangaforfree`, `mangaforfreecom`, `mangafree`, `mangamaniacs`, `mangaowlio`, `mangareadorg`, `mangasushi`, `mangatoporg`, `mangazinorg`, `manhuanext`, `manhwaclubnet`, `manhwaden`, `manhwahubto`, `manhwanex`, `manhwareads`, `manhwatoon`, `octopusmangacom`, `orchisasia`, `paritehaber`, `readerdecadencescanscom`, `s2readcom`, `toonizycom`, `topmanhuanet`, `tritiniaorg`, `zazamangacom`, `mangaread` (id) |
| `themesia` | Themesia / Tsuki-reader theme: `.listupd .bsx` cards, `.tt` titles, `img.ts-post-image` covers, `#chapterlist li` chapters, `#readerarea img` + `ts_reader.run({...})` reader, `/genres/{tag}/` routes | 16 members: `akazascans`, `arenascancom`, `athreascanscom`, `enthunderscanscom`, `evascansnet`, `galaxymangaio`, `kingofshojo`, `lagoonscans`, `madarascans` (name is Madara, DOM is Themesia), `mihentai`, `ravenscansorg`, `scythescanscom`, `violetmangacom`, `komikdewasa`, `ngomik`, `sektedoujin` (id) |
| `nextjs-adframe` | Next.js App Router + ad-frame JSON: pages are shells with a one-time `payloadKey`; data comes from `GET {origin}/ad-frame/data?k={key}` (CJK scheme). The adapter reads `secondaryFetch` ONLY at `urlPatterns.<name>.list` (search/genreSearch/tagSearch inherit home's list block), `selectors.detail`, and `selectors.reader` — the preset materializes all three (lists: `payloadPath`+`itemsPath`=`items`, `decodeKey` NicoManga, `itemFields` ur→id/n→title/c→coverUrl, `pagination` page/total/lastPage; detail/reader: `payloadPath`=`chaotic_payload`, `decodeKey` NicoMangaX2, reader adds `imagesPath`). Retired `listKey`/`detailKey`/`readerKey`/`surfaces` shapes are rejected by the build. DOM selectors are static/SEO fallbacks | `nicomanga` |

Not yet banked (intentionally): the `api` + `adapterConfig` family (9 configs,
all with different endpoint shapes — no clean core) and one-off sites
(`rawkuma` admin-ajax POST, `nicomanga`-style singletons outside the bank).

## Explicit Exclude list (issue #63 — do NOT migrate)

15 configs look Madara/Themesia-adjacent but fail the family contract or live
verification, so they stay `extends`-free (verified 2026-11-25, polite canary
`--delay 2 --jitter 0.5 --timeout 15`, max 2 attempts per probe):

- Pre-existing excludes (13): `gakamangascom`, `rackusreadscom`, `grabberzone`,
  `hunlightcomics`, `yakshacomicscom`, `hentai4freenet`, `mangadass`,
  `manga18fx`, `manhwa18cc`, `miauscan`, `manhwaread`, `hentaikun`, `vihentai`.
- `milftoon`: rebuilt as GenericRest (`nextjs-adframe`-adjacent singleton), not a
  scraper family member.
- `culturedworkscom`: home HTTP 526 (origin down, canary 2026-11-25) — blind
  backfill of 3 themesia paths refused; re-probe when the site returns.

Live-verified migrations in this batch carry explicit local overrides where the
preset default does not match the site (local wins by construction):

- `tritiniaorg`: `selectors.reader.mode: "html"` (preset: `chapterDataScript`).
- `lagoonscans`: `search.list.pagination.next: ".pagination a.next.page-numbers"`
  (live search p1 markup confirms `a.next.page-numbers`; p2 overlap 0).
- `mangadistrict`: `searchPage.list` pinned to a copy of the local search list
  (container `.page-item-detail`, `/series/` id selectors, `.wp-pagenavi`
  pagination) — the preset `searchPage.list` container
  (`.row.c-tabs-item__content`) does not match this site's search page (live p2:
  30 cards, overlap 0, all `.page-item-detail`).
- `mangadistrict` + `mangaread`: `chapters.mode: ajaxHtml` backfill accepted —
  same theme/container as the 4 live-working Madara siblings (`hm2d`,
  `manhwaden`, `manhwanex`, `gdscanscom`); static chapter lists verified
  (20/8 chapters in snapshots, no AJAX-only markers).

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
