"""LLM Abstraction Layer — provider-agnostic interface to language models.

Supports: Ollama (default), OpenAI, Anthropic.
Switch providers by setting LLM_PROVIDER in .env.

Comprehensive logging covers:
- Request dispatch (provider, model, prompt size, temperature)
- Response receipt (latency, response size, token counts where available)
- JSON parsing success/failure with content preview
- Health check status
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("hireai.llm")


class LLMResponse:
    """Standardized response from any LLM provider."""

    def __init__(self, content: str, model: str, latency_ms: int, raw_meta: dict = None):
        self.content = content
        self.model = model
        self.latency_ms = latency_ms
        self.raw_meta = raw_meta or {}

    def as_json(self) -> dict:
        """Parse content as JSON with robust extraction.

        Handles:
        - Clean JSON
        - JSON wrapped in markdown code fences (```json ... ```)
        - JSON embedded in prose (text before/after)
        - Trailing commas
        - Minor formatting issues
        """
        import re

        text = self.content.strip()

        if not text:
            logger.warning("LLM.parse | Empty response content")
            return {}

        # Strategy 1: Try direct parse
        parsed = self._try_json_parse(text)
        if parsed:
            return parsed

        # Strategy 2: Extract from markdown code fences
        fence_pattern = r'```(?:json)?\s*\n?(.*?)\n?\s*```'
        fence_matches = re.findall(fence_pattern, text, re.DOTALL)
        for match in fence_matches:
            parsed = self._try_json_parse(match.strip())
            if parsed:
                logger.debug("LLM.parse | Extracted JSON from code fences")
                return parsed

        # Strategy 3: Find first { ... } block (greedy from first { to last })
        first_brace = text.find('{')
        last_brace = text.rfind('}')
        if first_brace != -1 and last_brace > first_brace:
            json_candidate = text[first_brace:last_brace + 1]
            parsed = self._try_json_parse(json_candidate)
            if parsed:
                logger.debug("LLM.parse | Extracted JSON from brace-delimited block")
                return parsed

            # Strategy 4: Fix common LLM JSON issues and retry
            fixed = self._fix_json(json_candidate)
            parsed = self._try_json_parse(fixed)
            if parsed:
                logger.debug("LLM.parse | Extracted JSON after fixing common issues")
                return parsed

        # Strategy 5: Try line-by-line to find JSON start
        lines = text.split('\n')
        json_lines = []
        capture = False
        brace_depth = 0
        for line in lines:
            stripped = line.strip()
            if not capture and stripped.startswith('{'):
                capture = True
            if capture:
                json_lines.append(line)
                brace_depth += stripped.count('{') - stripped.count('}')
                if brace_depth <= 0:
                    break

        if json_lines:
            parsed = self._try_json_parse('\n'.join(json_lines))
            if parsed:
                logger.debug("LLM.parse | Extracted JSON via line-by-line brace tracking")
                return parsed

        # Strategy 6: Truncated JSON repair
        # LLM hit max_tokens mid-stream — try to surgically close open structures.
        raw = text[text.find('{'):] if '{' in text else text
        if raw:
            repaired = self._repair_truncated_json(raw)
            parsed = self._try_json_parse(repaired)
            if parsed:
                logger.warning(
                    "LLM.parse | Recovered partial JSON via truncation repair"
                    f" | original_len={len(raw)} | repaired_len={len(repaired)}"
                )
                return parsed

        # All strategies failed
        preview = text[:300].replace("\n", "\\n")
        logger.error(
            f"LLM.parse | All JSON extraction strategies FAILED"
            f" | content_length={len(text)}"
            f" | content_preview=\"{preview}...\""
        )
        return {}

    def _try_json_parse(self, text: str) -> dict:
        """Attempt to parse text as JSON. Returns dict or None."""
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                logger.info(
                    f"LLM.parse | JSON parsed successfully"
                    f" | keys={list(parsed.keys())}"
                )
                return parsed
            elif isinstance(parsed, list):
                logger.info(f"LLM.parse | JSON array parsed | length={len(parsed)}")
                return parsed
            return None
        except (json.JSONDecodeError, ValueError):
            return None

    def _fix_json(self, text: str) -> str:
        """Fix common LLM JSON issues."""
        import re
        fixed = text
        # Remove trailing commas before } or ]
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
        # Fix unquoted True/False/None (Python literals → JSON)
        fixed = re.sub(r'\bTrue\b', 'true', fixed)
        fixed = re.sub(r'\bFalse\b', 'false', fixed)
        fixed = re.sub(r'\bNone\b', 'null', fixed)
        # Remove single-line comments
        fixed = re.sub(r'//.*$', '', fixed, flags=re.MULTILINE)
        return fixed

    def _repair_truncated_json(self, text: str) -> str:
        """Attempt to close a JSON object that was cut off mid-stream.

        Walks the string character-by-character tracking open braces/brackets
        and open strings, then appends the minimum closing tokens needed to
        produce valid JSON.  The result may be structurally complete but
        semantically partial (e.g. the last skill entry is dropped) — callers
        should treat recovered output as best-effort and log a warning.
        """
        import re

        # 1. Strip trailing commas and partial key-value pairs so we close
        #    cleanly after the last *complete* value.
        text = text.rstrip()
        # Remove a trailing incomplete string (open quote never closed)
        text = re.sub(r',?\s*"[^"]*$', '', text)
        # Remove a trailing comma after the last complete value
        text = re.sub(r',\s*$', '', text)

        # 2. Walk the string to count open structures, skipping string content.
        stack = []     # '{' or '['
        in_string = False
        escape_next = False

        for ch in text:
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in ('{', '['):
                stack.append(ch)
            elif ch == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif ch == ']' and stack and stack[-1] == '[':
                stack.pop()

        # 3. Close all open structures in reverse order.
        closing = ''
        for opener in reversed(stack):
            closing += ']' if opener == '[' else '}'

        return text + closing


class LLMProvider(ABC):
    """Abstract base for all LLM providers."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        pass


