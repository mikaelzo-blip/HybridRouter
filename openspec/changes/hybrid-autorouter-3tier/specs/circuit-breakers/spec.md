# Spec Delta: circuit-breakers

## Purpose

Provides non-financial circular failure detection, loop mitigation, iteration bounds, and terminal human handoff for coding agent subtasks.

## ADDED Requirements

### Requirement: Normalized Traceback Loop Breaker
The circuit breaker MUST compute a normalized error signature by stripping volatile elements including timestamps, absolute directory prefixes, and memory hex addresses from failure outputs.

#### Scenario: Three identical normalized tracebacks force escalation
- **WHEN** the same normalized error signature is observed consecutively 3 times on the same subtask
- **THEN** the system SHALL prohibit further patches at the current tier and force escalate one tier immediately

### Requirement: Diff Oscillation Detection
The circuit breaker MUST maintain a rolling window of recent diff hashes (window size: 6) to detect alternating patch churn.

#### Scenario: Repeating diff cycle triggers tier escalation
- **WHEN** diff hashes within a window of 6 iterations exhibit an A-B-A oscillation pattern
- **THEN** the system SHALL force escalate one tier immediately

### Requirement: Empty Diff Guard
The circuit breaker MUST monitor consecutive iterations that generate zero net line changes.

#### Scenario: Three consecutive empty diffs trigger rollback and escalation
- **WHEN** an agent produces 3 consecutive empty diffs
- **THEN** the system SHALL rollback the workspace to the last clean commit and escalate one tier

### Requirement: Subtask Iteration and Wall-Clock Limits
The circuit breaker MUST enforce deterministic ceilings of at most 12 iterations or 30 wall-clock minutes per subtask.

#### Scenario: Subtask iteration ceiling exceeded
- **WHEN** an individual subtask reaches 12 iterations without passing tests
- **THEN** the system SHALL force escalate one tier immediately

### Requirement: Opus Exhaustion and Human Handoff
When Claude Opus 4.6 is engaged in architectural rescue, the circuit breaker MUST cap consecutive failed attempts at exactly 2 before terminating.

#### Scenario: Second consecutive Opus failure initiates human handoff
- **WHEN** Claude Opus 4.6 fails its second consecutive rescue attempt on a subtask
- **THEN** the system SHALL halt automatic iteration, preserve the current working branch, save the failure logs, and emit a structured handoff summary for human intervention
