#!/usr/bin/env python3
"""Generate SUPPORT-CONFIG.md: static support matrix (route + status) for every config.

Status legend:
  ✅ support   - route + selector dideklarasikan lengkap
  ⚠️ partial   - jalan sebagian (total tak terbaca, tanpa route Page, dll)
  ✖️ missing   - route/selector tidak ada di config (config juga tidak mengklaim)
  🚫 claimed   - config MENYATAKAN bisa (features.X=true) tapi deklarasinya tidak ada
                 (atau live: situsnya tidak punya fitur itu sama sekali)

Sync: urutan kolom di sini WAJIB sama dengan skill `kuron-config-verify` dan
AGENTS.md. Keduanya membawa marker `<!-- matrix-columns: ... -->` yang dicek
tiap run - kalau beda, exit 1.

Usage:
  python3 scripts/generate_support_table.py          # tulis SUPPORT-CONFIG.md
  python3 scripts/generate_support_table.py --check  # exit 1 bila stale / sync putus
"""
import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "SUPPORT-CONFIG.md")
SKILL = os.path.join(ROOT, ".agents", "skills", "kuron-config-verify", "SKILL.md")
AGENTS = os.path.join(ROOT, "AGENTS.md")
BUCKET_ORDER = ["id", "en", "ja", "vi", "ch", "global"]
SKIP_BUCKETS = {"new"}
TOTAL_KEYS = ("links", "last", "current")

# (key, header tabel) - urutan INI yang dipakai di semua tempat
COLUMNS = [
    ("home", "Home"),
    ("home_pag", "Home + Pag + Total"),
    ("search", "Search"),
    ("search_pag", "Search + Pag + Total"),
    ("detail", "Detail + Chapters"),
    ("reader", "Reader (image/video)"),
    ("genre", "Genre + Pag"),
    ("tag", "Tag + Pag"),
    ("author", "Author + Pag"),
    ("artist", "Artist + Pag"),
]
CANON = "|".join(key for key, _ in COLUMNS)
COLUMNS_KEYS = tuple(key for key, _ in COLUMNS)
MARKER = "<!-- matrix-columns: %s -->" % CANON
MARKER_PREFIX = "<!-- matrix-columns:"


def load_configs():
    rows = []
    for path in sorted(glob.glob(os.path.join(ROOT, "config", "*", "*-config.json"))):
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        bucket = rel.split("/")[1]
        if bucket in SKIP_BUCKETS:
            continue
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        rows.append((bucket, cfg.get("source", ""), rel, cfg))
    rows.sort(key=lambda r: (BUCKET_ORDER.index(r[0]) if r[0] in BUCKET_ORDER else 99, r[1]))
    return rows


def read_marker(path):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith(MARKER_PREFIX):
                    return line.strip()
    except FileNotFoundError:
        return None
    return None


def check_sync():
    problems = []
    for path in (SKILL, AGENTS):
        got = read_marker(path)
        if got != MARKER:
            problems.append(
                "%s marker %r (diharapkan %r)"
                % (os.path.relpath(path, ROOT), got, MARKER)
            )
    return problems


# ---------------------------------------------------------------- helpers ----
def get_url(up, name, _seen=None):
    _seen = _seen or set()
    if not isinstance(up, dict) or name in _seen or name not in up:
        return None
    _seen = _seen | {name}
    entry = up[name]
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        if entry.get("url"):
            return entry["url"]
        if entry.get("inherits"):
            return get_url(up, entry["inherits"], _seen)
    return None


def get_list(up, name, _seen=None):
    _seen = _seen or set()
    if not isinstance(up, dict) or name in _seen or name not in up:
        return None
    _seen = _seen | {name}
    entry = up[name]
    if not isinstance(entry, dict):
        return None
    if entry.get("list"):
        return entry["list"]
    if entry.get("inherits"):
        return get_list(up, entry["inherits"], _seen)
    return None


def pagination_of(lst):
    if not isinstance(lst, dict):
        return None
    return lst.get("pagination")


def has_total(pag):
    return bool(pag) and any(k in pag for k in TOTAL_KEYS)