class OllamaProvider(LLMProvider):
    """Ollama local LLM provider."""

    def __init__(self):
        self.base_url = settings.LLM_BASE_URL
        self.model = settings.LLM_MODEL
        self.client = httpx.AsyncClient(timeout=settings.LLM_TIMEOUT)
        logger.info(
            f"OllamaProvider initialized"
            f" | base_url={self.base_url}"
            f" | model={self.model}"
            f" | timeout={settings.LLM_TIMEOUT}s"
        )

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        effective_temp = temperature or settings.LLM_TEMPERATURE
        effective_max_tokens = max_tokens or settings.LLM_MAX_TOKENS

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": effective_temp,
                "num_predict": effective_max_tokens,
            },
        }

        prompt_chars = len(prompt)
        system_chars = len(system_prompt) if system_prompt else 0

        logger.info(
            f"LLM.request | provider=ollama | model={self.model}"
            f" | prompt_chars={prompt_chars} | system_chars={system_chars}"
            f" | temperature={effective_temp} | max_tokens={effective_max_tokens}"
        )
        logger.debug(
            f"LLM.request | prompt_preview=\"{prompt[:150].replace(chr(10), ' ')}...\""
        )

        start = time.time()
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            latency = int((time.time() - start) * 1000)

            # Extract Ollama-specific metadata
            raw_meta = {
                "eval_count": data.get("eval_count"),
                "eval_duration": data.get("eval_duration"),
                "prompt_eval_count": data.get("prompt_eval_count"),
                "prompt_eval_duration": data.get("prompt_eval_duration"),
                "total_duration": data.get("total_duration"),
            }

            response_chars = len(content)
            tokens_generated = data.get("eval_count", "?")
            tokens_prompt = data.get("prompt_eval_count", "?")

            logger.info(
                f"LLM.response | provider=ollama | model={self.model}"
                f" | latency={latency}ms"
                f" | response_chars={response_chars}"
                f" | tokens_prompt={tokens_prompt}"
                f" | tokens_generated={tokens_generated}"
            )
            logger.debug(
                f"LLM.response | content_preview=\"{content[:200].replace(chr(10), ' ')}...\""
            )

            return LLMResponse(
                content=content,
                model=self.model,
                latency_ms=latency,
                raw_meta=raw_meta,
            )

        except httpx.TimeoutException as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(
                f"LLM.timeout | provider=ollama | model={self.model}"
                f" | elapsed={elapsed}ms | timeout_limit={settings.LLM_TIMEOUT}s"
                f" | prompt_chars={prompt_chars}"
            )
            raise
        except httpx.HTTPStatusError as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(
                f"LLM.http_error | provider=ollama | model={self.model}"
                f" | status={e.response.status_code}"
                f" | elapsed={elapsed}ms"
                f" | body={e.response.text[:300]}"
            )
            raise
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(
                f"LLM.error | provider=ollama | model={self.model}"
                f" | elapsed={elapsed}ms"
                f" | {type(e).__name__}: {e}"
            )
            raise

    async def health_check(self) -> bool:
        try:
            resp = await self.client.get(f"{self.base_url}/api/tags")
            healthy = resp.status_code == 200
            if healthy:
                models = [m.get("name", "?") for m in resp.json().get("models", [])]
                logger.debug(f"LLM.health | ollama OK | available_models={models}")
            else:
                logger.warning(f"LLM.health | ollama unhealthy | status={resp.status_code}")
            return healthy
        except Exception as e:
            logger.warning(f"LLM.health | ollama unreachable | {type(e).__name__}: {e}")
            return False


