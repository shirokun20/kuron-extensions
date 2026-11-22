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
