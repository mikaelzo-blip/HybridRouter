from unittest.mock import MagicMock
import pytest
from src.distiller.dynamic_distiller import (
    est_tokens,
    render,
    distill,
    run_architect_pipeline,
    DISTILL_THRESHOLD_TOKENS,
    MAX_FETCH_ROUNDS
)


def test_est_tokens_and_render():
    assert est_tokens("12345678") == 2
    files = {"src/a.py": "x = 1", "src/b.py": "y = 2"}
    rendered = render(files)
    assert "// File: src/a.py\nx = 1" in rendered
    assert "// File: src/b.py\ny = 2" in rendered


def test_architect_pipeline_under_threshold_skips_distillation():
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Final Apex Design"
    mock_client.chat.completions.create.return_value.choices = [mock_choice]
    
    # Small repo (<40k tokens)
    repo = {"src/main.py": "print('hello')"}
    result = run_architect_pipeline(repo, "Build feature", client=mock_client)
    
    assert result == "Final Apex Design"
    # Should only call opus_apex once, no distill call
    assert mock_client.chat.completions.create.call_count == 1
    call_args = mock_client.chat.completions.create.call_args[1]
    assert call_args["model"] == "opus_apex"
    # Context should contain raw file
    user_prompt = call_args["messages"][1]["content"]
    assert "// File: src/main.py\nprint('hello')" in user_prompt


def test_architect_pipeline_over_threshold_invokes_distillation():
    mock_client = MagicMock()
    
    # First response: distill output from gemini_tactical
    # Second response: design output from opus_apex
    mock_choice_distill = MagicMock()
    mock_choice_distill.message.content = "Distilled Architecture Context < 12k"
    
    mock_choice_design = MagicMock()
    mock_choice_design.message.content = "Apex Blueprint Completed"
    
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[mock_choice_distill]),
        MagicMock(choices=[mock_choice_design])
    ]
    
    # Large repo (> 40k tokens, i.e. > 160k chars)
    large_code = "a" * 170000
    repo = {"src/big_file.py": large_code}
    
    result = run_architect_pipeline(repo, "Audit system", client=mock_client)
    assert result == "Apex Blueprint Completed"
    assert mock_client.chat.completions.create.call_count == 2
    
    # 1st call: gemini_tactical distill
    call_1 = mock_client.chat.completions.create.call_args_list[0][1]
    assert call_1["model"] == "gemini_tactical"
    
    # 2nd call: opus_apex design with distilled context
    call_2 = mock_client.chat.completions.create.call_args_list[1][1]
    assert call_2["model"] == "opus_apex"
    assert "Distilled Architecture Context < 12k" in call_2["messages"][1]["content"]


def test_need_file_feedback_loop():
    mock_client = MagicMock()
    
    # 1st Opus response: requests NEED_FILE: src/schema.sql
    resp1 = MagicMock()
    resp1.message.content = "Need more details.\nNEED_FILE: src/schema.sql"
    
    # 2nd Opus response: completes design
    resp2 = MagicMock()
    resp2.message.content = "Complete architecture specification."
    
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[resp1]),
        MagicMock(choices=[resp2])
    ]
    
    repo = {
        "src/main.py": "main code",
        "src/schema.sql": "CREATE TABLE users (id INT);"
    }
    
    result = run_architect_pipeline(repo, "Review schema", client=mock_client)
    assert result == "Complete architecture specification."
    assert mock_client.chat.completions.create.call_count == 2
    
    # Verify 2nd call received requested file content
    call_2 = mock_client.chat.completions.create.call_args_list[1][1]
    messages = call_2["messages"]
    assert any("CREATE TABLE users (id INT);" in m["content"] for m in messages if m["role"] == "user")
