import pytest
from src.router.extractor import extract_routing_context
from src.config import load_router_config
from src.router.engine import RouterEngine


@pytest.fixture
def router_engine():
    cfg = load_router_config("config/9router-production.yaml")
    return RouterEngine(cfg)


def test_extract_turn_and_tokens():
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello world!"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "Can you help me?"},
    ]
    ctx = extract_routing_context({"messages": messages})
    assert ctx.metadata.turn == 2
    assert ctx.total_tokens > 0
    assert ctx.total_tokens == (len("You are an assistant.") + len("Hello world!") + len("Hi there!") + len("Can you help me?")) // 4


def test_extract_files_target():
    messages = [
        {
            "role": "user",
            "content": "Please check src/services/auth.py and update schema.prisma along with tests/test_auth.py.",
        }
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "src/services/auth.py" in ctx.metadata.files_target
    assert "schema.prisma" in ctx.metadata.files_target
    assert "tests/test_auth.py" in ctx.metadata.files_target


def test_extract_intents():
    messages = [
        {
            "role": "user",
            "content": "Saya ingin merancang system architecture dan database migration untuk sistem ini.",
        }
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "system_architecture" in ctx.metadata.intent
    assert "database_migration" in ctx.metadata.intent


def test_extract_tracebacks_and_retries():
    traceback_sample = """
Traceback (most recent call last):
  File "src/main.py", line 12, in <module>
    run_app()
ZeroDivisionError: division by zero
"""
    messages = [
        {"role": "user", "content": "Run tests"},
        {"role": "assistant", "content": "Ran test."},
        {"role": "user", "content": f"Test failed with error:\n{traceback_sample}"},
        {"role": "assistant", "content": "Fixing..."},
        {"role": "user", "content": f"Still failing:\n{traceback_sample}"},
        {"role": "assistant", "content": "Fixing again..."},
        {"role": "user", "content": f"Another failure:\n{traceback_sample}"},
        {"role": "assistant", "content": "Fixing again..."},
        {"role": "user", "content": f"Still failing:\n{traceback_sample}"},
    ]
    ctx = extract_routing_context({"messages": messages})
    assert ctx.metadata.last_traceback is not None
    assert "ZeroDivisionError" in ctx.metadata.last_traceback
    assert ctx.metadata.retry_count >= 4


def test_integration_auto_routes_to_tactical_on_backend_file(router_engine):
    body = {
        "messages": [
            {"role": "user", "content": "Tolong modifikasi src/services/order_service.py"}
        ]
    }
    ctx = extract_routing_context(body)
    decision = router_engine.route(ctx)
    assert decision.target_model == "gemini_tactical"
    assert decision.rule_name == "backend_core"


def test_integration_auto_routes_to_opus_on_architecture_intent(router_engine):
    body = {
        "messages": [
            {
                "role": "user",
                "content": "Mari rancang system_architecture baru untuk modul payment dan auth_protocol",
            }
        ]
    }
    ctx = extract_routing_context(body)
    decision = router_engine.route(ctx)
    assert decision.target_model == "opus_apex"
    assert decision.rule_name == "apex_design"


def test_integration_auto_routes_to_opus_on_high_retries(router_engine):
    traceback_sample = """Traceback (most recent call last):
  File "test.py", line 1, in <module>
    raise ValueError("buntu")
ValueError: buntu"""

    messages = []
    for _ in range(4):
        messages.append({"role": "user", "content": f"Error lagi:\n{traceback_sample}"})
        messages.append({"role": "assistant", "content": "Coba cara lain..."})

    body = {"messages": messages}
    ctx = extract_routing_context(body)
    decision = router_engine.route(ctx)
    assert decision.target_model == "opus_apex"
    assert decision.rule_name == "rescue_opus"


# ── Activity detection: tool_calls from Hermes messages ──────────────

def test_detect_brainstorming_activity():
    """Hermes loads brainstorming skill -> activity = design."""
    messages = [
        {"role": "system", "content": "You are an AI assistant."},
        {"role": "user", "content": "Bikin fitur baru untuk payment gateway"},
        {
            "role": "assistant",
            "content": "Using brainstorming to explore design.",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "skill_view", "arguments": '{"name": "superpowers:brainstorming"}'}},
            ],
        },
        {"role": "tool", "content": "Brainstorming skill content...", "tool_call_id": "call_1", "name": "skill_view"},
        {
            "role": "assistant",
            "content": "Ini beberapa pendekatan...",
            "tool_calls": [
                {"id": "call_2", "type": "function", "function": {"name": "clarify", "arguments": '{"questions": [{"question": "Pilih approach mana?"}]}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "agent_activity_design" in ctx.metadata.intent


def test_detect_tdd_activity():
    """Hermes loads TDD skill and runs test cycle -> activity = tdd."""
    messages = [
        {"role": "system", "content": "You are an AI assistant."},
        {"role": "user", "content": "Implementasi fitur baru dengan TDD"},
        {
            "role": "assistant",
            "content": "Using TDD skill.",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "skill_view", "arguments": '{"name": "superpowers:test-driven-development"}'}},
            ],
        },
        {"role": "tool", "content": "TDD skill loaded.", "tool_call_id": "call_1", "name": "skill_view"},
        {
            "role": "assistant",
            "content": "Writing test first.",
            "tool_calls": [
                {"id": "call_2", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "tests/test_new.py", "content": "..."}'}},
            ],
        },
        {"role": "tool", "content": "File written.", "tool_call_id": "call_2", "name": "write_file"},
        {
            "role": "assistant",
            "content": "Running test.",
            "tool_calls": [
                {"id": "call_3", "type": "function", "function": {"name": "terminal", "arguments": '{"command": "uv run pytest tests/test_new.py"}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "agent_activity_tdd" in ctx.metadata.intent


def test_detect_debugging_activity():
    """Hermes loads systematic-debugging skill -> activity = debugging."""
    messages = [
        {"role": "system", "content": "You are an AI assistant."},
        {"role": "user", "content": "Ada bug di payment modul"},
        {
            "role": "assistant",
            "content": "Using systematic debugging.",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "skill_view", "arguments": '{"name": "superpowers:systematic-debugging"}'}},
            ],
        },
        {"role": "tool", "content": "Systematic debugging skill...", "tool_call_id": "call_1", "name": "skill_view"},
        {
            "role": "assistant",
            "content": "Investigating root cause.",
            "tool_calls": [
                {"id": "call_2", "type": "function", "function": {"name": "terminal", "arguments": '{"command": "uv run pytest tests/ -x"}'}},
                {"id": "call_3", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "src/payment.py"}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "agent_activity_debugging" in ctx.metadata.intent


def test_detect_code_review_activity():
    """Hermes doing code review -> activity = code_review."""
    messages = [
        {"role": "system", "content": "You are an AI assistant."},
        {"role": "user", "content": "Review PR ini"},
        {
            "role": "assistant",
            "content": "Reviewing changes.",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "skill_view", "arguments": '{"name": "superpowers:requesting-code-review"}'}},
            ],
        },
        {"role": "tool", "content": "Code review skill loaded.", "tool_call_id": "call_1", "name": "skill_view"},
        {
            "role": "assistant",
            "content": "Running diff review.",
            "tool_calls": [
                {"id": "call_2", "type": "function", "function": {"name": "diff_review", "arguments": '{"diff": "..."}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "agent_activity_code_review" in ctx.metadata.intent


def test_detect_research_activity():
    """Hermes doing web research -> activity = research."""
    messages = [
        {"role": "system", "content": "You are an AI assistant."},
        {"role": "user", "content": "Carikan best practice untuk redis caching"},
        {
            "role": "assistant",
            "content": "Searching...",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "redis caching best practices"}'}},
            ],
        },
        {"role": "tool", "content": "Results...", "tool_call_id": "call_1", "name": "web_search"},
        {
            "role": "assistant",
            "content": "Let me read this article.",
            "tool_calls": [
                {"id": "call_2", "type": "function", "function": {"name": "web_extract", "arguments": '{"urls": ["https://example.com"]}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "agent_activity_research" in ctx.metadata.intent


def test_detect_delegation_activity():
    """Hermes spawning subagents -> activity = delegation."""
    messages = [
        {"role": "system", "content": "You are an AI assistant."},
        {"role": "user", "content": "Implementasi ini dengan subagent parallel"},
        {
            "role": "assistant",
            "content": "Dispatching subagents.",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "delegate_task", "arguments": '{"tasks": [{"goal": "implement backend"}, {"goal": "implement frontend"}]}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "agent_activity_delegation" in ctx.metadata.intent


def test_detect_simple_chat_no_tools():
    """No tool calls = simple chat, no agent_activity intent."""
    messages = [
        {"role": "system", "content": "You are an AI assistant."},
        {"role": "user", "content": "Apa itu Python?"},
        {"role": "assistant", "content": "Python adalah bahasa pemrograman."},
        {"role": "user", "content": "Terima kasih"},
    ]
    ctx = extract_routing_context({"messages": messages})
    activity_intents = [i for i in ctx.metadata.intent if i.startswith("agent_activity_")]
    assert activity_intents == []


def test_detect_heavy_coding_without_skill():
    """Lots of write_file/patch/terminal without explicit skill = coding activity."""
    messages = [
        {"role": "system", "content": "You are an AI assistant."},
        {"role": "user", "content": "Tambahkan endpoint baru"},
        {
            "role": "assistant",
            "content": "Writing code.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "src/app.py"}'}},
            ],
        },
        {"role": "tool", "content": "file content...", "tool_call_id": "c1", "name": "read_file"},
        {
            "role": "assistant",
            "content": "Patching...",
            "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "patch", "arguments": '{"path": "src/app.py", "old_string": "a", "new_string": "b"}'}},
                {"id": "c3", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "src/new.py", "content": "..."}'}},
            ],
        },
        {"role": "tool", "content": "patched", "tool_call_id": "c2", "name": "patch"},
        {"role": "tool", "content": "written", "tool_call_id": "c3", "name": "write_file"},
        {
            "role": "assistant",
            "content": "Running tests.",
            "tool_calls": [
                {"id": "c4", "type": "function", "function": {"name": "terminal", "arguments": '{"command": "pytest"}'}},
            ],
        },
        {"role": "tool", "content": "PASSED", "tool_call_id": "c4", "name": "terminal"},
    ]
    ctx = extract_routing_context({"messages": messages})
    assert "agent_activity_coding" in ctx.metadata.intent


# ── Integration tests: activity-based routing ────────────────────────

def test_integration_design_activity_routes_to_tactical(router_engine):
    """Brainstorming activity should route to gemini_tactical."""
    messages = [
        {"role": "user", "content": "Bikin fitur baru"},
        {
            "role": "assistant",
            "content": "Loading brainstorming.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "skill_view", "arguments": '{"name": "superpowers:brainstorming"}'}},
            ],
        },
        {"role": "tool", "content": "brainstorming loaded.", "tool_call_id": "c1", "name": "skill_view"},
        {
            "role": "assistant",
            "content": "Proposing approaches.",
            "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "clarify", "arguments": '{"questions": [{"question": "A or B?"}]}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    decision = router_engine.route(ctx)
    assert decision.target_model == "gemini_tactical"


def test_integration_debugging_activity_routes_to_tactical(router_engine):
    """Debugging activity (no tracebacks yet, skill just loaded) -> tactical."""
    messages = [
        {"role": "user", "content": "Ada error aneh di modul ini"},
        {
            "role": "assistant",
            "content": "Loading debugging skill.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "skill_view", "arguments": '{"name": "superpowers:systematic-debugging"}'}},
            ],
        },
        {"role": "tool", "content": "debugging loaded.", "tool_call_id": "c1", "name": "skill_view"},
    ]
    ctx = extract_routing_context({"messages": messages})
    decision = router_engine.route(ctx)
    assert decision.target_model == "gemini_tactical"


def test_integration_delegation_routes_to_tactical(router_engine):
    """Delegation activity -> tactical (orchestration)."""
    messages = [
        {"role": "user", "content": "Kerjakan fitur ini parallel"},
        {
            "role": "assistant",
            "content": "Dispatching.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "delegate_task", "arguments": '{"tasks": [{"goal": "backend"}]}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    decision = router_engine.route(ctx)
    assert decision.target_model == "gemini_tactical"


def test_integration_simple_chat_routes_to_flash(router_engine):
    """Simple chat without tools -> default Flash."""
    messages = [
        {"role": "user", "content": "Apa itu Python?"},
        {"role": "assistant", "content": "Python adalah..."},
        {"role": "user", "content": "Thanks"},
    ]
    ctx = extract_routing_context({"messages": messages})
    decision = router_engine.route(ctx)
    assert decision.target_model == "gemini_executor"
    assert decision.rule_name == "default_worker"


def test_integration_research_routes_to_flash(router_engine):
    """Research activity -> stays on Flash (IO-bound)."""
    messages = [
        {"role": "user", "content": "Carikan info tentang GRPO"},
        {
            "role": "assistant",
            "content": "Searching.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "GRPO"}'}},
            ],
        },
        {"role": "tool", "content": "search results...", "tool_call_id": "c1", "name": "web_search"},
    ]
    ctx = extract_routing_context({"messages": messages})
    decision = router_engine.route(ctx)
    assert decision.target_model == "gemini_executor"


def test_integration_coding_routes_to_flash(router_engine):
    """Coding activity (no backend_core path match) -> stays on Flash."""
    messages = [
        {"role": "user", "content": "Tambah fungsi di utils.py"},
        {
            "role": "assistant",
            "content": "Writing.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "utils.py", "content": "..."}'}},
                {"id": "c2", "type": "function", "function": {"name": "terminal", "arguments": '{"command": "pytest"}'}},
            ],
        },
    ]
    ctx = extract_routing_context({"messages": messages})
    decision = router_engine.route(ctx)
    assert decision.target_model == "gemini_executor"
