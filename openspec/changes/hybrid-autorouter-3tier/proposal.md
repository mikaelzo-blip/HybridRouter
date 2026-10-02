# Proposal

## Why

Coding agent workflows require a robust multi-model routing tier to maximize speed and cost-effectiveness without sacrificing architectural rigor. Instead of routing blindly or relying on financial limits, this system enforces technical convergence via retry escalation, circuit breaker loop detection, test integrity guards, and dynamic codebase distillation, backed by real 9Router Antigravity upstream endpoints.

## What Changes

- **Router Engine & Escalation Ladder**: Rule-based priority router (priorities 100 to 10) evaluating `retry_count`, turn count, token budget, and target files to dispatch tasks between Opus 4.6, Gemini 3.1 Pro, and Gemini 3.8 Flash.
- **Circuit Breakers & Safeguards**: Non-financial protective monitors that detect normalized traceback loops (3x), diff oscillation (window of 6), consecutive empty diffs (3x with rollback), subtask iteration limits (12 iterations or 30 wall-clock minutes), and Opus exhaustion (2 failures -> human handoff).
- **Test Integrity Guard**: AST and diff analysis engine protecting test files (`tests/**`, `*.test.*`, `*_test.*`) from modification by Tier 3 (Flash) and rejecting test tampering (adding `skip`, `xfail`, `.only`, or deleting assertions).
- **Codebase Distillation Pipeline**: Ingestion pipeline triggering for contexts exceeding 40,000 tokens to extract structural context (< 12,000 tokens) via Gemini 3.1 Pro, with a `NEED_FILE: <path>` feedback loop (up to 3 rounds) for Opus 4.6.
- **OpenAI-Compatible Proxy Server**: Local HTTP service exposing `/v1/chat/completions`, `/debug/route`, and `/health` proxying to 9Router on `http://127.0.0.1:20128/v1` with resilient fallback chaining on 429/5xx errors.

## Capabilities

### New Capabilities
- `router-engine`: Deterministic rule-based model selection with priority ordering (100 -> 10), retry count escalation ladder, and context transforms (`strip_terminal_noise`, `inject_system_prompt`).
- `circuit-breakers`: Non-financial loop detection, diff oscillation monitoring, empty diff rollback guards, iteration/time caps, and terminal human handoff.
- `test-integrity`: Static AST and diff inspection to enforce test read-only boundaries and prevent test tampering (skip, xfail, .only, assertion removal).
- `codebase-distillation`: Dynamic AST/graph distillation for large codebases (>40k tokens) and multi-round raw file on-demand retrieval (`NEED_FILE`).
- `openai-proxy`: Fast local OpenAI-compatible HTTP proxy forwarding requests to 9Router with resilience fallbacks (Opus -> Sonnet, Pro -> Sonnet, Flash -> Pro).

### Modified Capabilities
*(None - fresh project)*

## Impact
- New Python 3.14+ codebase under `src/` using Pydantic v2, FastAPI, and Pytest.
- Integration with local 9Router service on port 20128.
- Agent harness and local tooling can target `http://127.0.0.1:20200/v1` as an OpenAI-compatible gateway.
