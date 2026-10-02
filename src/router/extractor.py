import re
from typing import Any
from src.schemas import RequestMetadata
from src.router.engine import RoutingContext

# Regex patterns for detecting file targets in prompts and conversation
FILE_PATH_REGEX = re.compile(
    r"""(?xi)
    \b(?:
        [a-zA-Z0-9_\-\./\\]+\.(?:py|tsx?|jsx?|prisma|sql|ya?ml|json|toml|rs|go|java|cpp|c|h|sh|cmd|ps1)
        | docker-compose\.ya?ml
        | openapi\.[a-zA-Z0-9]+
        | schema\.[a-zA-Z0-9]+
    )\b
    """
)

# Intent keyword mappings
INTENT_PATTERNS = {
    "system_architecture": re.compile(r"(?i)\b(system[ _\-]architecture|arsitektur[ _\-]sistem|architecture[ _\-]design)\b"),
    "database_migration": re.compile(r"(?i)\b(database[ _\-]migration|migrasi[ _\-]database|db[ _\-]migration|alembic|prisma\s+migrate)\b"),
    "concurrency_design": re.compile(r"(?i)\b(concurrency[ _\-]design|desain[ _\-]konkurensi|race[ _\-]condition|deadlock|concurrency)\b"),
    "auth_protocol": re.compile(r"(?i)\b(auth[ _\-]protocol|protokol[ _\-]auth|jwt|oauth|auth[ _\-]flow|authentication[ _\-]protocol)\b"),
}

TRACEBACK_PATTERN = re.compile(
    r"Traceback \(most recent call last\):.*?(?:\n[a-zA-Z0-9_.]+(?:Error|Exception)?:[^\n]*)",
    re.DOTALL
)
FAILURE_MARKERS = [
    "Traceback (most recent call last):",
    "FAILED tests/",
    "AssertionError",
    "Error:",
    "Exception:",
    "pytest.Pytest",
]

# ── Hermes Agent Activity Detection ──────────────────────────────────
# Skill names that map to agent activities
SKILL_ACTIVITY_MAP: dict[str, str] = {
    "superpowers:brainstorming": "agent_activity_design",
    "superpowers:writing-plans": "agent_activity_design",
    "superpowers:test-driven-development": "agent_activity_tdd",
    "superpowers:systematic-debugging": "agent_activity_debugging",
    "superpowers:requesting-code-review": "agent_activity_code_review",
    "superpowers:receiving-code-review": "agent_activity_code_review",
}

# Tool names that signal specific activities (when no skill overrides)
RESEARCH_TOOLS = {"web_search", "web_extract", "browser_exec"}
DELEGATION_TOOLS = {"delegate_task"}
CODING_TOOLS = {"write_file", "patch", "terminal"}
CODE_REVIEW_TOOLS = {"diff_review"}
DESIGN_TOOLS = {"clarify"}

# Minimum coding tool calls to classify as "coding" activity
CODING_TOOL_THRESHOLD = 3


def _extract_tool_names(messages: list[dict[str, Any]]) -> list[str]:
    """Extract all tool function names from assistant tool_calls in messages."""
    names: list[str] = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        tool_calls = m.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if isinstance(fn, dict) and "name" in fn:
                names.append(fn["name"])
    return names


def _extract_skill_arguments(messages: list[dict[str, Any]]) -> list[str]:
    """Extract skill names from skill_view tool_call arguments."""
    skills: list[str] = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        tool_calls = m.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not isinstance(fn, dict) or fn.get("name") != "skill_view":
                continue
            args_str = fn.get("arguments", "")
            # Quick regex to extract the skill name without json parsing overhead
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', args_str)
            if name_match:
                skills.append(name_match.group(1))
    return skills


