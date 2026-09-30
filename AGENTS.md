# AGENTS.md — Aturan Kerja Repo kuron-extensions

Repo ini berisi **config source** Kuron + registry `manifest.json`. Baca skill di `.agents/skills/`
sebelum kerja: `kuron-source-config` (authoring/publish config), `kuron-config-verify` (verifikasi fitur),
`kuron-issue` (buat/lapor issue lewat form `.github/ISSUE_TEMPLATE/`).

## Peta repo

```text
config/<bucket>/<id>-config.json   # config source (bucket: id|en|ja|vi|ch|global)
config/new/                        # staging config belum terverifikasi - JANGAN daftarkan ke manifest
images/<id>.png                    # ikon source
manifest.json                      # registry yang dibaca aplikasi (schemaVersion 2)
SUPPORT-CONFIG.md                  # MATRIX support fitur per config (hasil verifikasi) - JANGAN dihapus barisnya
scripts/generate_support_table.py  # generator statis matrix dari config
scripts/refresh_manifest.py        # sync versi/checksum/sizeKb/lastUpdated
scripts/issue_body.py              # isi issue dari form .github/ISSUE_TEMPLATE (--list/--check)
.agents/skills/                    # skills (gitignored)
```

## Rules: authoring config

1. Ikuti skill `kuron-source-config`. Skema field lengkap ada di `docs/CONFIG-GUIDE.md` repo `nhasixapp`.
2. Satu source = satu bucket = satu file; nama file = field `source`. Duplikat id DILARANG.
3. `version` semver penuh (`MAJOR.MINOR.PATCH`, tanpa suffix), sama antara file dan manifest; bump PATCH setiap ubah config.
4. `meta.language` manifest = nama bucket. Ikon `images/<id>.png` wajib ada.
5. Jangan edit `checksum`/`sizeKb`/`lastUpdated` manual → `python3 scripts/refresh_manifest.py`, lalu `--check` harus bersih.
6. Config baru masuk dulu ke `config/new/`, diverifikasi, baru dipindah ke bucket + daftar ke manifest.

## Rules: verifikasi fitur (support matrix)

Dikerjakan lewat skill `kuron-config-verify`. Intinya:

1. **Semua config wajib punya baris di `SUPPORT-CONFIG.md`.** Urutan kolom tetap (marker sync: `<!-- matrix-columns: ... -->` dicek `scripts/generate_support_table.py --check`):
   <!-- matrix-columns: home|home_pag|search|search_pag|detail|reader|genre|tag|author|artist -->
   `No | Config/Path | Home | Home+Pag+Total | Search | Search+Pag+Total | Detail+Chapters | Reader | Genre+Pag | Tag+Pag | Author+Pag | Artist+Pag | Note`
2. Genre / Tag / Author / Artist **terpisah** — ada yang support cuma salah satu; pagination dinilai terpisah juga (ada route `...Page` → `+pag`, tidak → remark).
3. Emoji: ✅ support, ⚠️ partial, ✖️ error/tidak ada, **🚫 config menyatakan bisa tapi aktualnya tidak ada** (`features.X=true` tanpa route/selector pendukung, atau live: situsnya tak punya fitur itu) — wajib dicatat di **Note** dengan bukti (`<kolom>: features.X=true tapi …` / `live: …`).
4. Status **statis** (dari config) = dugaan. Status **live** (Playwright/curl/web search) = kebenaran; live menang, tulis selisihnya di Note.
5. Tiap ✖️/⚠️ berarti bug config → wajib GitHub issue (`gh issue create --label config-verify`, cari duplikat dulu), dan **tutup hanya setelah re-verify ✅**. Issue dari user boleh lewat template `.github/ISSUE_TEMPLATE/` (lihat Rules: issue).
6. `SUPPORT-CONFIG.md` berisi 4 blok: Legend, Ringkasan per kolom (kolom 🚫), **Masalah terbuka** (berurutan: 🚫 klaim palsu → ✖️ kritis → ✖️ genre/tag → ⚠️ partial dikelompokkan per jenis masalah → statistik gap author/artist), Support Matrix. Bagian "Masalah terbuka" = daftar kandidat issue, urut prioritas.
7. Sync dengan skill: marker `<!-- matrix-columns: ... -->` ada di `AGENTS.md` + `SKILL.md`, dicek `python3 scripts/generate_support_table.py --check` (exit 1 bila marker beda / matrix stale). Kalau ubah urutan kolom, ubah **ketiganya** (COLUMNS di script, marker di AGENTS.md, marker + tabel kolom di SKILL.md).
8. Setelah verifikasi selesai → catat di `MEMORY.md` (lihat Rules: MEMORY.md). Matrix hasil edit manual jangan ditimpa regenerate; kalau memang regenerate, gabungkan temuan live dulu. Baris yang sudah diverifikasi live ditandai teks `live` di kolom **Note** — `--check` otomatis mengabaikan baris config begitu (jadi edit live tidak dianggap stale), tapi **isi baris tetap hilang saat regenerate** → simpan dulu temuan live-nya.