def api_endpoint(cfg, *names):
    eps = (cfg.get("api") or {}).get("endpoints") or {}
    for n in names:
        if n in eps:
            return eps[n]
    return None


def _api_path(ep):
    if isinstance(ep, str):
        return ep
    if isinstance(ep, dict):
        path = ep.get("path", "")
        params = ep.get("params") or {}
        if params:
            qs = "&".join("%s=%s" % (k, v) for k, v in params.items())
            sep = "?" if "?" not in path else "&"
            path = "%s%s%s" % (path, sep, qs)
        return path
    return None


def _api_has_paging(pth):
    return any(t in (pth or "") for t in ("{page}", "{offset}", "page=", "offset="))


def route(url, limit=56):
    """Rute pendek dalam code span, aman untuk sel tabel."""
    if not url:
        return ""
    s = str(url).replace("|", "\\|").replace("`", "")
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return "`%s`" % s


def esc(text):
    return str(text).replace("|", "\\|")


LIVE_MARK = "live"
_KEY_RE = re.compile(r"`([^`]*-config\.json)`")


def _row_key(line):
    m = _KEY_RE.search(line)
    return m.group(1) if m else None


def live_keys_of(text):
    """Key config (`...-config.json`) yang barisnya sudah ditandai live."""
    keys = set()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and LIVE_MARK in s.lower():
            key = _row_key(s)
            if key:
                keys.add(key)
    return keys


def normalize_live(text, live_keys):
    """Buang baris milik config yang sudah diverifikasi live.

    Matrix hasil verifikasi live diedit manual (lihat AGENTS.md rule 8), jadi
    `--check` tidak boleh menandainya stale. Dua sisi (generated vs file) dibuang
    berdasarkan `live_keys` yang sama, lalu baris kosong diabaikan.
    """
    kept = []
    parent_key = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("| "):
            key = _row_key(s)
            if key and key in live_keys:
                continue
            if LIVE_MARK in s.lower():
                continue
            parent_key = None
        elif s.startswith("- **"):
            key = _row_key(s)
            if key:
                parent_key = key
                if key in live_keys:
                    continue
            if LIVE_MARK in s.lower():
                continue
        elif s.startswith("- ") and line[:1] in (" ", "\t"):
            if parent_key and parent_key in live_keys:
                continue
        elif LIVE_MARK in s.lower():
            continue
        kept.append(line)
    return "\n".join(kept)


