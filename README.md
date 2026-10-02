# Hybrid Autorouter 3-Tier

Sistem routing multi-model untuk coding agent:
- Tier 0: Claude Opus 4.6 (Apex design & architectural rescue)
- Tier 1/2: Gemini 3.1 Pro (Ingestion & tactical logic patching)
- Tier 3: Gemini 3.8 Flash (Tool loop & rapid coder)
- Upstream: 9Router pada `http://127.0.0.1:20128/v1`
- Gateway: Local port 20250 (`/v1/chat/completions`, `/debug/route`, `/health`)
