# Spec Delta: test-integrity

## Purpose

Enforces static and behavioral safeguards to prevent coding agents from tampering with test definitions, assertions, or test runners.

## ADDED Requirements

### Requirement: Test File Read-Only Boundary for Tier 3
The test integrity guard MUST strictly prohibit Tier 3 (Gemini 3.8 Flash) from creating, modifying, or deleting test files.

#### Scenario: Tier 3 attempts to modify test files
- **WHEN** an agent at Tier 3 submits a patch touching paths matching `tests/**`, `*.test.*`, `*_test.*`, or test runner configs
- **THEN** the test integrity guard SHALL reject the patch and force escalate the subtask to Tier 2 (Gemini 3.1 Pro)

### Requirement: Anti-Tampering Inspection
The test integrity guard MUST inspect incoming patches and code modifications using AST and pattern analysis to detect test suppression or assertion removal.

#### Scenario: Patch introduces test skip or xfail
- **WHEN** a patch adds `@pytest.mark.skip`, `@pytest.mark.xfail`, `.skip(`, `.only(`, or `--passWithNoTests`
- **THEN** the test integrity guard SHALL reject the patch with reason `TEST_TAMPERING_DETECTED` and escalate one tier

#### Scenario: Patch removes assertions
- **WHEN** a patch reduces the number of active assertion statements within a test file
- **THEN** the test integrity guard SHALL reject the patch and preserve the original test suite

### Requirement: Comprehensive PASS Verification
The evaluation engine MUST verify that a subtask PASS condition satisfies test execution, typecheck, lint, and build without decreasing test count or code coverage.

#### Scenario: Partial pass is rejected
- **WHEN** test execution succeeds but typecheck or linting fails
- **THEN** the system SHALL treat the result as a failure, increment `retry_count`, and invoke the appropriate tier handler