# ---------------------------------------------------------------- analyze ----
def analyze(cfg):
    issues = []
    scraper = cfg.get("scraper") or {}
    up = scraper.get("urlPatterns") or {}
    sel = scraper.get("selectors") or {}
    feats = cfg.get("features") or {}
    api = cfg.get("api") or {}
    is_api = bool(api.get("enabled"))

    # --- Home -------------------------------------------------------------
    home_url = get_url(up, "home")
    if home_url:
        home = "✅ " + route(home_url)
    elif is_api:
        ep = _api_path(api_endpoint(cfg, "allGalleries", "browse"))
        home = "✅ " + route(ep) + " (API)" if ep else "✖️ tanpa endpoint browse"
        if not ep:
            issues.append("home: config API tanpa endpoint allGalleries/browse")
    else:
        home = "✖️ route `home` tidak ada"
        issues.append("home: urlPatterns.home tidak didefinisikan")

    # --- Home + pagination + total ---------------------------------------
    if is_api:
        pth = _api_path(api_endpoint(cfg, "allGalleries", "browse")) or ""
        home_pag = _api_pag_cell(pth, "home", issues)
    else:
        home_pag = _paged_cell(up, "home", "homePage")
        _collect_paged_issue(issues, up, "home", "homePage", home_pag)

    # --- Search -----------------------------------------------------------
    search_url = get_url(up, "search")
    if search_url:
        search = "✅ " + route(search_url)
    elif is_api:
        ep = _api_path(api_endpoint(cfg, "search"))
        if ep:
            search = "✅ " + route(ep) + " (API)"
        elif api_endpoint(cfg, "browse"):
            search = "⚠️ search via browse"
            issues.append("search: endpoint `search` tidak ada - dialihkan ke browse")
        else:
            search = "✖️ tanpa endpoint search"
            issues.append("search: config API tanpa endpoint search")
    else:
        search = "✖️ route `search` tidak ada"
        issues.append("search: urlPatterns.search tidak didefinisikan")

    # --- Search + pagination + total -------------------------------------
    if is_api:
        pth = _api_path(api_endpoint(cfg, "search") or api_endpoint(cfg, "browse")) or ""
        search_pag = _api_pag_cell(pth, "search", issues) if pth else "✖️"
    elif not search_url:
        search_pag = "✖️ (ikut search ✖️)"
    else:
        search_pag = _paged_cell(up, "search", "searchPage")
        _collect_paged_issue(issues, up, "search", "searchPage", search_pag)

    # --- Detail + chapters ------------------------------------------------
    detail_url = get_url(up, "detail")
    chapter_url = get_url(up, "chapter")
    chapters_sel = (sel.get("detail") or {}).get("chapters")
    if not detail_url and is_api:
        detail_url = _api_path(api_endpoint(cfg, "detail"))
    # sumber chapter: selector HTML, endpoint API chapters, atau api.detail.chapters
    api_ch = api_endpoint(cfg, "chapters") or _api_detail_chapters(api)
    has_chapters = bool(chapters_sel or api_ch)
    if not detail_url and is_api and feats.get("detail"):
        # detail di-resolve adapter (contentIdPattern); chapter tetap bisa diambil
        if has_chapters:
            detail = "✅ listed (API, detail via adapter contentIdPattern)"
        else:
            detail = "⚠️ single (API tanpa endpoint detail - di-resolve adapter via contentIdPattern)"
        issues.append("detail: API tanpa endpoint detail - di-resolve adapter via contentIdPattern")
    elif not detail_url:
        detail = "✖️ route `detail` tidak ada"
        issues.append("detail: urlPatterns.detail tidak didefinisikan")
    elif has_chapters:
        detail = "✅ listed " + route(detail_url)
    elif feats.get("chapters"):
        detail = "⚠️ single " + route(detail_url)
        issues.append("detail: features.chapters=true tapi selectors.detail.chapters hilang")
    else:
        detail = "✅ single " + route(detail_url)
        if not chapter_url and not is_api:
            issues.append("detail: route `chapter` tidak ada - reader hanya via detail")

    # --- Reader (image/video) --------------------------------------------
    reader = _reader_cell(cfg, sel, api)
    if reader.startswith("✖️"):
        issues.append(reader[2:].strip())

    # --- Genre / Tag / Author / Artist + pagination -----------------------
    facets = _facet_cells(cfg, up, is_api, search, search_pag, issues)

    if cfg.get("maintenance") or cfg.get("maintenanceMessage"):
        issues.append("maintenance: source lagi dalam perbaikan")

    out = {
        "home": home,
        "home_pag": home_pag,
        "search": search,
        "search_pag": search_pag,
        "detail": detail,
        "reader": reader,
    }
    out.update(facets)
    _mark_declared(cfg, out, issues)
    out["issues"] = issues
    return out


# features.<k> = true  ->  kolom yang dicek deklarasinya
DECLARED_MAP = [
    ("home", ["home"]),
    ("search", ["search"]),
    ("detail", ["detail"]),
    ("reader", ["reader"]),
    ("chapters", ["detail"]),
    ("contentByTag", ["genre", "tag"]),
]
EMOJI_HEAD = re.compile(r"^[\u2705\u26a0\u2716\u2715\u26d4\ufe0f]?\s*")


def _swap_emoji(cell, new):
    """Ganti emoji di depan sel dengan `new` (emoji = 2 codepoint: X + FE0F)."""
    return new + EMOJI_HEAD.sub("", cell)


