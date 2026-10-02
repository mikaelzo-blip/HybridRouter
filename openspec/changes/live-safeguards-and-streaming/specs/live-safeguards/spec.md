# Spec: live-safeguards

## Purpose
Integrate `CircuitBreakerTracker` and `TestIntegrityGuard` directly into the live HTTP request lifecycle to enforce safety invariants during active agent runs.

## ADDED Requirements

### Requirement: Live Circuit Breaker Enforcement
The proxy service MUST inspect request metadata on `/v1/chat/completions`, track loop states (normalized tracebacks, diff oscillation, consecutive empty diffs), and escalate target tiers automatically when thresholds are reached.

#### Scenario: Identical normalized traceback triggers tier escalation
- **WHEN** a client submits 3 requests with identical normalized tracebacks
- **THEN** the proxy SHALL force an escalation of the routing tier by at least one tier level

#### Scenario: Consecutive empty diffs trigger rollback guard
- **WHEN** a client submits 3 consecutive requests resulting in empty diffs
- **THEN** the proxy SHALL flag rollback requirement and escalate the tier

#### Scenario: Circuit breaker status inspection
- **WHEN** a client sends a GET request to `/breakers/status`
- **THEN** the service SHALL return the active circuit breaker state and trip counts for the active session

### Requirement: Live Test Integrity Guard
The proxy service MUST inspect candidate patch payloads or file target lists and reject modifications to test files if attempted by Tier 3 (Flash).

#### Scenario: Rejection of test file edits by Tier 3
- **WHEN** a request targeting Tier 3 (`gemini_executor`) attempts to touch files under `tests/**`
- **THEN** the proxy SHALL reject the request with HTTP 403 Forbidden and force tier escalation