## Rules: issue (GitHub)

Issue form lives in `.github/ISSUE_TEMPLATE/`. **Pilih satu sesuai tipe + bahasa** — jangan campur bahasa dalam satu issue.

| File | Untuk | Label otomatis |
|---|---|---|
| `config-bug-id.yml` | config rusak, laporan Bahasa Indonesia | `config-verify`, `bug` |
| `config-bug-en.yml` | config rusak, laporan English | `config-verify`, `bug` |
| `config-bug-zh.yml` | 配置错误报告（中文） | `config-verify`, `bug` |
| `feature-id.yml` | feature request / perbaikan tooling, ID | `enhancement` |
| `feature-en.yml` | feature request / tooling, English | `enhancement` |
| `feature-zh.yml` | 功能请求（中文） | `enhancement` |
| `config.yml` | matikan blank issue + link ke `SUPPORT-CONFIG.md` & `AGENTS.md` | - |

1. Satu issue = satu config + satu kelompok masalah. Kalau config punya banyak kolom rusak, pilih **semua** kolomnya di dropdown (field-nya multi-select), jangan buka issue terpisah per kolom.
2. Isi minimal untuk bug config: source id, path config, baseUrl, kolom fitur, status (✅/⚠️/✖️/🚫), yang diharapkan, yang terjadi, **bukti** (curl/log/screenshot).
3. Nama kolom fitur di ketiga bahasa **wajib sama persis** (Home, Home+Pag+Total, …, Artist+Pag) supaya issue bisa dicari/difilter lintas bahasa.
4. Prefix title otomatis: `[bug][id]`, `[bug][en]`, `[bug][zh]`, `[feat][id]`, `[feat][en]`, `[feat][zh]`. Jangan hapus prefixnya.
5. Bukti wajib bisa direproduksi (max 2 percobaan sesuai skill); issue tanpa bukti → closet dengan alasan, bukan `config-verify` baru.
6. Issue dari agent lewat skill `.agents/skills/kuron-issue/SKILL.md` — renderer `python3 scripts/issue_body.py --form bug:id --set …` mengisi body dari form yang sama (`--list` menampilkan field, `--check` menggigit form yang tidak sinkron), lalu `gh issue create` mengirim title ber-prefix + label dari form.
7. Jejak issue: `MEMORY.md` (baris `| Jam | issue | #N + form | … | kuron-issue |`) + `(issue #N)` di kolom Note baris matrix yang sudah bertanda `live` (baris non-live dibiarkan agar `generate_support_table.py --check` hijau). Issue ditutup hanya setelah re-verify live ✅.

## Rules: MEMORY.md

1. `MEMORY.md` = log permanen keputusan & hasil verifikasi, wajib berbentuk **tabel** (`| Jam | Aksi | Target | Hasil | Agent |`) dengan **jam (`YYYY-MM-DD HH:MM`, WIB)**, dan **urutan terbaru di atas** (baris baru = entry paling atas, tepat di bawah header `## Log`). Tambah baris, jangan pernah menghapus/hilangkan riwayat lama.
2. Kolom `Agent` = siapa yang mengerjakan: `opencode` (agent utama), `<nama-skill>` (mis. `kuron-config-verify`, hasil pemakaian skill itu), atau `user` (manual). Wajib diisi, tanpa ini entry tidak sah.
3. Kolom `Hasil` = ringkas + emoji (✅/⚠️/✖️) dan nomor issue bila ada.
4. Wajib diisi bila: verifikasi config dilakukan, config diubah/publish, issue dibuat/ditutup, aturan di AGENTS.md diubah, skill dibuat/diubah. Jam = waktu kerja benar-benar dikerjakan (bukan waktu commit).

## Rules: umum

- Jalankan semua perintah dari root repo; python3 stdlib saja, `npx playwright` untuk browser, `gh` untuk issue.
- Jangan commit/push tanpa diminta user.
- Jangan commit `.DS_Store`.
- Larangan dari skill terkait (checksum manual, daftarkan `config/new/`, hapus baris matrix) berlaku mutlak.