def _mark_declared(cfg, out, issues):
    """Tandai 🚫 : config menyatakan fitur bisa (features.X=true) tapi tak ada deklarasinya."""
    feats = cfg.get("features") or {}
    for feat, keys in DECLARED_MAP:
        if feats.get(feat) is not True:
            continue
        for key in keys:
            if key not in COLUMNS_KEYS:
                continue
            cell = out.get(key) or ""
            if key == "detail" and feat == "chapters":
                if not cell.startswith("⚠️ single"):
                    continue
                out[key] = "🚫 chapters hilang (route detail ada)"
                why = "selectors.detail.chapters hilang"
            elif cell.startswith("✖️"):
                out[key] = _swap_emoji(cell, "🚫")
                why = cell.strip()[2:].strip() or "route/selector tidak ada"
            else:
                continue
            # buang issue lama untuk kolom ini (prefix "<kolom>: "), lalu catat versi 🚫
            issues[:] = [i for i in issues
                         if not (i.startswith(key + ": ") and not i.startswith(key + "+"))]
            msg = "%s: features.%s=true tapi %s" % (key, feat, why)
            if msg not in issues:
                issues.append(msg)
    # search sendiri 🚫 -> search_pag yang mengikutinya juga 🚫
    if (out.get("search") or "").startswith("🚫"):
        cell = out.get("search_pag") or ""
        if cell.startswith("✖️"):
            out["search_pag"] = _swap_emoji(cell, "🚫")


def _api_detail_chapters(api):
    """Config API bisa ambil chapter lewat `api.detail.chapters` (bukan endpoints.chapters)."""
    if not isinstance(api, dict):
        return None
    ch = (api.get("detail") or {}).get("chapters")
    return ch if isinstance(ch, dict) and ch else None


def _api_pag_cell(pth, label, issues):
    if not pth:
        return "✖️"
    if _api_has_paging(pth):
        return "✅ " + route(pth) + " (API+total)"
    issues.append("%s+pagination: endpoint API tanpa param page/offset" % label)
    return "⚠️ " + route(pth) + " (API tanpa pag)"


def _paged_cell(up, base, page_name):
    base_url = get_url(up, base)
    if not base_url:
        return "✖️"
    pag = pagination_of(get_list(up, base)) or pagination_of(get_list(up, page_name))
    page_url = get_url(up, page_name)
    inline = "{page}" in base_url
    if not pag:
        if inline:
            return "⚠️ " + route(base_url) + " (page inline, tanpa next)"
        return "✖️ tanpa pagination"
    if not page_url and not inline:
        return "⚠️ pagination `next` ada, route page hilang"
    target = page_url or base_url
    if has_total(pag):
        return "✅ " + route(target)
    return "⚠️ " + route(target) + " (next-only)"


def _collect_paged_issue(issues, up, base, page_name, cell):
    if cell.startswith("✅"):
        return
    base_url = get_url(up, base)
    pag = pagination_of(get_list(up, base)) or pagination_of(get_list(up, page_name))
    page_url = get_url(up, page_name)
    label = "home" if base == "home" else "search"
    if not base_url:
        msg = "route `%s` tidak ada" % base
    elif not pag:
        msg = (
            "url sudah menampung {page} tapi tanpa selector pagination"
            if "{page}" in base_url
            else "list tanpa blok pagination"
        )
    elif not page_url and "{page}" not in base_url:
        msg = "pagination ada tapi route `%s` hilang" % page_name
    else:
        msg = "pagination hanya punya `next` - total halaman tidak terbaca"
    kind = "pagination" if cell.startswith("✖️") else "total"
    issues.append("%s+%s: %s" % (label, kind, msg))


