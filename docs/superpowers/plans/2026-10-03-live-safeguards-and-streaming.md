# Live Safeguards & True SSE Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate live in-memory circuit breakers and test integrity guards into the FastAPI request lifecycle, and replace buffered streaming with true zero-buffering SSE chunk streaming in the upstream client.

**Architecture:** Wire `CircuitBreakerTracker` and `TestIntegrityGuard` into `/v1/chat/completions` inside `src/server/app.py`. Update `src/server/upstream.py` to use `httpx.AsyncClient.stream("POST", ...)` with pre-stream fallback, yielding byte chunks directly into FastAPI's `StreamingResponse`.

**Tech Stack:** Python 3.14+, FastAPI, httpx, Pydantic v2, Pytest, Pytest-Asyncio.

**Spec:** `openspec/changes/live-safeguards-and-streaming/specs/` and `openspec/changes/live-safeguards-and-streaming/design.md`.

## Global Constraints
- Strictly preserve backward compatibility for existing endpoints (`/health`, `/v1/models`, `/debug/route`).
- Use in-memory state for session breakers (`app.state.session_breakers`), zero external database or Redis bloat.
- Support pre-stream fallback: if upstream returns 429/5xx before any stream bytes are emitted, smoothly fall back to the next model in the fallback chain.
- All code changes strictly covered by TDD (RED -> GREEN).

## Review Focus
- High-frequency streaming chunks must not raise `RuntimeError: Response content has already been streamed`.
- Session breaker state must isolate counters per `session_id`.
- Rejection of Tier 3 edits on test files must return HTTP 403 Forbidden with a clear error payload.
- Pre-stream 429 errors during streaming must transition to fallback model without leaking broken SSE frames.
- Stream generator must properly close upstream connection even if the client disconnects prematurely.

---

### Task 1: True Low-Latency SSE Streaming in UpstreamClient & Server

**Files:**
- Modify: `src/server/upstream.py`
- Modify: `src/server/app.py`
- Test: `tests/test_streaming.py`

**Interfaces:**
- Consumes: `UpstreamClient.forward_stream(alias: str, payload: dict[str, Any]) -> tuple[AsyncIterator[bytes], str, dict[str, str]]`
- Produces: StreamingResponse with real-time SSE chunk forwarding and pre-stream resilience fallback.

- [x] **Step 1: Write failing test in `tests/test_streaming.py`**
- [x] **Step 2: Run test to verify it fails (RED)**
- [x] **Step 3: Implement true streaming in `src/server/upstream.py` and `src/server/app.py`**
- [x] **Step 4: Run test to verify it passes (GREEN)**
- [x] **Step 5: Commit changes**

---

### Task 2: In-Memory Circuit Breaker State & Escalation Wiring in Server

**Files:**
- Modify: `src/server/app.py`
- Test: `tests/test_live_breakers.py`

**Interfaces:**
- Consumes: `CircuitBreakerTracker`, `normalize_traceback`
- Produces: `/breakers/status`, `/breakers/reset`, and automatic escalation on `/v1/chat/completions`

- [x] **Step 1: Write failing test in `tests/test_live_breakers.py`**
- [x] **Step 2: Run test to verify it fails (RED)**
- [x] **Step 3: Wire circuit breaker tracking into `src/server/app.py`**
- [x] **Step 4: Run test to verify it passes (GREEN)**
- [x] **Step 5: Commit changes**

---

### Task 3: Live Test Integrity Guard Enforcement

**Files:**
- Modify: `src/server/app.py`
- Test: `tests/test_live_guard.py`

**Interfaces:**
- Consumes: `TestIntegrityGuard`
- Produces: Test file protection gate in `/v1/chat/completions`

- [x] **Step 1: Write failing test in `tests/test_live_guard.py`**
- [x] **Step 2: Run test to verify it fails (RED)**
- [x] **Step 3: Integrate test integrity guard into `src/server/app.py`**
- [x] **Step 4: Run test to verify it passes (GREEN)**
- [x] **Step 5: Commit changes**

---

### Task 4: Full Suite Regression & OpenSpec Verification

**Files:**
- Test: All tests under `tests/`
- Documentation: `openspec/changes/live-safeguards-and-streaming/tasks.md`

- [x] **Step 1: Run full pytest suite**
- [x] **Step 2: Run OpenSpec strict validation**
- [x] **Step 3: Mark tasks completed and commit**
