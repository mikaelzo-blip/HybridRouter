"""Pre-merge architecture & quality review via Claude Opus.

Usage:
    uv run python scripts/opus_review.py [--base main] [--output docs/reviews/]

Collects git diff, loads domain rules from the repo (constitution, AGENTS.md),
and sends one Opus call through the HybridRouter for a structured review report.
"""

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

import httpx

ROUTER_URL = os.getenv("HYBRID_ROUTER_URL", "http://127.0.0.1:20250")
# Domain rule files to discover and inject as context (relative to repo root)
DOMAIN_RULE_CANDIDATES = [
    ".specify/memory/constitution.md",
    "AGENTS.md",
]
# Also scan these globs for additional domain docs
DOMAIN_RULE_GLOBS = [
    "docs/superpowers/specs/*.md",
    "specs/*/spec.md",
]


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def collect_diff(base: str) -> str:
    # Check diff against base branch (includes committed and working tree changes)
    diff = run_git("diff", base, "--stat")
    full_diff = run_git("diff", base)
    
    if not full_diff:
        # Fallback to local unstaged/staged diff if on base branch
        diff = run_git("diff", "HEAD", "--stat")
        full_diff = run_git("diff", "HEAD")

    if not full_diff:
        print(f"No diff found against {base} or HEAD.", file=sys.stderr)
        sys.exit(0)
    return f"## Diff Summary\n\n```\n{diff}\n```\n\n## Full Diff\n\n```diff\n{full_diff}\n```"


def collect_domain_rules(repo_root: Path) -> str:
    sections: list[str] = []
    for candidate in DOMAIN_RULE_CANDIDATES:
        p = repo_root / candidate
        if p.exists():
            content = p.read_text(encoding="utf-8", errors="replace")
            sections.append(f"### {candidate}\n\n{content}")

    # Skip glob scanning if we already have substantial rules
    # (avoids token bloat from dozens of spec files)
    if len(sections) < 2:
        import glob
        for pattern in DOMAIN_RULE_GLOBS:
            for match in sorted(glob.glob(str(repo_root / pattern)))[:3]:
                rel = os.path.relpath(match, repo_root)
                content = Path(match).read_text(encoding="utf-8", errors="replace")
                if len(content) < 8000:
                    sections.append(f"### {rel}\n\n{content}")

    if not sections:
        return "(No domain rule files found in repository.)"
    return "\n\n---\n\n".join(sections)


SYSTEM_PROMPT = """\
You are a senior architect performing a pre-merge review. Analyze the diff \
against the project's domain rules and architecture blueprint.

Produce a structured markdown report with these sections:

## 1. Architecture Assessment
- Layer boundary violations (router vs upstream vs proxy vs guard)
- Dependency direction issues
- File structure alignment with blueprint

## 2. Code Quality
- Error handling gaps
- Missing edge cases
- Code smells or unnecessary complexity
- Test coverage gaps (files changed but no corresponding test changes)

## 3. Domain Rule Compliance
- Check every applicable domain invariant from the constitution/AGENTS.md
- Flag any violation or potential violation with the rule number/name

## 4. Security & Safety
- Input validation at trust boundaries
- Secret/credential handling
- Destructive operation guards

## 5. Verdict
- PASS / PASS WITH NOTES / FAIL
- Summary of critical findings (if any)
- Recommended actions before merge

Be specific: cite file paths and line ranges. Do not pad with praise. \
If everything is clean, say so briefly.
"""


def call_opus(diff_text: str, domain_rules: str) -> str:
    user_content = (
        f"# Domain Rules\n\n{domain_rules}\n\n"
        f"---\n\n# Changes to Review\n\n{diff_text}"
    )

    # Truncate if too large (Opus context is generous but diff can be huge)
    max_chars = 180_000
    if len(user_content) > max_chars:
        user_content = user_content[:max_chars] + "\n\n[... truncated ...]"

    payload = {
        "model": "opus_apex",
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }

    print(f"Calling Claude Review via {ROUTER_URL} ...", file=sys.stderr)
    with httpx.Client(timeout=300) as client:
        resp = client.post(f"{ROUTER_URL}/v1/chat/completions", json=payload)
        # Automatic fallback to Sonnet if Opus is rate limited / out of quota
        if resp.status_code in (429, 502, 503):
            print(f"Opus unavailable ({resp.status_code}). Falling back to Sonnet...", file=sys.stderr)
            payload["model"] = "sonnet_fallback"
            resp = client.post(f"{ROUTER_URL}/v1/chat/completions", json=payload)

    if resp.status_code != 200:
        print(f"Claude review call failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        print("No response from Opus.", file=sys.stderr)
        sys.exit(1)

    content = choices[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})
    print(
        f"Done. Tokens: {usage.get('prompt_tokens', '?')} in, "
        f"{usage.get('completion_tokens', '?')} out.",
        file=sys.stderr,
    )
    return content


def main():
    parser = argparse.ArgumentParser(description="Pre-merge Opus review")
    parser.add_argument("--base", default="main", help="Base branch (default: main)")
    parser.add_argument("--output", default="docs/reviews", help="Output directory")
    parser.add_argument("--repo", default=".", help="Repository root")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    os.chdir(repo_root)

    branch = run_git("branch", "--show-current")
    print(f"Reviewing {branch} against {args.base}", file=sys.stderr)

    diff_text = collect_diff(args.base)
    domain_rules = collect_domain_rules(repo_root)

    report = call_opus(diff_text, domain_rules)

    # Write report
    date_str = datetime.date.today().isoformat()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = branch.replace("/", "-").replace("\\", "-")
    out_path = out_dir / f"{date_str}-{slug}-review.md"

    header = (
        f"# Pre-Merge Review: `{branch}`\n\n"
        f"- **Date**: {date_str}\n"
        f"- **Base**: `{args.base}`\n"
        f"- **Reviewer**: Claude Opus (automated)\n\n---\n\n"
    )
    out_path.write_text(header + report, encoding="utf-8")
    print(f"\nReport saved: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