def _reader_cell(cfg, sel, api):
    rdr = sel.get("reader") or {}
    video = _video_hint(cfg, sel)
    if isinstance(rdr, dict) and rdr:
        mode = rdr.get("mode")
        has_images = bool(
            rdr.get("images")
            or rdr.get("readerImageSelector")
            or (rdr.get("response") or {}).get("images")
            or rdr.get("cdnPathRegex")
            or rdr.get("thumbSelector")
        )
        if has_images:
            base = "✅ image" + (" (%s)" % mode if mode else "")
            return base + video
        if mode:
            return "⚠️ image (mode %s belum lengkap)" % mode + video
    if (sel.get("detail") or {}).get("imageUrls"):
        return "✅ image (detail.imageUrls)" + video
    if cfg.get("decryption"):
        return "✅ image (decryption)" + video
    if cfg.get("hitomiProtocol"):
        return "✅ image (hitomiProtocol)" + video
    images = (
        api.get("images")
        or api_endpoint(cfg, "images", "pages", "contentUrl")
        or ((api.get("detail") or {}).get("images") if api else None)
    )
    if images:
        return "✅ image (API)" + video
    if not cfg.get("scraper") and not api:
        return "✖️ reader: tidak ada scraper/adapter"
    return "✖️ reader: selectors.reader.images hilang"


def _video_hint(cfg, sel):
    """Ada indikator konten video -> kolom reader ditandai (cek live: card -> webview)."""
    try:
        blob = json.dumps(sel).lower() + " " + (cfg.get("notes") or "").lower()
    except (TypeError, ValueError):
        blob = ""
    if "video" in blob:
        return " · 🎥 video?"
    return ""


# ---------------------------------------------------------------- facets ----
def _facet_route(up, keys):
    for key in keys:
        url = get_url(up, key)
        if not url:
            continue
        page_url = get_url(up, key + "Page")
        if page_url:
            return "ok", url, page_url
        if "{page}" in url:
            return "ok", url, url
        return "nopag", url, None
    return None


def _facet_api(cfg, names):
    for n in names:
        ep = api_endpoint(cfg, n)
        if ep:
            pth = _api_path(ep) or ""
            return pth, _api_has_paging(pth)
    return None, False


def _via_search_cell(search, search_pag, label, issues):
    if search.startswith("✖️"):
        issues.append("%s: mau via search tapi search sendiri ✖️" % label)
        return "✖️ via-search (search ✖️)"
    if search_pag.startswith("✅"):
        return "✅ via-search"
    issues.append("%s: via search - pagination mengikuti kolom Search+Pag yang %s"
                  % (label, search_pag[:1]))
    return "⚠️ via-search"