class OpenAIProvider(LLMProvider):
    """OpenAI API provider (future)."""

    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.OPENAI_MODEL
        self.client = httpx.AsyncClient(
            timeout=settings.LLM_TIMEOUT,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        logger.info(f"OpenAIProvider initialized | model={self.model} | key_set={bool(self.api_key)}")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        effective_temp = temperature or settings.LLM_TEMPERATURE
        effective_max_tokens = max_tokens or settings.LLM_MAX_TOKENS

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": effective_temp,
            "max_tokens": effective_max_tokens,
        }

        logger.info(
            f"LLM.request | provider=openai | model={self.model}"
            f" | prompt_chars={len(prompt)} | temperature={effective_temp}"
        )

        start = time.time()
        try:
            resp = await self.client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            latency = int((time.time() - start) * 1000)

            usage = data.get("usage", {})
            logger.info(
                f"LLM.response | provider=openai | model={self.model}"
                f" | latency={latency}ms"
                f" | tokens_prompt={usage.get('prompt_tokens', '?')}"
                f" | tokens_completion={usage.get('completion_tokens', '?')}"
                f" | tokens_total={usage.get('total_tokens', '?')}"
            )

            return LLMResponse(content=content, model=self.model, latency_ms=latency, raw_meta=usage)

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(f"LLM.error | provider=openai | {elapsed}ms | {type(e).__name__}: {e}")
            raise

    async def health_check(self) -> bool:
        healthy = bool(self.api_key)
        logger.debug(f"LLM.health | openai | key_configured={healthy}")
        return healthy


class AnthropicProvider(LLMProvider):
    """Anthropic API provider (future)."""

    def __init__(self):
        self.api_key = settings.ANTHROPIC_API_KEY
        self.model = settings.ANTHROPIC_MODEL
        self.client = httpx.AsyncClient(
            timeout=settings.LLM_TIMEOUT,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        logger.info(f"AnthropicProvider initialized | model={self.model} | key_set={bool(self.api_key)}")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt
        if temperature is not None:
            payload["temperature"] = temperature

        logger.info(
            f"LLM.request | provider=anthropic | model={self.model}"
            f" | prompt_chars={len(prompt)}"
        )

        start = time.time()
        try:
            resp = await self.client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["content"][0]["text"]
            latency = int((time.time() - start) * 1000)

            usage = data.get("usage", {})
            logger.info(
                f"LLM.response | provider=anthropic | model={self.model}"
                f" | latency={latency}ms"
                f" | tokens_input={usage.get('input_tokens', '?')}"
                f" | tokens_output={usage.get('output_tokens', '?')}"
            )

            return LLMResponse(content=content, model=self.model, latency_ms=latency, raw_meta=usage)

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(f"LLM.error | provider=anthropic | {elapsed}ms | {type(e).__name__}: {e}")
            raise

    async def health_check(self) -> bool:
        healthy = bool(self.api_key)
        logger.debug(f"LLM.health | anthropic | key_configured={healthy}")
        return healthy


# ── Factory ──────────────────────────────────────────────────────────────────

_provider_instance: Optional[LLMProvider] = None


def get_llm_provider() -> LLMProvider:
    """Get or create the LLM provider based on configuration."""
    global _provider_instance
    if _provider_instance is None:
        providers = {
            "ollama": OllamaProvider,
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
        }
        provider_cls = providers.get(settings.LLM_PROVIDER)
        if not provider_cls:
            logger.error(f"LLM.factory | Unknown provider: {settings.LLM_PROVIDER}")
            raise ValueError(f"Unknown LLM provider: {settings.LLM_PROVIDER}")
        _provider_instance = provider_cls()
        logger.info(f"LLM.factory | Provider created: {settings.LLM_PROVIDER}")
    return _provider_instance
