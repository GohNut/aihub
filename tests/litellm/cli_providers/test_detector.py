import shutil
from unittest.mock import patch

import pytest

from litellm.llms.cli_providers.detector import (
    CLI_REGISTRY,
    detect_available_providers,
    detect_cli,
)


def test_detect_cli_found(tmp_path):
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\necho ok")
    fake_bin.chmod(0o755)
    with patch("shutil.which", return_value=str(fake_bin)):
        result = detect_cli("claude")
    assert result == str(fake_bin)


def test_detect_cli_not_found():
    with patch("shutil.which", return_value=None):
        result = detect_cli("claude")
    assert result is None


def test_detect_cli_fallback_bin():
    """ถ้า primary bin ไม่มี ให้ลอง fallback bins"""
    def fake_which(name):
        if name == "openclaude":
            return "/usr/local/bin/openclaude"
        return None
    with patch("shutil.which", side_effect=fake_which):
        result = detect_cli("claude")
    assert result == "/usr/local/bin/openclaude"


def test_detect_available_providers_empty():
    with patch("shutil.which", return_value=None):
        result = detect_available_providers()
    assert result == {}


def test_detect_available_providers_some_installed():
    def fake_which(name):
        return f"/usr/bin/{name}" if name == "claude" else None
    with patch("shutil.which", side_effect=fake_which):
        result = detect_available_providers()
    assert "claude" in result
    assert result["claude"] == "/usr/bin/claude"
    assert "gemini" not in result


def test_cli_registry_has_expected_providers():
    assert "claude" in CLI_REGISTRY
    assert "gemini" in CLI_REGISTRY
    for provider_id, entry in CLI_REGISTRY.items():
        assert "bins" in entry, f"{provider_id} missing 'bins'"
        assert len(entry["bins"]) >= 1
