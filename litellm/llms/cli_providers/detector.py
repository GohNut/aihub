import shutil
from typing import Optional

# Registry ของ CLI providers ที่รองรับ
# bins: list ของ binary names ที่ลองตามลำดับ (primary ก่อน fallback)
CLI_REGISTRY: dict[str, dict] = {
    "claude": {"bins": ["claude", "openclaude"]},
    "codex": {"bins": ["codex"]},
    "devin": {"bins": ["devin"]},
    "cursor": {"bins": ["cursor-agent"]},
    "gemini": {"bins": ["gemini"]},
    "opencode": {"bins": ["opencode"]},
    "qwen": {"bins": ["qwen-code"]},
    "qoder": {"bins": ["qoder"]},
    "copilot": {"bins": ["gh-copilot"]},
    "hermes": {"bins": ["hermes"]},
    "kimi": {"bins": ["kimi"]},
    "pi": {"bins": ["pi"]},
    "kiro": {"bins": ["kiro"]},
    "kilo": {"bins": ["kilo"]},
    "vibe": {"bins": ["vibe"]},
    "deepseek": {"bins": ["deepseek"]},
}


def detect_cli(provider_id: str) -> Optional[str]:
    """คืน path ของ CLI binary ถ้าหาเจอ หรือ None ถ้าไม่มี"""
    entry = CLI_REGISTRY.get(provider_id)
    if not entry:
        return None
    for bin_name in entry["bins"]:
        path = shutil.which(bin_name)
        if path:
            return path
    return None


def detect_available_providers() -> dict[str, str]:
    """คืน dict ของ {provider_id: bin_path} สำหรับ CLIs ที่ install ไว้แล้ว"""
    result: dict[str, str] = {}
    for provider_id in CLI_REGISTRY:
        path = detect_cli(provider_id)
        if path is not None:
            result[provider_id] = path
    return result