def _classify_agent_activity(messages: list[dict[str, Any]]) -> set[str]:
    """Classify Hermes agent activity from tool_calls in messages.

    Returns a set of agent_activity_* intents.

    Priority:
    1. Explicit skill loaded (brainstorming, TDD, debugging, code-review)
    2. Tool pattern heuristic (delegation, research, coding)
    """
    activities: set[str] = set()
    tool_names = _extract_tool_names(messages)
    if not tool_names:
        return activities

    # 1. Check loaded skills
    loaded_skills = _extract_skill_arguments(messages)
    for skill_name in loaded_skills:
        activity = SKILL_ACTIVITY_MAP.get(skill_name)
        if activity:
            activities.add(activity)

    # 2. Check tool-based signals (only if no skill already classified the activity)
    tool_set = set(tool_names)

    if tool_set & DELEGATION_TOOLS:
        activities.add("agent_activity_delegation")

    if tool_set & CODE_REVIEW_TOOLS and "agent_activity_code_review" not in activities:
        activities.add("agent_activity_code_review")

    if tool_set & RESEARCH_TOOLS and not activities - {"agent_activity_delegation"}:
        activities.add("agent_activity_research")

    # Coding heuristic: 3+ coding tools without a higher-level skill
    if not activities:
        coding_count = sum(1 for t in tool_names if t in CODING_TOOLS)
        if coding_count >= CODING_TOOL_THRESHOLD:
            activities.add("agent_activity_coding")

    return activities


def extract_routing_context(body: dict[str, Any]) -> RoutingContext:
    messages: list[dict[str, Any]] = body.get("messages", [])
    raw_meta: dict[str, Any] = body.get("metadata", {})
    existing_meta = RequestMetadata(**raw_meta)

    # 1. Calculate turn count (number of user messages)
    user_turns = sum(1 for m in messages if m.get("role") == "user")
    turn = max(1, user_turns)
    if "turn" in raw_meta:
        turn = existing_meta.turn

    # 2. Estimate total tokens across all messages
    calculated_tokens = 0
    all_content_chunks: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            calculated_tokens += len(content) // 4
            all_content_chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    txt = part["text"]
                    calculated_tokens += len(txt) // 4
                    all_content_chunks.append(txt)

    total_tokens = body.get("total_tokens") or getattr(existing_meta, "total_tokens", None) or calculated_tokens

    # 3. Detect files mentioned
    detected_files = set(existing_meta.files_target)
    for chunk in all_content_chunks:
        for match in FILE_PATH_REGEX.findall(chunk):
            cleaned = match.strip().replace("\\", "/")
            # Filter obvious false positives like version numbers 1.0.0
            if "/" in cleaned or "." in cleaned:
                detected_files.add(cleaned)

    # 4. Detect intents (keyword + agent activity)
    detected_intents = set(existing_meta.intent)
    full_text = "\n".join(all_content_chunks)
    for intent_name, pattern in INTENT_PATTERNS.items():
        if pattern.search(full_text):
            detected_intents.add(intent_name)

    # 4b. Detect Hermes agent activity from tool_calls
    detected_intents |= _classify_agent_activity(messages)

    # 5. Extract traceback and retry count
    last_traceback = existing_meta.last_traceback
    traceback_matches = TRACEBACK_PATTERN.findall(full_text)
    if traceback_matches and not last_traceback:
        last_traceback = traceback_matches[-1].strip()

    # Calculate retries by looking for failure signals in user/tool messages
    error_occurrences = 0
    for m in messages:
        if m.get("role") in ("user", "tool"):
            c = m.get("content", "")
            if isinstance(c, str) and any(marker in c for marker in FAILURE_MARKERS):
                error_occurrences += 1

    retry_count = max(existing_meta.retry_count, error_occurrences)

    # Construct unified RequestMetadata
    merged_metadata = RequestMetadata(
        subtask_id=existing_meta.subtask_id,
        session_id=existing_meta.session_id,
        turn=turn,
        files_target=list(detected_files),
        retry_count=retry_count,
        intent=list(detected_intents),
        last_traceback=last_traceback,
        last_diff=existing_meta.last_diff,
    )

    return RoutingContext(
        metadata=merged_metadata,
        total_tokens=total_tokens,
        messages=messages,
        request=body,
    )
