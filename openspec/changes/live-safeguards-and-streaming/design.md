# Design: Live Safeguards & True SSE Streaming

## Context
HybridRouter provides 3-tier routing between Flash, Pro, and Opus models. To ensure high reliability and responsive UX for coding agents like Hermes, we are wiring in-memory circuit breakers and test integrity guards directly into FastAPI request processing, and refactoring upstream forwarding to stream SSE chunks without memory buffering.

## Architecture & Data Flow

```
Client (Hermes / OpenCode)
   │ POST /v1/chat/completions (stream=True/False)
   ▼
[ FastAPI App (src/server/app.py) ]
   │
   ├─► 1. Extract metadata (session_id, traceback, diff, files_target)
   │
   ├─► 2. CircuitBreakerTracker:
   │      - Record traceback -> if loop (3x) => force escalate
   │      - Record diff -> if oscillation / empty (3x) => force escalate & rollback flag
   │
   ├─► 3. TestIntegrityGuard:
   │      - If target is Tier 3 (Flash) and files_target touches tests/** => Reject 403 / Escalate
   │
   ├─► 4. RouterEngine:
   │      - Route with escalated tier constraints
   │
   └─► 5. UpstreamClient (src/server/upstream.py):
          - If stream=False: httpx.AsyncClient.post(...) with fallback chain
          - If stream=True: httpx.AsyncClient.stream("POST", ...)
            * If initial status in (429, 502, 503, 504) -> close stream & fallback
            * Yield chunks as they arrive -> StreamingResponse(chunk_generator)
```

## State Management
- In-memory dictionary `session_breakers: dict[str, CircuitBreakerTracker]` attached to `app.state`.
- New endpoint `GET /breakers/status?session_id=...` returning current circuit stats.
- New endpoint `POST /breakers/reset?session_id=...` to reset breaker counters.

## Streaming Implementation
- Refactor `UpstreamClient.forward_stream(alias, payload)` as an async generator or response context manager yielding raw SSE bytes.
- Pre-stream fallback handles upstream HTTP 429/5xx before any header/data is flushed to the client.