def _facet_cells(cfg, up, is_api, search, search_pag, issues):
    feats = cfg.get("features") or {}
    tqm = ((cfg.get("navigation") or {}).get("tagQueryMapping")) or {}
    fp = cfg.get("featurePages") or {}
    blocked = feats.get("contentByTag") is False
    cells = {}

    # ---------------- genre ----------------
    res = _facet_route(up, ["genreSearch"])
    if res:
        st, url, pg = res
        if st == "ok":
            cells["genre"] = "✅ " + route(url) + " +pag"
        else:
            cells["genre"] = "⚠️ " + route(url)
            issues.append("genre: route genreSearch ada tapi genreSearchPage hilang")
    elif blocked:
        cells["genre"] = "✖️ nonaktif"
        issues.append("genre: features.contentByTag=false - fitur tag/genre dimatikan")
    else:
        pth, pag = _facet_api(cfg, ("tagSearch", "genres", "tagsSearch"))
        if pth:
            cells["genre"] = ("✅ " if pag else "⚠️ ") + route(pth, 40) + (" API+pag" if pag else " API tanpa pag")
            if not pag:
                issues.append("genre: endpoint API genre tanpa param page")
        elif "genre" in tqm or "default" in tqm:
            cells["genre"] = _via_search_cell(search, search_pag, "genre", issues)
        elif _facet_route(up, ["tagSearch", "tag"]):
            cells["genre"] = "⚠️ via tag route"
            issues.append("genre: config tanpa genreSearch - kemungkinan memakai route tag")
        elif fp.get("genreList"):
            cells["genre"] = "⚠️ genre-index"
            issues.append("genre: hanya ada featurePages.genreList (indeks daftar genre, bukan hasil)")
        else:
            cells["genre"] = "✖️"
            issues.append("genre: tidak ada genreSearch / tagQueryMapping / endpoint API")

    # ---------------- tag ----------------
    res = _facet_route(up, ["tagSearch", "tag"])
    if res:
        st, url, pg = res
        if st == "ok":
            cells["tag"] = "✅ " + route(url) + " +pag"
        else:
            cells["tag"] = "⚠️ " + route(url)
            issues.append("tag: route tag ada tapi varian Page hilang")
    elif blocked:
        cells["tag"] = "✖️ nonaktif"
        issues.append("tag: features.contentByTag=false - fitur tag/genre dimatikan")
    else:
        if "tag" in tqm or "default" in tqm:
            cells["tag"] = _via_search_cell(search, search_pag, "tag", issues)
        else:
            pth, pag = _facet_api(cfg, ("tagsSearch", "tagSearch"))
            if pth:
                cells["tag"] = ("✅ " if pag else "⚠️ ") + route(pth, 40) + (" API+pag" if pag else " API tanpa pag")
                if not pag:
                    issues.append("tag: endpoint API tag tanpa param page")
            elif _facet_route(up, ["genreSearch"]):
                cells["tag"] = "⚠️ via genre route"
                issues.append("tag: config tanpa tagSearch - kemungkinan memakai route genre")
            elif "genres" in (cfg.get("api") or {}).get("endpoints", {}):
                cells["tag"] = "⚠️ via API genres"
                issues.append("tag: hanya endpoint genres (daftar), bukan route hasil tag")
            else:
                cells["tag"] = "✖️"
                issues.append("tag: tidak ada tagSearch / tagQueryMapping / endpoint API")

    # ---------------- author ----------------
    res = _facet_route(up, ["authorSearch"])
    if res:
        st, url, pg = res
        if st == "ok":
            cells["author"] = "✅ " + route(url) + " +pag"
        else:
            cells["author"] = "⚠️ " + route(url)
            issues.append("author: route authorSearch ada tapi authorSearchPage hilang")
    elif "author" in tqm:
        cells["author"] = _via_search_cell(search, search_pag, "author", issues)
    else:
        cells["author"] = "✖️"
        issues.append("author: tidak ada authorSearch dan tanpa tagQueryMapping.author")

    # ---------------- artist ----------------
    res = _facet_route(up, ["artistSearch"])
    if res:
        st, url, pg = res
        if st == "ok":
            cells["artist"] = "✅ " + route(url) + " +pag"
        else:
            cells["artist"] = "⚠️ " + route(url)
            issues.append("artist: route artistSearch ada tapi artistSearchPage hilang")
    elif "artist" in tqm:
        cells["artist"] = _via_search_cell(search, search_pag, "artist", issues)
    else:
        cells["artist"] = "✖️"
        issues.append("artist: tidak ada artistSearch dan tanpa tagQueryMapping.artist")

    return cells


