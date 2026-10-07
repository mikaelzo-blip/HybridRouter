# Hybrid Autorouter 3-Tier

Sistem routing multi-model untuk coding agent:
- Tier 0: Claude Opus 4.6 (Apex design & architectural rescue)
- Tier 1/2: Gemini 3.1 Pro (Ingestion & tactical logic patching)
- Tier 3: Gemini 3.8 Flash (Tool loop & rapid coder)
- Upstream: 9Router pada `http://127.0.0.1:20128/v1`
- Gateway: Local port 20250 (`/v1/chat/completions`, `/debug/route`, `/health`)

## Catatan Arsitektur & Batasan Sistem

1. **Per-Request Routing vs Orchestrated Pipeline**:
   - Endpoint `/v1/chat/completions` beroperasi sebagai single-turn request proxy router.
   - Aturan YAML yang mendefinisikan blok `pipeline: [...]` (misal `ingest_then_design`) akan diarahkan oleh server ke target langkah pertama (`pipeline[0].target`).
   - Eksekusi alur multi-step penuh (seperti siklus distilasi -> arsitektur dengan feedback loop `NEED_FILE`) digerakkan di sisi client melalui modul `src/distiller/dynamic_distiller.py`.

2. **Deteksi Diff & Circuit Breakers Otomatis**:
   - Router mengekstrak diff secara otomatis dari pesan `patch` dan `write_file` assistant di tail percakapan, sehingga proteksi osilasi diff dan empty-diff aktif tanpa memerlukan wrapper client khusus.
   - Streak error (`consecutive_identical_tracebacks`) otomatis direset ketika turn berikutnya berjalan bersih tanpa kegagalan (`retry_count == 0` dan tidak ada traceback).

3. **Persistensi State**:
   - Konfigurasi `CIRCUIT_STATE_FILE` dan `SPEND_STATE_FILE` (opsional) memungkinkan snapshot state loop breaker dan pemakaian token disimpan ke disk (JSON) agar bertahan melintasi restart server. Default fail-open jika tidak diset.

