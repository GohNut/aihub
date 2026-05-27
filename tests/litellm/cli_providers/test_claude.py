import json
import os
from unittest.mock import patch

import pytest

from litellm.llms.cli_providers.providers.claude import ClaudeCliLLM
from litellm.types.utils import Choices, Message
from litellm.utils import ModelResponse


def make_model_response() -> ModelResponse:
    return ModelResponse(
        id="test-id",
        choices=[Choices(index=0, message=Message(role="assistant", content=""))],
        model="cli-claude/claude",
    )


# ตัวอย่าง JSONL output จาก claude --output-format stream-json
CLAUDE_STREAM_OUTPUT = "\n".join([
    json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-4-5"}),
    json.dumps({"type": "assistant", "message": {
        "id": "msg_01",
        "content": [{"type": "text", "text": "Hello! How can I help?"}]
    }}),
    json.dumps({"type": "result", "subtype": "success", "result": "Hello! How can I help?"}),
])

CLAUDE_STREAM_MULTI_BLOCK = "\n".join([
    json.dumps({"type": "assistant", "message": {
        "id": "msg_02",
        "content": [
            {"type": "text", "text": "First part. "},
            {"type": "text", "text": "Second part."},
        ]
    }}),
])


def test_provider_id():
    assert ClaudeCliLLM().provider_id == "claude"


def test_bin_name():
    assert ClaudeCliLLM().bin == "claude"


def test_build_args_includes_required_flags():
    args = ClaudeCliLLM().build_args
    assert "-p" in args
    assert "--output-format" in args
    assert "stream-json" in args
    assert "--permission-mode" in args
    assert "bypassPermissions" in args


def test_parse_output_extracts_text():
    cli = ClaudeCliLLM()
    result = cli._parse_output(CLAUDE_STREAM_OUTPUT)
    assert result == "Hello! How can I help?"


def test_parse_output_concatenates_multiple_text_blocks():
    cli = ClaudeCliLLM()
    result = cli._parse_output(CLAUDE_STREAM_MULTI_BLOCK)
    assert result == "First part. Second part."


def test_parse_output_ignores_non_json_lines():
    cli = ClaudeCliLLM()
    raw = "not json\n" + json.dumps({"type": "assistant", "message": {
        "id": "x", "content": [{"type": "text", "text": "ok"}]
    }})
    assert cli._parse_output(raw) == "ok"


def test_parse_output_ignores_tool_use_blocks():
    """tool_use blocks ไม่ควรปรากฏใน response text"""
    cli = ClaudeCliLLM()
    raw = json.dumps({"type": "assistant", "message": {
        "id": "x",
        "content": [
            {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
            {"type": "text", "text": "done"},
        ]
    }})
    assert cli._parse_output(raw) == "done"


def test_build_env_strips_anthropic_api_key():
    """strip ANTHROPIC_API_KEY เพื่อให้ claude CLI ใช้ login session แทน"""
    cli = ClaudeCliLLM()
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret", "HOME": "/home/user"}, clear=True):
        env = cli._build_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert env.get("HOME") == "/home/user"


def test_completion_end_to_end():
    cli = ClaudeCliLLM()
    model_response = make_model_response()
    with patch.object(cli, "_run_subprocess", return_value=CLAUDE_STREAM_OUTPUT):
        result = cli.completion(
            model="cli-claude/claude",
            messages=[{"role": "user", "content": "hi"}],
            api_base="",
            custom_prompt_dict={},
            model_response=model_response,
            print_verbose=lambda *a: None,
            encoding=None,
            api_key=None,
            logging_obj=None,
            optional_params={},
        )
    assert result.choices[0].message.content == "Hello! How can I help?"
