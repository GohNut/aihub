"""Shim file สำหรับ LiteLLM proxy config

LiteLLM โหลด custom_handler จาก path ที่ relative ต่อ config file
ดังนั้นไฟล์นี้ต้องอยู่ข้าง aihub_config.yaml และ expose instances ตรงๆ
"""
from litellm.llms.cli_providers.providers.claude import ClaudeCliLLM
from litellm.llms.cli_providers.providers.gemini import GeminiCliLLM

# instance ที่ LiteLLM จะหยิบไปใช้
claude_handler = ClaudeCliLLM()
gemini_handler = GeminiCliLLM()
