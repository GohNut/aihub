from unittest.mock import MagicMock, patch

import pytest

from litellm.llms.cli_providers.base import CLIProviderLLM
from litellm.llms.custom_llm import CustomLLMError
from litellm.types.utils import Choices, Message
from litellm.utils import ModelResponse


class FakeCLI(CLIProviderLLM):
    """Concrete subclass สำหรับ test เท่านั้น"""

    @property
    def provider_id(self) -> str:
        return "fake"

    @property
    def bin(self) -> str:
        return "fake-cli"

    @property
    def build_args(self) -> list:
        return ["--flag"]

    def _parse_output(self, raw_output: str) -> str:
        return raw_output.strip()


def make_model_response() -> ModelResponse:
    return ModelResponse(
        id="test-id",
        choices=[Choices(index=0, message=Message(role="assistant", content=""))],
        model="fake/model",
    )


def test_format_prompt_from_string_content():
    cli = FakeCLI()
    messages = [{"role": "user", "content": "hello world"}]
    assert cli._format_prompt(messages) == "hello world"


def test_format_prompt_from_list_content():
    cli = FakeCLI()
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi there"}]}]
    assert cli._format_prompt(messages) == "hi there"


def test_format_prompt_uses_last_user_message():
    cli = FakeCLI()
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second"},
    ]
    assert cli._format_prompt(messages) == "second"


def test_run_subprocess_raises_when_cli_not_found():
    cli = FakeCLI()
    with patch("shutil.which", return_value=None):
        with pytest.raises(CustomLLMError) as exc_info:
            cli._run_subprocess("hello")
    assert exc_info.value.status_code == 503
    assert "not found" in exc_info.value.message


def test_run_subprocess_raises_on_nonzero_exit():
    cli = FakeCLI()
    with patch("shutil.which", return_value="/usr/bin/fake-cli"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            with pytest.raises(CustomLLMError) as exc_info:
                cli._run_subprocess("hello")
    assert exc_info.value.status_code == 500


def test_run_subprocess_returns_stdout_on_success():
    cli = FakeCLI()
    with patch("shutil.which", return_value="/usr/bin/fake-cli"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="response text\n", stderr="")
            result = cli._run_subprocess("hello")
    assert result == "response text\n"


def test_completion_sets_model_response_content():
    cli = FakeCLI()
    model_response = make_model_response()
    with patch.object(cli, "_run_subprocess", return_value="parsed text"):
        result = cli.completion(
            model="fake/model",
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
    assert result.choices[0].message.content == "parsed text"


@pytest.mark.asyncio
async def test_acompletion_calls_completion_in_executor():
    cli = FakeCLI()
    model_response = make_model_response()
    with patch.object(cli, "_run_subprocess", return_value="async parsed text"):
        result = await cli.acompletion(
            model="fake/model",
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
    assert result.choices[0].message.content == "async parsed text"
