# Spec Delta: router-engine

## Purpose

Enforces deterministic multi-model task routing and retry-driven escalation across Claude Opus 4.6, Gemini 3.1 Pro, and Gemini 3.8 Flash.

## ADDED Requirements

### Requirement: Priority-Based Rule Evaluation
The router engine MUST evaluate candidate routing rules strictly in descending order of priority, from priority 100 down to priority 10, executing the target of the first matching condition.

#### Scenario: Rule selection matches highest priority first
- **WHEN** incoming request context matches both a priority 100 rule and a priority 70 rule
- **THEN** the router engine SHALL select the target model associated with the priority 100 rule

### Requirement: Retry Count Escalation Ladder
The router engine MUST maintain a single source of truth for subtask escalation based on consecutive failure count (`retry_count`).

#### Scenario: Tier default on low retry count
- **WHEN** `retry_count` is between 0 and 2
- **THEN** the router engine SHALL route to the tier default model (Gemini 3.8 Flash, or Gemini 3.1 Pro for core backend files)

#### Scenario: Logic patching escalation on three retries
- **WHEN** `retry_count` equals 3
- **THEN** the router engine SHALL route to Gemini 3.1 Pro for tactical logic patching

#### Scenario: Architectural rescue escalation on four or more retries
- **WHEN** `retry_count` is greater than or equal to 4
- **THEN** the router engine SHALL route to Claude Opus 4.6 for architectural rescue

### Requirement: Escalation Priority Invariant
Rules conditioned on `retry_count` MUST have higher priority than rules conditioned solely on file path patterns or general intent.

#### Scenario: Retry rule supersedes backend file rule
- **WHEN** target files match `src/services/**` and `retry_count` is 4
- **THEN** the router engine SHALL route to Claude Opus 4.6 (`rescue_opus`, priority 100) instead of Gemini 3.1 Pro (`backend_core`, priority 70)

### Requirement: Context Transforms
When an escalation rule matches, the router engine MUST apply configured context transformations before dispatching to upstream.

#### Scenario: Terminal noise truncation
- **WHEN** rule `rescue_opus` is triggered
- **THEN** the router engine SHALL retain only the last 40 lines of terminal output and inject the architectural audit system prompt

#### Scenario: L1 Pro patch context transform
- **WHEN** rule `l1_pro_patch` is triggered
- **THEN** the router engine SHALL retain only the last 60 lines of terminal output
