from __future__ import annotations

import json
import os

from ..base import CLIProviderLLM


class ClaudeCliLLM(CLIProviderLLM):
    """Claude Code CLI provider — ใช้ login session แทน API key

    เรียก: claude -p --output-format stream-json --verbose --permission-mode bypassPermissions
    Input: plain text via stdin
    Output: JSONL stream, เราดึงเฉพาะ text จาก assistant messages
    """

    @property
    def provider_id(self) -> str:
        return "claude"

    @property
    def bin(self) -> str:
        return "claude"

    @property
    def build_args(self) -> list:
        return [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
        ]

    def _build_env(self) -> dict:
        """Strip ANTHROPIC_API_KEY เพื่อให้ Claude Code ใช้ claude login แทน"""
        env = {**os.environ}
        # ลบ key แบบ case-insensitive (Windows อาจเก็บเป็น 'Anthropic_Api_Key')
        to_delete = [k for k in env if k.upper() == "ANTHROPIC_API_KEY"]
        for key in to_delete:
            del env[key]
        return env

    def _parse_output(self, raw_output: str) -> str:
        """Parse JSONL output จาก claude --output-format stream-json

        ดึง text จาก type=assistant messages เท่านั้น
        ข้าม: type=system, type=result, tool_use blocks
        """
        text_parts: list = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            message = obj.get("message", {})
            if not isinstance(message, dict):
                continue
            for block in message.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        text_parts.append(text)
        return "".join(text_parts)
