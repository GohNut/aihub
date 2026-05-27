from __future__ import annotations

import json
import os

from ..base import CLIProviderLLM


class GeminiCliLLM(CLIProviderLLM):
    """Gemini CLI provider — ใช้ Google account login แทน API key

    เรียก: gemini --output-format stream-json --yolo
    Input: plain text via stdin
    Output: JSONL stream
    Env: GEMINI_CLI_TRUST_WORKSPACE=true เพื่อ skip workspace trust prompt
    """

    @property
    def provider_id(self) -> str:
        return "gemini"

    @property
    def bin(self) -> str:
        return "gemini"

    @property
    def build_args(self) -> list:
        return ["--output-format", "stream-json", "--yolo"]

    def _build_env(self) -> dict:
        """เพิ่ม GEMINI_CLI_TRUST_WORKSPACE เพื่อ skip trust prompt"""
        return {**os.environ, "GEMINI_CLI_TRUST_WORKSPACE": "true"}

    def _parse_output(self, raw_output: str) -> str:
        """Parse JSONL output จาก gemini --output-format stream-json

        รองรับสอง format:
        - {"type": "content", "value": "..."}  → streaming chunks
        - {"type": "response", "content": "..."} → complete response
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
            if not isinstance(obj, dict):
                continue
            event_type = obj.get("type")
            if event_type == "content":
                value = obj.get("value", "")
                if value:
                    text_parts.append(value)
            elif event_type == "response":
                content = obj.get("content", "")
                if content:
                    text_parts.append(content)
        return "".join(text_parts)
