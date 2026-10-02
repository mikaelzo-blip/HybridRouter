# Design: Hybrid Autorouter 3-Tier

## Context

Coding agents need to route LLM queries across different tiers according to task hardness, failure repetition, and codebase size. The system interfaces with a local 9Router instance at `http://127.0.0.1:20128/v1` which hosts three OAuth-authenticated Antigravity accounts with verified quotas (1,000 requests per 5h window across Gemini and Claude & GPT models). See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- Provide a unified Python 3.14+ proxy and routing runtime implementing the 3-Tier topology (Tier 0: Claude Opus 4.6, Tier 1/2: Gemini 3.1 Pro, Tier 3: Gemini 3.8 Flash).
- Ensure strict escalation precedence: retry-based rules supersede file-based rules.
- Implement non-financial circular failure circuit breakers (traceback loop, diff oscillation, empty diff rollback, iteration/time bounds, Opus exhaustion handoff).
- Guard test integrity via AST inspection, preventing Tier 3 test modifications and anti-tampering (skip/xfail/.only/assertion drops).
- Implement dynamic distillation (>40k token gate) and interactive `NEED_FILE` feedback loop.
- Deliver an OpenAI-compatible HTTP server (`/v1/chat/completions`, `/debug/route`, `/health`).

**Non-Goals:**
- Financial or monetary budget tracking (system is non-financial; convergence, loop breakers, and test pass/fail control termination).
- Modifying 9Router internals or Antigravity OAuth tokens directly.
- Multi-tenant cloud SaaS deployment (this is a personal/developer local runtime on `127.0.0.1`).

## Decisions

### Decision 1: Python-Native Stack with FastAPI, Pydantic v2, and Pytest
- **Selected**: Python 3.14 + FastAPI + Pydantic v2 + uv + Pytest.
- **Alternatives Considered**:
  - TypeScript/Fastify (auto-router-v2): Rejected because the distillation pipeline (`dynamic_distiller.py`) and AST test inspection are written in Python; splitting across runtimes creates IPC latency and scattered state.
  - Subprocess CLI only: Rejected because client coding agents expect a standard OpenAI HTTP endpoint.
- **Rationale**: Python provides native AST parsing for test tampering, native regex/string diff analysis, and direct compatibility with `dynamic_distiller.py`.

### Decision 2: Rule Priority Ordering (100 -> 10)
- **Selected**: Hard-ordered rule priorities:
  - 100: `rescue_opus` (`retry_count >= 4`) -> `opus_apex`
  - 95: `l1_pro_patch` (`retry_count == 3`) -> `gemini_tactical`
  - 90: `ingest_then_design` (`turn == 1 and total_tokens > 40000` + arch files/intent) -> pipeline `[distill, design]`
  - 85: `ingest_only` (`turn == 1 and total_tokens > 40000`) -> pipeline `[distill, plan]`
  - 80: `apex_design` (`turn == 1` + arch files/intent) -> `opus_apex`
  - 70: `backend_core` (`files.target` matching core backend) -> `gemini_tactical`
  - 10: `default_worker` (default fallback) -> `gemini_executor`
- **Alternatives Considered**: Score-based or weighted routing: Rejected because deterministic priority guarantees retry escalation can never be overridden by file patterns.

### Decision 3: Upstream Model Identifiers with Environment Variable Fallbacks
- **Selected**: Exact IDs observed in live 9Router with `${ENV_VAR}` configuration:
  - Opus: `ag/claude-opus-4-6-thinking` (`${OPUS_4_6_MODEL_ID}`)
  - Sonnet (Fallback): `ag/claude-sonnet-4-6` (`${SONNET_4_6_MODEL_ID}`)
  - Gemini 3.1 Pro: `ag/gemini-3.1-pro-low` (`${GEMINI_3_1_PRO_MODEL_ID}`)
  - Gemini 3.8 Flash: `ag/gemini-3.8-flash` (`${GEMINI_3_8_FLASH_MODEL_ID}`)
- **Rationale**: Complies with Hard Rule 1: No made-up IDs; verified live against `http://127.0.0.1:20128/v1/models`.

### Decision 4: Deterministic Normalization for Traceback Circuit Breaker
- **Selected**: Strip volatile substrings using regex:
  - File paths: normalize absolute paths to base filenames or `<path>`
  - Memory addresses: `0x[0-9a-fA-F]+` -> `<mem_addr>`
  - Timestamps: ISO dates/times -> `<timestamp>`
  - Line numbers in tracebacks can be preserved or normalized depending on frame.
- **Rationale**: Identical logical errors across runs produce different absolute strings due to temp dirs or timestamps; normalization allows true loop detection.

### Decision 5: Test Tampering Defense via Python AST and Diff Scanner
- **Selected**: Multi-language static check:
  - For Python: parse AST to verify test count and assertion node counts do not decrease; detect decorators (`pytest.mark.skip`, `pytest.mark.xfail`).
  - For JS/TS: regex patterns checking `.skip(`, `.only(`, and runner suppression flags.
  - File path gating: reject any patch from Tier 3 targeting `tests/`, `test/`, `*test*`, or configuration.

## Risks / Trade-offs

- **[Risk] High token consumption on large repos** → **Mitigation**: Structural distillation compresses context below 12k tokens before passing to Opus 4.6.
- **[Risk] Upstream 429 rate limit across Antigravity quotas** → **Mitigation**: Exponential backoff with jitter and fallback chain (`Opus -> Sonnet`, `Pro -> Sonnet`, `Flash -> Pro`).
- **[Risk] False positive test tampering on legitimate test refactoring** → **Mitigation**: Tier 2 (Gemini 3.1 Pro) and Tier 0 (Opus 4.6) are authorized to modify tests; only Tier 3 (Flash) is blocked.
- **[Risk] Infinite loops across oscillating diffs** → **Mitigation**: Circuit breaker tracks a 6-iteration rolling hash window and forces escalation on repetition.

## Migration Plan

1. Initialize project with `pyproject.toml` and dependencies (`fastapi`, `uvicorn`, `pydantic-settings`, `httpx`, `pyyaml`).
2. Implement components in modular stages via strict TDD (tests first).
3. Verify `/v1/models` and `/debug/route` against simulated and live 9Router payloads.
4. Point coding agents to `http://127.0.0.1:20200/v1` as the virtual OpenAI provider.

## Open Questions

None. All model IDs, endpoints, escalation rules, and circuit breaker constraints are specified in `docs/blueprint-v2.md` and verified against the live 9Router instance.
