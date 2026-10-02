# Spec Delta: codebase-distillation

## Purpose

Extracts compact structural architecture maps from large codebases to provide high-leverage context for architectural reasoning within token budgets.

## ADDED Requirements

### Requirement: Token-Gated Ingestion
The codebase distillation pipeline MUST trigger whenever estimated repository context tokens exceed the 40,000 token threshold.

#### Scenario: Small repository bypasses distillation
- **WHEN** total estimated context tokens are less than or equal to 40,000
- **THEN** the pipeline SHALL pass raw file contents directly to Tier 0 / Tier 2 without distillation

#### Scenario: Large repository triggers structural distillation
- **WHEN** total estimated context tokens exceed 40,000
- **THEN** the pipeline SHALL invoke Gemini 3.1 Pro to distill the repository into an architectural map under 12,000 tokens

### Requirement: Structural Context Extraction
The distillation engine MUST extract strictly structural context (interfaces, types, DB schemas, function signatures, controller routes, and dependency maps), omitting boilerplate implementation and styling.

#### Scenario: Distillation output contains required elements
- **WHEN** distillation is performed on a codebase
- **THEN** the resulting summary SHALL contain at most 1,500 lines and include the explicit list of summarized file paths

### Requirement: On-Demand Raw File Fetch Loop
When Claude Opus 4.6 evaluates the architectural context and requests specific raw files, the pipeline MUST resolve and provide those files over multiple rounds.

#### Scenario: Opus requests additional raw files via NEED_FILE
- **WHEN** Opus returns one or more lines matching `NEED_FILE: <path>`
- **THEN** the pipeline SHALL append the exact contents of the requested files and re-prompt Opus, up to a maximum of 3 rounds

#### Scenario: Exceeding maximum fetch rounds
- **WHEN** Opus continues to request raw files after 3 feedback rounds
- **THEN** the pipeline SHALL return the latest response without further file retrieval
