import ast
import fnmatch
import re
from pydantic import BaseModel


class PatchValidationResult(BaseModel):
    approved: bool
    reason: str | None = None
    details: str | None = None


TEST_FILE_PATTERNS = [
    "tests/**",
    "test/**",
    "*.test.*",
    "*_test.*",
    "*test*.py",
    "pytest.ini",
    "vitest.config.*",
    "jest.config.*",
]

PROHIBITED_TEST_TAMPERING_PATTERNS = [
    re.compile(r"@pytest\.mark\.skip"),
    re.compile(r"@pytest\.mark\.xfail"),
    re.compile(r"pytest\.skip\("),
    re.compile(r"\.skip\("),
    re.compile(r"\.only\("),
    re.compile(r"--passWithNoTests"),
]


def is_test_file(path: str) -> bool:
    norm_path = path.replace("\\", "/")
    for pattern in TEST_FILE_PATTERNS:
        norm_pat = pattern.replace("\\", "/")
        if norm_pat.endswith("/**"):
            prefix = norm_pat[:-3]
            if norm_path == prefix or norm_path.startswith(prefix + "/"):
                return True
        elif fnmatch.fnmatch(norm_path, norm_pat):
            return True
        # Also check if any component of path is tests or test
        parts = norm_path.split("/")
        if "tests" in parts or "test" in parts:
            return True
    return False


def count_python_assertions(code: str) -> int:
    try:
        tree = ast.parse(code)
        return sum(1 for node in ast.walk(tree) if isinstance(node, ast.Assert))
    except Exception:
        # Fallback to regex if syntax error in partial code
        return len(re.findall(r"\bassert\b", code))


class TestIntegrityGuard:
    def validate_patch(
        self,
        tier: str,
        file_path: str,
        original_content: str,
        new_content: str
    ) -> PatchValidationResult:
        tier_clean = tier.strip().lower()
        test_file = is_test_file(file_path)

        # 1. Tier 3 (Flash) read-only enforcement
        if test_file and tier_clean in ("flash", "gemini_executor", "tier3"):
            return PatchValidationResult(
                approved=False,
                reason="TEST_FILE_READ_ONLY_FOR_TIER3",
                details=f"Tier 3 ({tier}) is prohibited from modifying test file '{file_path}'"
            )

        # 2. Check for test tampering markers across all tiers
        if test_file:
            for pat in PROHIBITED_TEST_TAMPERING_PATTERNS:
                m = pat.search(new_content)
                if m:
                    matched_str = m.group(0)
                    # Only reject if not present in original content
                    if not pat.search(original_content):
                        return PatchValidationResult(
                            approved=False,
                            reason="TEST_TAMPERING_DETECTED",
                            details=f"Detected tampering token '{matched_str}' in '{file_path}'"
                        )

            # 3. Python assertion reduction check
            if file_path.endswith(".py"):
                orig_asserts = count_python_assertions(original_content)
                new_asserts = count_python_assertions(new_content)
                if new_asserts < orig_asserts:
                    return PatchValidationResult(
                        approved=False,
                        reason="ASSERTION_COUNT_REDUCED",
                        details=f"Assertion count reduced from {orig_asserts} to {new_asserts} in '{file_path}'"
                    )

        return PatchValidationResult(approved=True)
