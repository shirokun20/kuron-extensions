# CI & canary tooling

Tooling added by **#62** (JSON Schema + CI PR gate + canary probe) and **#64**
(automated canary health check + matrix regression guard). Everything is Python
stdlib; CI installs one dependency (`jsonschema`) for the reference schema
engine.

## Commands

```bash
# 1. JSON Schema (Draft 2020-12) for every config
python3 scripts/validate_schema.py                 # 120/120 expected
python3 scripts/validate_schema.py --json --verbose
python3 scripts/validate_schema.py config/en/milftoon-config.json

# 2. Local CI gate (same steps as .github/workflows/validate.yml)
python3 scripts/test_canary_tooling.py
python3 scripts/build_from_presets.py --check
python3 scripts/check_conformance.py
python3 scripts/refresh_manifest.py --check
python3 scripts/generate_support_table.py --check

# 3. Feature-matrix regression guard (#64)
python3 scripts/matrix_diff.py --base origin/main
python3 scripts/matrix_diff.py --base origin/main --fail-on-regression
python3 scripts/matrix_diff.py --base HEAD~1 --json

# 4. Canary smoke runner: Home / Search / Detail / Reader (#64)
python3 scripts/canary_smoke_runner.py --help
python3 scripts/canary_smoke_runner.py --suite refactor-24 --dry-run
python3 scripts/canary_smoke_runner.py --suite refactor-24 --sample 5 --delay 2
python3 scripts/canary_smoke_runner.py --bucket en --sample 10 --output report.json
python3 scripts/canary_smoke_runner.py --source hentairox --routes detail,reader
#   snapshot mode (record once, replay offline, CI-friendly)
python3 scripts/canary_smoke_runner.py --suite core-smoke \
    --save-snapshots .canary/snapshots
python3 scripts/canary_smoke_runner.py --from-snapshots .canary/snapshots

# 5. Scheduled drift probe: HTTP status + Home/Search item counts (#62)
python3 scripts/canary_probe.py --help
python3 scripts/canary_probe.py --dry-run --sample 10
python3 scripts/canary_probe.py --sample 30 --seed "$(date +%V)" \
    --baseline .canary/baseline.json --update-baseline \
    --output .canary/report.json --markdown canary-report.md --fail-on-drift
```

## Workflows

| File | Trigger | What it does |
|---|---|---|
| `.github/workflows/validate.yml` | `pull_request` → `main`, `push` → `main`, manual | Runs tooling self-tests, JSON Schema validation, preset drift, conformance, manifest sync, support-matrix freshness, canary `--dry-run`, and `matrix_diff.py --fail-on-regression` against the PR base (falls back to `origin/main`). |
| `.github/workflows/canary.yml` | cron `17 3 * * 1,4` (Mon/Thu 03:17 UTC) + manual | Probes a rotating sample (`--sample 30`, weekly seed), compares with the cached `.canary/baseline.json`, publishes the Markdown report to the job summary + artifact, and opens/comments on one `[canary]` issue when critical drift is found. |

## What counts as a failure

**JSON Schema** – required identity fields (`source`, `version`, `baseUrl`,
`enabled`, `defaultLanguage`, `idType`, `ui`, `features`) plus typed structural
blocks (`scraper.urlPatterns`, `secondaryFetch`, `api`, `network`,
`navigation`). `schemaVersion` accepts `"2.0"`, `2`, or absence. Closed maps
(`features`, `fieldSpec`, `secondaryFetch`) reject misspelt/retired keys;
unknown future blocks at the root are allowed so the schema does not block new
engine surfaces. Cross-repo coordination: `schemaVersion` accepts the v2 and
v3 families (`2`, `"2.0"`, `3`, `"3.0"`, `2.x`/`3.x`, or absent on legacy
configs) and `ui` formally accepts `iconPath`, `icon` (string or object) and
`iconUrl` for built-in source configs such as nhasixapp's `nhentai`.

**matrix_diff** – a status drop (`✅` → `⚠️`/`✖️`/`🚫`) fails unless the head
row's Note changed and carries an explanation marker (`live`, `issue #`,
`regress`, `turun`, `degrad`, `verified`, …) or the manifest version for that
source was bumped between base and head. Use `--allow <source>` for an explicit
waiver, `--strict` to also fail on dropped rows whose config file still exists.

**canary_smoke_runner** – `home`/`search` need HTTP 200 + at least one parsed
item (HTML `list.container` or REST `api.list.items`); `detail` needs HTTP 200 +
non-empty title (and chapters when `features.chapters`); `reader` needs HTTP 200
+ an image URL that itself answers 200/206 with an image content type (403 →
hotlink protection). Routes the adapter handles with AJAX/JS (e.g. Madara
`chapters.mode: ajaxHtml`, POST search forms, chapter-only reader routes) are
reported as `skip` with the reason, never as a false failure. Exit 1 on any
real failure; `--allow-failures` forces 0 for report-only runs.

**canary_probe** – drift severities:

| Severity | Rule |
|---|---|
| `critical` | route was 2xx in the baseline and is now non-2xx/unreachable, or item count is 0, or count drops ≥90% |
| `warning` | item count drops more than `--drop-threshold` (default 50%) |
| `info` | count increase / recovery |
| `skip` | `maintenance: true`, no route, or dry-run |

`--update-baseline` only records 2xx probes with ≥1 item, so a transient outage
does not overwrite the last good baseline. The baseline lives in `.canary/`
(gitignored; CI caches it), so the repo stays clean.

## Canary suites

`scripts/canary_suites.json`:

* **`refactor-24`** – the 24 sources rebuilt in sweep `0aafb98` (milftoon REST
  migration, hentairox/hentaienvy redesign, bypass-shield additions). This is
  the suite required by #64.
* **`core-smoke`** – a small mixed set (HTML scraper, REST API, gallery reader,
  bypass source) for a quick local sanity run.
