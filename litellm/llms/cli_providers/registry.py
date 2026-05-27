"""
ต่อ CLI providers เข้า litellm.custom_provider_map

วิธีใช้:
    from litellm.llms.cli_providers.registry import register_cli_providers
    register_cli_providers()

หรือใน litellm proxy config:
    litellm_settings:
      custom_provider_map:
        - provider: "cli-claude"
          custom_handler: litellm.llms.cli_providers.providers.claude.ClaudeCliLLM
"""
from __future__ import annotations

import litellm

from .detector import detect_available_providers
from .providers.claude import ClaudeCliLLM
from .providers.gemini import GeminiCliLLM

# Map provider_id → handler class
_PROVIDER_CLASSES: dict = {
    "claude": ClaudeCliLLM,
    "gemini": GeminiCliLLM,
}

# Prefix สำหรับ model string: "cli-claude/model-name"
CLI_PROVIDER_PREFIX = "cli-"


def register_cli_providers(only_installed: bool = True) -> list:
    """Register CLI providers ที่หาเจอบน PATH เข้า litellm.custom_provider_map

    Args:
        only_installed: ถ้า True จะ register แค่ CLIs ที่ install ไว้แล้ว

    Returns:
        list ของ provider_ids ที่ register สำเร็จ
    """
    if only_installed:
        available = detect_available_providers()
    else:
        available = {pid: None for pid in _PROVIDER_CLASSES}

    registered: list = []
    for provider_id, handler_class in _PROVIDER_CLASSES.items():
        if only_installed and provider_id not in available:
            continue
        provider_name = f"{CLI_PROVIDER_PREFIX}{provider_id}"
        # ป้องกัน duplicate registration
        existing_ids = {item["provider"] for item in litellm.custom_provider_map}
        if provider_name not in existing_ids:
            litellm.custom_provider_map.append({
                "provider": provider_name,
                "custom_handler": handler_class(),
            })
            registered.append(provider_name)
    return registered
