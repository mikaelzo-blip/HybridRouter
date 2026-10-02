# Tasks

## 1. True Low-Latency SSE Streaming in UpstreamClient & Server

- [x] 1.1 Write integration tests in `tests/test_streaming.py` verifying real-time chunk streaming and pre-stream fallback on HTTP 429/5xx errors
- [x] 1.2 Implement `forward_stream` in `src/server/upstream.py` using `httpx.AsyncClient.stream("POST", ...)` and integrate into `src/server/app.py`
- [x] 1.3 Verify streaming tests pass with `uv run pytest tests/test_streaming.py`

## 2. In-Memory Circuit Breaker State & Escalation Wiring in Server

- [x] 2.1 Write integration tests in `tests/test_live_breakers.py` for `/breakers/status`, loop traceback escalation, and consecutive empty diff escalation during chat completions
- [x] 2.2 Wire `CircuitBreakerTracker` into `src/server/app.py` request processing and implement `/breakers/status` and `/breakers/reset` endpoints
- [x] 2.3 Verify live circuit breaker tests pass with `uv run pytest tests/test_live_breakers.py`

## 3. Live Test Integrity Guard Enforcement

- [x] 3.1 Write integration tests in `tests/test_live_guard.py` testing rejection of Tier 3 edits on test files and enforcement of test invariants on incoming requests
- [x] 3.2 Wire `TestIntegrityGuard` into `src/server/app.py` request routing pipeline
- [x] 3.3 Verify live guard tests pass with `uv run pytest tests/test_live_guard.py`

## 4. Full Suite Regression & OpenSpec Verification

- [x] 4.1 Execute full test suite with `uv run pytest` and verify zero failures
- [x] 4.2 Validate OpenSpec change with `openspec validate live-safeguards-and-streaming --strict`
