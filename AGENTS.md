# AGENTS.md — Aturan Kerja Repo kuron-extensions

Repo ini berisi **config source** Kuron + registry `manifest.json`. Baca skill di `.agents/skills/`
sebelum kerja: `kuron-source-config` (authoring/publish config) dan `kuron-config-verify` (verifikasi fitur).

## Peta repo

```text
config/<bucket>/<id>-config.json   # config source (bucket: id|en|ja|vi|ch|global)
config/new/                        # staging config belum terverifikasi - JANGAN daftarkan ke manifest
images/<id>.png                    # ikon source
manifest.json                      # registry yang dibaca aplikasi (schemaVersion 2)
SUPPORT-CONFIG.md                  # MATRIX support fitur per config (hasil verifikasi) - JANGAN dihapus barisnya
scripts/generate_support_table.py  # generator statis matrix dari config
scripts/refresh_manifest.py        # sync versi/checksum/sizeKb/lastUpdated
docs/                              # gitignored, kerja lokal saja
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
5. Tiap ✖️/⚠️ berarti bug config → wajib GitHub issue (`gh issue create --label config-verify`, cari duplikat dulu), dan **tutup hanya setelah re-verify ✅**.
6. `SUPPORT-CONFIG.md` berisi 4 blok: Legend, Ringkasan per kolom (kolom 🚫), **Masalah terbuka** (berurutan: 🚫 klaim palsu → ✖️ kritis → ✖️ genre/tag → ⚠️ partial dikelompokkan per jenis masalah → statistik gap author/artist), Support Matrix. Bagian "Masalah terbuka" = daftar kandidat issue, urut prioritas.
7. Sync dengan skill: marker `<!-- matrix-columns: ... -->` ada di `AGENTS.md` + `SKILL.md`, dicek `python3 scripts/generate_support_table.py --check` (exit 1 bila marker beda / matrix stale). Kalau ubah urutan kolom, ubah **ketiganya** (COLUMNS di script, marker di AGENTS.md, marker + tabel kolom di SKILL.md).
8. Setelah verifikasi selesai → catat di `MEMORY.md` (lihat Rules: MEMORY.md). Matrix hasil edit manual jangan ditimpa regenerate; kalau memang regenerate, gabungkan temuan live dulu. Baris yang sudah diverifikasi live ditandai teks `live` di kolom **Note** — `--check` otomatis mengabaikan baris config begitu (jadi edit live tidak dianggap stale), tapi **isi baris tetap hilang saat regenerate** → simpan dulu temuan live-nya.

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
