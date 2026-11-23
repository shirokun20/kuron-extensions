# Config conventions: title text + content ID type

Two repo-wide conventions are enforced by `scripts/check_conformance.py`
(rules **R6** and **R7**). Both are cross-repo contracts with the Kuron
consumer app (`shirokun20/nhasixapp`); keep them in sync with its config
parser before removing or renaming either one.

## `transform: "cleanText"` on every title field

Every field object named `title` that lives under a `fields` mapping —
list cards, detail fields, chapter lists, API `list`/`detail` fields —
declares:

```jsonc
"title": {
  "transform": "cleanText",
  "selector": ".entry-title, h1"
}
```

Engine semantics: `value.trim().replaceAll(RegExp(r"\s+"), " ")` — collapse
internal newlines/tabs/repeated spaces to one space, then trim. This keeps
Detail titles from breaking across lines and lets the Reader AppBar clip a
single line cleanly.

Notes:

- `searchForm.params.title` is a **search parameter**, not a content title;
  it must NOT get this transform. R6 only inspects fields under a `fields`
  mapping.
- The transform is additive: `selector`, `attribute`, `regex`, `multi`, etc.
  are untouched. If a title also uses `regex`, cleanText is applied to the
  regex result.
- Raw IDs (e.g. `transform: "slug"`) are never given cleanText.

## Top-level `idType`: `"real" | "slug"`

Every config declares one of:

| Value | Meaning | Examples |
|---|---|---|
| `real` | The content ID is a machine-generated token: UUID, numeric, or numeric+hex. | `mangadex` (UUID), `hentainexus`/`ehentai`/`hitomi` (numeric regex), `schale-network`/`hdoujin` (`\d+/[a-f0-9]+`), `rawdevart`, `spyfakku`, `asmhentai`, `hentai3`, `hentaienvy`, `hentaiera`, `hentaizap`, `xiutaku`, `mangapill` |
| `slug` | The content ID is a human-readable URL path segment (`transform: "slug"` or a `([^/]+)`-style `contentIdPattern`). | `komikindo`, `komikcast`, `crotpedia`, and the other 105 sources |

The app reads `idType` on the Detail screen to decide whether to show the
raw ID (`real`) or a short, elegant display ID (`slug`) instead of a long
URL slug. **Raw `id` values and routing are never changed** — `idType` is
metadata only.

Classification rule of thumb (mirrors the app's heuristic fallback for
configs without the field):

1. content-id field has a numeric/opaque-token `regex`, or
2. `contentIdPattern` capture is purely numeric/hex/UUID, or
3. API id selector is `$.id`/`..._id` with no slug-shaped `contentIdPattern`
   → `real`; everything else → `slug`.

## Validation

```sh
python3 scripts/check_conformance.py          # R1..R7 gate; exit 1 on any violation
python3 scripts/refresh_manifest.py --check   # version/checksum/size sync
python3 scripts/generate_support_table.py --check
```

A PATCH version bump is required on every config change; regenerate the
manifest with `python3 scripts/refresh_manifest.py` (never edit checksums by
hand).

## Pattern Bank (`presets/` + `scripts/build_from_presets.py`)

Family base presets live in `presets/` (authoring-time only; built configs
stay self-contained and the app ignores the `extends` key). A config opts in
with `"extends": "<preset-name>"` right after `"source"`. Merge rule is
**local-wins**: `dict`+`dict` recurses, otherwise the file's own value wins;
rebuilds only *add* missing preset paths, never change values.
For `secondaryFetch` the preset must materialize the per-surface blocks the
nhasixapp adapter actually reads (`urlPatterns.<name>.list`,
`selectors.detail`, `selectors.reader` — canonical key set in
`SecondaryFetchSpec.tryParse`); the retired `listKey`/`detailKey`/`readerKey`/
`surfaces` shape is rejected by the build in both modes.

```sh
python3 scripts/build_from_presets.py          # backfill missing preset paths
python3 scripts/build_from_presets.py --check  # exit 1 on drift
```

Re-running the build is byte-identical. How to add a preset: survey the
family (cluster by identical `scraper.selectors` blocks), verify every preset
path already exists in every member, create `presets/<name>.json` with
`{"preset", "description", "defaults": {"scraper": {...}}}`, add `extends` to
members, then run build + `--check` + the full gate above (PATCH bump +
manifest refresh required). Full workflow: `presets/README.md`.
