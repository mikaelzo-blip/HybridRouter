# Tasks

## 1. Project Scaffolding & Configuration Foundation

- [x] 1.1 Create `pyproject.toml` with dependencies (`fastapi`, `uvicorn`, `pydantic-settings`, `httpx`, `pyyaml`, `pytest`, `pytest-asyncio`) and verify environment sync with `uv sync`
- [x] 1.2 Write unit tests in `tests/test_config.py` for settings loader and environment variable placeholder resolution (`${ENV_VAR}`)
- [x] 1.3 Implement configuration loader and Pydantic schemas in `src/config.py` and `src/schemas.py`, verifying tests pass with `uv run pytest tests/test_config.py`
- [x] 1.4 Create `config/9router-production.yaml` with verified Antigravity model IDs and escalation parameters

## 2. Router Engine & Escalation Ladder

- [x] 2.1 Write unit tests in `tests/test_router_engine.py` covering priority order (100 -> 10), retry count escalation ladder, and context transforms (`strip_terminal_noise`, `inject_system_prompt`)
- [x] 2.2 Implement rule evaluation engine and context transforms in `src/router/engine.py` and `src/router/transforms.py`
- [x] 2.3 Verify router engine tests pass with `uv run pytest tests/test_router_engine.py`

## 3. Circuit Breakers & Loop Protection

- [x] 3.1 Write unit tests in `tests/test_circuit_breakers.py` for normalized traceback signatures, diff oscillation window of 6, consecutive empty diffs (3x rollback), subtask iteration limits (12 iterations / 30 mins), and Opus exhaustion (2 failures -> handoff)
- [x] 3.2 Implement traceback normalizer in `src/breakers/normalizer.py` and circuit breaker state tracker in `src/breakers/circuit.py`
- [x] 3.3 Verify circuit breaker tests pass with `uv run pytest tests/test_circuit_breakers.py`

## 4. Test Integrity Guard

- [x] 4.1 Write unit tests in `tests/test_test_integrity.py` testing rejection of Tier 3 edits on test files, detection of `@pytest.mark.skip`, `@pytest.mark.xfail`, `.skip(`, `.only(`, and detection of assertion statement count reduction
- [x] 4.2 Implement AST and pattern-based test guard in `src/guard/test_integrity.py`
- [x] 4.3 Verify test integrity tests pass with `uv run pytest tests/test_test_integrity.py`

## 5. Codebase Distillation Pipeline

- [x] 5.1 Write unit tests in `tests/test_distiller.py` for token estimation, 40,000 token threshold triggering, structural context extraction prompt formatting, and multi-round `NEED_FILE: <path>` feedback loop (up to 3 rounds)
- [x] 5.2 Implement dynamic distillation engine in `src/distiller/dynamic_distiller.py`
- [x] 5.3 Verify distiller tests pass with `uv run pytest tests/test_distiller.py`

## 6. OpenAI-Compatible HTTP Proxy Server

- [x] 6.1 Write integration tests in `tests/test_server.py` for `/health`, `/v1/models`, `/debug/route`, `/v1/chat/completions`, and fallback chaining (Opus -> Sonnet, Pro -> Sonnet, Flash -> Pro)
- [x] 6.2 Implement FastAPI server and 9Router upstream forwarder in `src/server/app.py` and `src/server/upstream.py`
- [x] 6.3 Verify proxy server tests pass with `uv run pytest tests/test_server.py`

## 7. Full Suite Regression & End-to-End Quality Gate

- [x] 7.1 Execute full test suite with `uv run pytest` and verify all tests pass without skipping
- [x] 7.2 Run live `/debug/route` simulation against local 9Router instance at `http://127.0.0.1:20128/v1` and verify end-to-end routing decision logging