# ----------------------------------------------------------------- build ----
def build():
    rows = load_configs()
    results = [(rel, src, analyze(cfg), cfg) for _, src, rel, cfg in rows]

    lines = ["# Support Config Matrix", ""]
    lines.append(
        "Status tiap kolom di bawah = hasil **analisis statis config** (route + selector "
        "yang dideklarasikan). Status **live** (Playwright/curl/web search lewat skill "
        "`kuron-config-verify`) adalah kebenaran; kalau beda, tulis selisihnya di kolom "
        "**Note** + buka issue `config-verify`."
    )
    lines.append("")
    lines.append("Sumber data: `config/*/*-config.json` (kecuali `config/new/`) - regenerate: "
                 "`python3 scripts/generate_support_table.py`")
    lines.append("")
    lines.append(MARKER)
    lines.append("")

    # Legend
    lines += [
        "## Legend",
        "",
        "| Simbol | Arti |",
        "|---|---|",
        "| ✅ | support - jalan / route lengkap |",
        "| ⚠️ | partial - jalan sebagian atau perlu konfirmasi live |",
        "| ✖️ | error - fitur tidak ada / route tidak ditemukan (config juga tidak mengklaim) |",
        "| 🚫 | **dinyatakan bisa di config tapi aktualnya tidak ada** - `features.X=true` "
        "tanpa route/selector pendukung, atau live: situsnya tak punya fitur itu |",
        "| `+pag` | punya route halaman (`...Page`) -> pagination jalan |",
        "| `(next-only)` | halaman berikutnya jalan, tapi total tak terbaca |",
        "| `via-search` | facet ditangani lewat route search (tagQueryMapping) |",
        "| `API` | lewat endpoint `api.endpoints`, bukan HTML route |",
        "| `🎥 video?` | ada indikator video -> wajib cek live: card tampil + klik -> webview |",
        "",
    ]

    # Ringkasan
    lines += ["## Ringkasan per kolom", "",
              "| Kolom | ✅ | ⚠️ | ✖️ | 🚫 |", "|---|---:|---:|---:|---:|"]
    for key, label in COLUMNS:
        c = {"ok": 0, "part": 0, "miss": 0, "claim": 0}
        for _, _, res, _ in results:
            v = res[key]
            bucket = ("ok" if v.startswith("✅") else "part" if v.startswith("⚠️")
                      else "claim" if v.startswith("🚫") else "miss")
            c[bucket] += 1
        lines.append("| %s | %d | %d | %d | %d |"
                     % (label, c["ok"], c["part"], c["miss"], c["claim"]))
    n_claim = sum(1 for _, _, res, _ in results
                  if any(res[k].startswith("🚫") for k in COLUMNS_KEYS))
    lines += ["", "Total config: **%d**" % len(results),
              "", "Kolom 🚫 = config mengklaim fitur ada tapi deklarasi/situsnya tidak "
              "menyediakan - **%d config** kena." % n_claim, ""]

    # Masalah terbuka
    FACET_ONLY = ("author:", "artist:")
    claimed, crit, facet_missing, aa_missing, partial = [], [], [], [], []
    for rel, src, res, cfg in results:
        cl = [k for k, _ in COLUMNS if res[k].startswith("🚫")]
        core = [k for k, _ in COLUMNS[:6] if res[k].startswith("✖️")]
        gen = [k for k in ("genre", "tag") if res[k].startswith("✖️")]
        aa = [k for k in ("author", "artist") if res[k].startswith("✖️")]
        warn = [k for k, _ in COLUMNS if res[k].startswith("⚠️")]
        cl_iss = [i for i in res["issues"] if "=true" in i]
        core_iss = [i for i in res["issues"]
                    if not i.startswith(FACET_ONLY) and "=true" not in i]
        if cl:
            claimed.append((src, rel, cl, cl_iss))
        if core:
            crit.append((src, rel, core, core_iss))
        elif gen:
            facet_missing.append((src, rel, gen, core_iss))
        if aa:
            aa_missing.append((src, rel, aa))
        if warn and not core and not gen and not cl:
            partial.append((src, rel, warn, core_iss))

    lines.append("## Masalah terbuka")
    lines.append("")
    lines.append(
        "Perbaikan berurutan: 🚫 klaim palsu → ✖️ kritis → ✖️ genre/tag → ⚠️ partial → "
        "gap author/artist. Tiap baris = kandidat issue `config-verify` (cari duplikat dulu)."
    )
    lines.append("")
    lines.append("### 1. 🚫 Dinyatakan support tapi aktualnya tidak ada (%d config)" % len(claimed))
    lines.append("")
    if claimed:
        for src, rel, cols, iss in claimed:
            lines.append("- **%s** (`%s`) - kolom: %s" % (src, rel, ", ".join(cols)))
            for i in iss:
                lines.append("  - %s" % esc(i))
    else:
        lines.append("- tidak ada")
    lines.append("")
    lines.append("### 2. ✖️ Kritis - fitur inti rusak (%d config)" % len(crit))
    lines.append("")
    if crit:
        for src, rel, cols, iss in crit:
            lines.append("- **%s** (`%s`) - kolom: %s" % (src, rel, ", ".join(cols)))
            for i in iss:
                lines.append("  - %s" % esc(i))
    else:
        lines.append("- tidak ada")
    lines += ["", "### 3. ✖️ Genre/Tag tidak ada route (%d config)" % len(facet_missing), ""]
    if facet_missing:
        for src, rel, cols, iss in facet_missing:
            lines.append("- **%s** (`%s`) - %s" % (src, rel, ", ".join(cols)))
            for i in iss:
                lines.append("  - %s" % esc(i))
    else:
        lines.append("- tidak ada")
    lines += ["", "### 4. ⚠️ Partial - jalan tapi rusak sebagian (%d config)" % len(partial), ""]
    if partial:
        groups = {}
        for src, rel, warn, iss in partial:
            for i in iss:
                groups.setdefault(i, []).append(src)
            if not iss:
                groups.setdefault("(kolom ⚠️ tanpa rincian)", []).append(src)
        for msg in sorted(groups, key=lambda m: (-len(groups[m]), m)):
            names = groups[msg]
            shown = ", ".join("`%s`" % n for n in names[:12])
            if len(names) > 12:
                shown += ", … +%d lagi" % (len(names) - 12)
            lines.append("- **%s** — %d config: %s" % (esc(msg), len(names), shown))
    else:
        lines.append("- tidak ada")
    lines += ["", "### 5. Gap Author / Artist (%d config)" % len(aa_missing), ""]
    lines.append(
        "Author/artist route jarang dideklarasikan - ini statistik, **bukan** daftar issue. "
        "Detail per config lihat kolom Author/Artist di Support Matrix. "
        "Config yang sudah punya author/artist route justru patut diprioritaskan untuk diverifikasi live."
    )
    lines.append("")
    if aa_missing:
        no_author = sorted({s for s, _, c in aa_missing if "author" in c})
        no_artist = sorted({s for s, _, c in aa_missing if "artist" in c})
        all_src = sorted({r[1] for r in results})
        ok_author = sorted(set(all_src) - set(no_author))
        ok_artist = sorted(set(all_src) - set(no_artist))
        lines.append("- tanpa author route: **%d / %d** config (yang punya: %s)"
                     % (len(no_author), len(results),
                        ", ".join("`%s`" % x for x in ok_author) or "-"))
        lines.append("- tanpa artist route: **%d / %d** config (yang punya: %s)"
                     % (len(no_artist), len(results),
                        ", ".join("`%s`" % x for x in ok_artist) or "-"))
    else:
        lines.append("- tidak ada")
    lines.append("")

    # Matrix
    lines.append("## Support Matrix")
    lines.append("")
    header = ["No", "Config / Path"] + [h for _, h in COLUMNS] + ["Note"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---:"] + ["---"] * (len(header) - 1)) + "|")
    for i, (rel, src, res, cfg) in enumerate(results, 1):
        base = cfg.get("baseUrl", "")
        cfg_cell = "`%s`%s" % (rel, ("<br>%s" % base) if base else "")
        cells = [str(i), cfg_cell] + [esc(res[k]) for k, _ in COLUMNS]
        cells.append("<br>".join(esc(x) for x in res["issues"]) if res["issues"] else "-")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 bila file beda / sync putus")
    args = ap.parse_args()

    sync = check_sync()
    if sync:
        print("SYNC PUTUS - marker %s tidak cocok di:" % MARKER)
        for s in sync:
            print("  - " + s)
        print("Perbaiki marker di AGENTS.md dan SKILL.md (atau ubah COLUMNS di script ini).")
        sys.exit(1)

    content = build()
    if args.check:
        try:
            with open(OUT, encoding="utf-8") as fh:
                current = fh.read()
        except FileNotFoundError:
            current = ""
        keys = live_keys_of(current)
        if normalize_live(content, keys) != normalize_live(current, keys):
            print("SUPPORT-CONFIG.md stale - jalankan scripts/generate_support_table.py")
            print("(baris/row bertanda `live` dikecualikan: itu hasil verifikasi manual)")
            sys.exit(1)
        print("SUPPORT-CONFIG.md up to date + sync skill/AGENTS OK")
        return
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(content)
    print("wrote %s (%d bytes)" % (OUT, len(content)))


if __name__ == "__main__":
    main()
