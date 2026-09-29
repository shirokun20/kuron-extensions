# MEMORY.md — Log keputusan & hasil kerja

Format **tabel**, **urutan terbaru di atas** (baris paling baru = pekerjaan terbaru). Tambah baris baru di bawah header `## Log`, jangan hapus riwayat lama.

Kolom:

| Kolom | Isi |
|---|---|
| Jam | `YYYY-MM-DD HH:MM` (WIB, 24h) |
| Aksi | setup / verifikasi / fix / issue / rules / skill / decision |
| Target | file, config id, issue number, atau skill |
| Hasil | singkat + emoji status |
| Agent | siapa/sesi apa yang mengerjakan (`opencode` = agent utama, `kuron-config-verify` = skill yang dipakai, `user` = manual) |

## Log

| Jam | Aksi | Target | Hasil | Agent |
|---|---|---|---|---|
| 2026-09-29 23:48 | fix | `scripts/generate_support_table.py` | false positive 🚫 diperbaiki: chapter config API dibaca dari `api.detail.chapters` (bukan cuma `api.endpoints.chapters`) → config API (comix, mangadex, doujindesuxxx, rawdevart) tak lagi salah ditandai; sisa 🚫 = **2 config** (mangafire genre, ehentai chapters) ✅ | opencode |
| 2026-09-29 23:48 | fix | `scripts/generate_support_table.py --check` | `--check` kini toleran edit live: baris matrix + sub-bullet config yang Note-nya bertanda `live` dikecualikan dari deteksi stale (kedua sisi dinormalisasi by key config) → check hijau tanpa hilangin temuan live ✅ | opencode |
| 2026-09-29 23:44 | decision | emoji khusus "dinyatakan bisa tapi tidak ada" | dipilih `🚫` (user minta emot khusus);bedanya dari ✖️: ✖️ = config tak mengklaim, 🚫 = config mengklaim tapi tak ada | user + opencode |
| 2026-09-29 23:44 | decision | lanjutan validasi live | **ditunda atas permintaan user** — hanya 3 config yang sudah dicek live (komiku, hentairead, beauty3600000); 101 config sisanya menunggu | user |
| 2026-09-29 23:42 | rules | emoji `🚫` | ditambahkan di legend (`SUPPORT-CONFIG.md`), `AGENTS.md` rule 3, `SKILL.md` (legend + definisi) = "dinyatakan bisa tapi aktualnya tidak ada"; dideteksi statis dari `features.X=true` tanpa route/selector (6 config: comix, mangafire, mangadex, ehentai, doujindesuxxx, rawdevart) ✅ | opencode |
| 2026-09-29 23:42 | fix | `scripts/generate_support_table.py` | `_mark_declared()` menandai sel 🚫; Ringkasan dapat kolom 🚫; Masalah terbuka jadi 5 seksi (🚫 klaim palsu → ✖️ kritis → ✖️ genre/tag → ⚠️ partial → gap author/artist) ✅ | opencode |
| 2026-09-29 23:40 | verifikasi | `config/global/beauty3600000-config.json` | ⚠️ sebagian: home+page2 OK (16 art, total 5325 di link Last tapi tak ada selector total), `?s=` timeout 4x, `/category/gravure/page/2/` timeout 2x; sisa kolom belum dicek | kuron-config-verify |
| 2026-09-29 23:30 | verifikasi | `config/en/hentairead-config.json` | ⚠️ 10 kolom dicek live; 3 temuan (selector home=slider, pagination situs pakai `?act=…&pageNum=`, path `/page/2/` palsu); issue **#2** dibuka | kuron-config-verify |
| 2026-09-29 23:23 | verifikasi | `config/id/komiku-config.json` | ✅ 10 kolom dicek live: static==live; search+pag ✖️ terkonfirmasi (`paged=2`→404, cap 10 hasil); issue **#1** dibuka (label `config-verify`) | kuron-config-verify |
| 2026-09-29 23:18 | setup | label GitHub `config-verify` | dibuat (`gh label create`, #B60205) ✅ | opencode |
| 2026-09-29 23:15 | fix | `scripts/generate_support_table.py` | selesai: kolom facet dipisah (Genre/Tag/Author/Artist + Pag), sel +pag, blok "Masalah terbuka" berprioritas (✖️ kritis → ✖️ genre/tag → ⚠️ partial per jenis → gap author/artist), `--check` exit 1 kalau sync putus ✅ | opencode |
| 2026-09-29 23:15 | sync | `AGENTS.md` + `SKILL.md` kuron-config-verify | marker `<!-- matrix-columns: ... -->` dipasang di keduanya; aturan urutan kolom + struktur 4 blok SUPPORT-CONFIG.md ditulis ✅ | opencode |
| 2026-09-29 23:12 | setup | `SUPPORT-CONFIG.md` | diregenerasi: 104 baris matrix, ringkasan per kolom, issue kritis tinggal 3 config (komiku, hentairead, beauty3600000) ✅ | opencode |
| 2026-09-29 23:01 | rules | `MEMORY.md` | format ditambah kolom **Jam** + aturan urutan **terbaru di atas** ✅ | user + opencode |
| 2026-09-29 22:59 | rules | `AGENTS.md` bagian Rules: MEMORY.md | disesuaikan ke format tabel + kolom `Agent` wajib ✅ | opencode |
| 2026-09-29 22:57 | rules | `MEMORY.md` | format diubah dari bullet `## YYYY-MM-DD` menjadi tabel (Tanggal→Jam/Aksi/Target/Hasil/Agent) ✅ | user + opencode |
| 2026-09-29 22:55 | skill | `.agents/skills/kuron-config-verify/SKILL.md` | dibuat: 10 kolom fitur, urutan buff (static → curl → web search → Playwright), wajib issue label `config-verify`, tutup issue hanya setelah re-verify ✅ | opencode |
| 2026-09-29 22:53 | rules | `AGENTS.md` | dibuat: peta repo, aturan authoring config, aturan support matrix, aturan penulisan MEMORY ✅ | opencode |
| 2026-09-29 22:50 | decision | kolom Genre/Tag/Author/Artist | dipisah (bukan digabung "tag/author/artist") karena support beda-beda; pagination facet dinilai sendiri | user + opencode |
| 2026-09-29 22:45 | setup | `SUPPORT-CONFIG.md` + `scripts/generate_support_table.py` | matrix statis 104 config, kolom ✅/⚠️/✖️ per fitur + route + Note ⚠️ generator belum selesai (fungsi `_facet_cells` belum ada, kolom facet belum dipisah) | opencode |
