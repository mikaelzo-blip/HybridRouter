# Proposal

## Why
While unit tests verify the standalone logic of `CircuitBreakerTracker` and `TestIntegrityGuard`, they are not yet wired into the live HTTP request pipeline (`src/server/app.py`). Furthermore, proxy streaming currently buffers upstream responses in memory before returning them, causing unnecessary latency for client applications like Hermes. Connecting these safeguards and implementing zero-buffering SSE streaming will elevate HybridRouter to production-grade reliability and responsiveness.

## What Changes
- **Live Circuit Breaker & Safeguards Pipeline**:
  - Integrate `CircuitBreakerTracker` into `/v1/chat/completions` request lifecycle with in-memory session tracking.
  - Automatically detect loop tracebacks and empty diffs from request metadata, triggering immediate tier escalation.
  - Integrate `TestIntegrityGuard` into request processing to prevent Tier 3 (Flash) edits on test files and reject test tampering.
  - Expose `/breakers/status` endpoint to inspect live circuit breaker state and reset session counters if needed.
- **Low-Latency True SSE Streaming**:
  - Upgrade `UpstreamClient` to utilize `httpx.AsyncClient.stream("POST", ...)` instead of buffered `client.post` for streaming chat completions.
  - Implement zero-buffering chunk generator yielding SSE data frames in real time as upstream emits them.
  - Preserve pre-stream resilience fallback: if upstream returns 429/5xx before the stream commits, seamlessly fail over to the next tier in the fallback chain.

## Capabilities

### New Capabilities
- `live-safeguards`: In-memory circuit breaker and test guard enforcement inside the HTTP proxy pipeline, with automated escalation and status querying.
- `true-sse-streaming`: Low-latency, zero-buffering server-sent events proxying with pre-stream fallback preservation.

### Modified Capabilities
- `openai-proxy`: Updated `/v1/chat/completions` handler to enforce circuit breakers before routing and stream SSE tokens on the fly.

## Impact
- Instantaneous Time-to-First-Token (TTFT) for streaming clients (Hermes, OpenCode).
- Immediate runtime protection against infinite loops, oscillating diffs, and test degradation.
- Zero extra external dependencies (FastAPI + httpx + in-memory state).
