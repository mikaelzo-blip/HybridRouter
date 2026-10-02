# Spec Delta: openai-proxy

## Purpose

Exposes a fast, compliant OpenAI HTTP proxy interface that routes requests to local 9Router endpoints with resilient fallback chains.

## ADDED Requirements

### Requirement: OpenAI Compatible Endpoints
The proxy service MUST expose standard OpenAI-compatible endpoints `/v1/chat/completions` and `/v1/models`, as well as a `/debug/route` inspection endpoint and `/health`.

#### Scenario: Routing inspection via debug endpoint
- **WHEN** a client sends a POST request to `/debug/route` with prompt messages and metadata
- **THEN** the proxy SHALL return the resolved upstream model, matched rule, and planned transforms without invoking the upstream model

#### Scenario: Chat completion request forwarding
- **WHEN** a client sends a POST request to `/v1/chat/completions`
- **THEN** the proxy SHALL apply rule-based model resolution, transform context, forward the payload to 9Router, and stream or return the completion

### Requirement: Upstream Resilience and Fallback Chaining
When the upstream provider returns transient failure codes (HTTP 429, 502, 503, 504), the proxy MUST execute exponential backoff with jitter and follow designated fallback chains.

#### Scenario: Opus upstream failure falls back to Sonnet
- **WHEN** a call to `opus_apex` returns a 429 or 5xx status after retry backoff exhaustion
- **THEN** the proxy SHALL dispatch the request to `sonnet_fallback` (`ag/claude-sonnet-4-6`)

#### Scenario: Tactical upstream failure falls back to Sonnet
- **WHEN** a call to `gemini_tactical` fails with a transient 5xx error
- **THEN** the proxy SHALL dispatch the request to `sonnet_fallback`

#### Scenario: Executor upstream failure falls back to Tactical
- **WHEN** a call to `gemini_executor` fails with a transient 5xx error
- **THEN** the proxy SHALL dispatch the request to `gemini_tactical`

### Requirement: Non-Financial Spending Observability
The proxy MUST record per-request token usage, latency, and routing decisions in structured logs, alerting when a subtask token consumption deviates significantly (anomaly threshold p99 x 3).

#### Scenario: Structured decision logging
- **WHEN** any routing decision is made
- **THEN** the proxy SHALL emit a structured log containing `rule_name`, `target_model`, `retry_count`, `latency_ms`, and `escalation_reason`
