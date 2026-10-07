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
    "test_*.py",
    "*_test.py",
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


_DIFF_PATH_HEADER = re.compile(r"^(?:\+\+\+|---)\s+(?:[ab]/)?(\S.*?)\s*$")
_GIT_DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)")


def split_diff_by_file(diff_text: str) -> list[tuple[str | None, list[str], list[str]]]:
    """Split a unified-ish diff into (path, added_lines, removed_lines) per file.

    A diff without any file header yields a single section with path None.
    """
    sections: list[tuple[str | None, list[str], list[str]]] = []
    path: str | None = None
    added: list[str] = []
    removed: list[str] = []
    started = False

    def flush() -> None:
        if started or added or removed:
            sections.append((path, added, removed))

    for line in diff_text.splitlines():
        git_m = _GIT_DIFF_HEADER.match(line)
        if git_m:
            flush()
            path, added, removed, started = git_m.group(2), [], [], True
            continue
        if line.startswith("--- "):
            hdr = _DIFF_PATH_HEADER.match(line)
            new_path = hdr.group(1) if hdr else None
            # "--- path" opens a new file section unless it follows a `diff --git` header
            if not (started and not added and not removed and path is not None):
                flush()
                added, removed = [], []
            path = None if new_path == "/dev/null" else new_path
            started = True
            continue
        if line.startswith("+++ "):
            hdr = _DIFF_PATH_HEADER.match(line)
            if hdr and hdr.group(1) != "/dev/null":
                path = hdr.group(1)
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    flush()
    return sections


class TestIntegrityGuard:
    def validate_diff(self, diff_text: str, fallback_paths: list[str] | None = None) -> PatchValidationResult:
        """Inspect an (already produced) diff for test tampering, regardless of tier.

        Only lines the diff ADDS are suspect: removing a `@pytest.mark.skip` is a fix,
        not tampering. Sections whose file is not a test file are ignored. Diffs with
        no file header are attributed to `fallback_paths` (e.g. request files_target).
        """
        for path, added, removed in split_diff_by_file(diff_text):
            paths = [path] if path else list(fallback_paths or [])
            test_paths = [p for p in paths if is_test_file(p)]
            if not test_paths:
                continue
            target = test_paths[0]
            added_text = "\n".join(added)
            removed_text = "\n".join(removed)

            for pat in PROHIBITED_TEST_TAMPERING_PATTERNS:
                m = pat.search(added_text)
                if m and not pat.search(removed_text):
                    return PatchValidationResult(
                        approved=False,
                        reason="TEST_TAMPERING_DETECTED",
                        details=f"Detected tampering token '{m.group(0)}' in '{target}'"
                    )

            if target.endswith(".py"):
                removed_asserts = len(re.findall(r"\bassert\b", removed_text))
                added_asserts = len(re.findall(r"\bassert\b", added_text))
                if added_asserts < removed_asserts:
                    return PatchValidationResult(
                        approved=False,
                        reason="ASSERTION_COUNT_REDUCED",
                        details=f"Assertion count reduced by {removed_asserts - added_asserts} in '{target}'"
                    )

        return PatchValidationResult(approved=True)

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
