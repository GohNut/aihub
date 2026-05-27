import json
from unittest.mock import patch

import pytest

from litellm.llms.cli_providers.providers.gemini import GeminiCliLLM
from litellm.types.utils import Choices, Message
from litellm.utils import ModelResponse


def make_model_response() -> ModelResponse:
    return ModelResponse(
        id="test-id",
        choices=[Choices(index=0, message=Message(role="assistant", content=""))],
        model="cli-gemini/gemini",
    )


# Gemini CLI ใช้ --output-format stream-json
# format: {"type": "content", "value": "...text..."} หรือ {"type": "response", "content": "..."}
GEMINI_STREAM_OUTPUT = "\n".join([
    json.dumps({"type": "content", "value": "Hello "}),
    json.dumps({"type": "content", "value": "from Gemini!"}),
    json.dumps({"type": "done", "exitCode": 0}),
])

GEMINI_STREAM_RESPONSE = "\n".join([
    json.dumps({"type": "response", "content": "Hello from Gemini!"}),
])


def test_provider_id():
    assert GeminiCliLLM().provider_id == "gemini"


def test_bin_name():
    assert GeminiCliLLM().bin == "gemini"


def test_build_args_includes_required_flags():
    args = GeminiCliLLM().build_args
    assert "--output-format" in args
    assert "stream-json" in args
    assert "--yolo" in args


def test_build_env_sets_trust_workspace():
    cli = GeminiCliLLM()
    env = cli._build_env()
    assert env.get("GEMINI_CLI_TRUST_WORKSPACE") == "true"


def test_parse_output_content_events():
    cli = GeminiCliLLM()
    result = cli._parse_output(GEMINI_STREAM_OUTPUT)
    assert result == "Hello from Gemini!"


def test_parse_output_response_type():
    cli = GeminiCliLLM()
    result = cli._parse_output(GEMINI_STREAM_RESPONSE)
    assert result == "Hello from Gemini!"


def test_parse_output_ignores_non_json():
    cli = GeminiCliLLM()
    raw = "not json\n" + json.dumps({"type": "content", "value": "ok"})
    assert cli._parse_output(raw) == "ok"


def test_completion_end_to_end():
    cli = GeminiCliLLM()
    model_response = make_model_response()
    with patch.object(cli, "_run_subprocess", return_value=GEMINI_STREAM_OUTPUT):
        result = cli.completion(
            model="cli-gemini/gemini",
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
    assert result.choices[0].message.content == "Hello from Gemini!"
