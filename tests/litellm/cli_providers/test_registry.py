from unittest.mock import patch

import pytest

import litellm
from litellm.llms.cli_providers.providers.claude import ClaudeCliLLM
from litellm.llms.cli_providers.providers.gemini import GeminiCliLLM
from litellm.llms.cli_providers.registry import (
    CLI_PROVIDER_PREFIX,
    register_cli_providers,
)


@pytest.fixture(autouse=True)
def reset_custom_provider_map():
    """ล้าง custom_provider_map ก่อน/หลังทุก test"""
    original = list(litellm.custom_provider_map)
    litellm.custom_provider_map.clear()
    yield
    litellm.custom_provider_map.clear()
    litellm.custom_provider_map.extend(original)


def test_register_with_only_installed_filters_unavailable():
    """ถ้า only_installed=True จะ register แค่ CLIs ที่หาเจอบน PATH"""
    with patch(
        "litellm.llms.cli_providers.registry.detect_available_providers",
        return_value={"claude": "/usr/bin/claude"},
    ):
        result = register_cli_providers(only_installed=True)
    assert result == ["cli-claude"]
    assert len(litellm.custom_provider_map) == 1
    assert litellm.custom_provider_map[0]["provider"] == "cli-claude"
    assert isinstance(litellm.custom_provider_map[0]["custom_handler"], ClaudeCliLLM)


def test_register_only_installed_empty():
    """ถ้าไม่มี CLI ติดตั้งเลย — register ไม่มีอะไร"""
    with patch(
        "litellm.llms.cli_providers.registry.detect_available_providers",
        return_value={},
    ):
        result = register_cli_providers(only_installed=True)
    assert result == []
    assert len(litellm.custom_provider_map) == 0


def test_register_all_when_only_installed_false():
    """only_installed=False จะ register ทุก providers ที่รองรับ"""
    result = register_cli_providers(only_installed=False)
    assert "cli-claude" in result
    assert "cli-gemini" in result
    providers = {item["provider"] for item in litellm.custom_provider_map}
    assert "cli-claude" in providers
    assert "cli-gemini" in providers


def test_register_does_not_duplicate():
    """call ซ้ำไม่ควรเพิ่ม duplicate"""
    register_cli_providers(only_installed=False)
    initial_count = len(litellm.custom_provider_map)
    second_result = register_cli_providers(only_installed=False)
    assert second_result == []  # ไม่มีอะไรใหม่
    assert len(litellm.custom_provider_map) == initial_count


def test_prefix_constant():
    assert CLI_PROVIDER_PREFIX == "cli-"


def test_handler_instance_types():
    """ตรวจสอบว่า handler ที่ register เป็น instance ของ class ที่ถูกต้อง"""
    register_cli_providers(only_installed=False)
    by_provider = {item["provider"]: item["custom_handler"] for item in litellm.custom_provider_map}
    assert isinstance(by_provider["cli-claude"], ClaudeCliLLM)
    assert isinstance(by_provider["cli-gemini"], GeminiCliLLM)
