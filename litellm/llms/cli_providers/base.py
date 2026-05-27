from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from abc import abstractmethod
from typing import Any, Callable, Coroutine, Optional, Union

from litellm.llms.custom_llm import CustomLLM, CustomLLMError
from litellm.utils import ModelResponse


class CLIProviderLLM(CustomLLM):
    """Base class สำหรับ CLI-based providers

    Subclass ต้อง implement: provider_id, bin, build_args, _parse_output
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """ID ของ provider เช่น 'claude', 'gemini'"""
        ...

    @property
    @abstractmethod
    def bin(self) -> str:
        """ชื่อ binary หลักของ CLI"""
        ...

    @property
    @abstractmethod
    def build_args(self) -> list:
        """Arguments ที่ pass ให้ CLI ทุกครั้ง"""
        ...

    @property
    def timeout_seconds(self) -> int:
        """Timeout สำหรับ subprocess (default 120s)"""
        return 120

    def _build_env(self) -> dict:
        """Environment variables สำหรับ subprocess"""
        return {**os.environ}

    def _format_prompt(self, messages: list) -> str:
        """แปลง messages list เป็น plain text prompt — ใช้ user message ล่าสุด"""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return " ".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
        return ""

    def _run_subprocess(self, prompt: str) -> str:
        """Spawn CLI subprocess, feed prompt via stdin, return stdout"""
        bin_path = shutil.which(self.bin)
        if bin_path is None:
            raise CustomLLMError(
                status_code=503,
                message=f"CLI '{self.bin}' not found on PATH. Install and login first.",
            )
        result = subprocess.run(
            [bin_path] + self.build_args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=self._build_env(),
        )
        if result.returncode != 0:
            raise CustomLLMError(
                status_code=500,
                message=f"CLI '{self.bin}' exited {result.returncode}: {result.stderr[:500]}",
            )
        return result.stdout

    @abstractmethod
    def _parse_output(self, raw_output: str) -> str:
        """Parse raw CLI output → plain assistant text"""
        ...

    def completion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding: Any,
        api_key: Optional[str],
        logging_obj: Any,
        optional_params: dict,
        **kwargs: Any,
    ) -> ModelResponse:
        prompt = self._format_prompt(messages)
        raw_output = self._run_subprocess(prompt)
        text = self._parse_output(raw_output)
        model_response.choices[0].message.content = text
        return model_response

    async def acompletion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding: Any,
        api_key: Optional[str],
        logging_obj: Any,
        optional_params: dict,
        **kwargs: Any,
    ) -> Union[ModelResponse, Coroutine[Any, Any, ModelResponse]]:
        """Async wrapper — รัน subprocess ใน thread executor เพื่อไม่บล็อก event loop"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.completion(
                model=model,
                messages=messages,
                api_base=api_base,
                custom_prompt_dict=custom_prompt_dict,
                model_response=model_response,
                print_verbose=print_verbose,
                encoding=encoding,
                api_key=api_key,
                logging_obj=logging_obj,
                optional_params=optional_params,
                **kwargs,
            ),
        )
