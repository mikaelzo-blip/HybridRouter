# Spec: true-sse-streaming

## Purpose
Provide true zero-buffering Server-Sent Events (SSE) streaming proxying from upstream 9Router to downstream AI agent clients.

## ADDED Requirements

### Requirement: Zero-Buffering SSE Stream Forwarding
When the client request specifies `"stream": true`, the proxy MUST establish a live HTTP stream with the upstream provider and forward byte chunks or SSE events immediately to the client without buffering the full response.

#### Scenario: Streaming chat completion chunk propagation
- **WHEN** a client submits a chat completion request with `"stream": true`
- **THEN** the proxy SHALL yield SSE chunks (`data: ...\n\n`) in real time as emitted by the upstream provider

### Requirement: Pre-Stream Resilience Fallback
If the upstream provider returns an error (429, 502, 503, 504) before any stream data chunk has been emitted to the client, the proxy MUST fail over to the designated fallback model.

#### Scenario: Pre-stream failover on upstream 429
- **WHEN** the primary streaming model responds with HTTP 429 before emitting any data chunks
- **THEN** the proxy SHALL catch the error and initiate a streaming connection to the fallback model in the resilience chain
