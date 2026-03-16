# LLM API Configuration

This document records the LLM providers, models, and their corresponding API key environment variables used in DojoZero.

## Quick Reference

| Provider | Model | Model Type | API Key Environment Variable | Base URL Environment Variable | Status |
|----------|-------|------------|------------------------------|------------------------------|--------|
| **Qwen** | `qwen3-max` | `dashscope` | `DOJOZERO_DASHSCOPE_API_KEY` | - | ✅ Active |
| **Deepseek** | `deepseek-v3.2` | `dashscope` | `DOJOZERO_DASHSCOPE_API_KEY` | - | ✅ Active |
| **Anthropic** | `claude-haiku-4-5-20251001` | `anthropic` | `DOJOZERO_ANTHROPIC_API_KEY` | - | ✅ Active |
| **Gemini** | `gemini-3-pro-preview` | `gemini` | `DOJOZERO_GEMINI_API_KEY` | - | ⚠️ Commented out |
| **OpenAI** | `gpt-5-mini-2025-08-07` | `openai` | `DOJOZERO_MODEL_API_KEY` | `DOJOZERO_OPENAI_BASE_URL` | ✅ Active |
| **Grok** | `grok-4-1-fast-reasoning` | `openai` | `DOJOZERO_MODEL_API_KEY` | `DOJOZERO_OPENAI_BASE_URL` | ✅ Active |
