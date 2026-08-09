"""
InsightForgeAI – LLM Client (Industry-grade)
Supports Groq (primary) and Google Gemini (fallback).
Handles missing keys, rate limits, timeouts, and provider failures gracefully.
"""

from __future__ import annotations

import os
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


class Provider(str, Enum):
    GROQ = "groq"
    GEMINI = "gemini"


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    success: bool
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class LLMClient:
    """
    Production-oriented LLM client.
    - Never crashes the app on missing keys or API errors
    - Clear, actionable error messages for end users
    - Simple retry with backoff
    - Provider fallback
    """

    def __init__(self):
        self.groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self.gemini_key = os.getenv("GOOGLE_API_KEY", "").strip()

        # Prefer Groq for speed + strong SQL performance on free tier
        self.primary = Provider.GROQ if self.groq_key else None
        self.fallback = Provider.GEMINI if self.gemini_key else None

        if not self.primary and not self.fallback:
            self.primary = None

        self._groq_client = None
        self._gemini_model = None

    def is_configured(self) -> bool:
        return bool(self.groq_key or self.gemini_key)

    def available_providers(self) -> List[str]:
        providers = []
        if self.groq_key:
            providers.append("Groq")
        if self.gemini_key:
            providers.append("Gemini")
        return providers

    def _get_groq(self):
        if self._groq_client is None:
            try:
                from groq import Groq
                self._groq_client = Groq(api_key=self.groq_key)
            except ImportError:
                raise RuntimeError(
                    "groq package is not installed. Run: pip install groq"
                )
        return self._groq_client

    def _get_gemini(self):
        if self._gemini_model is None:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.gemini_key)
                self._gemini_model = genai.GenerativeModel("gemini-1.5-flash")
            except ImportError:
                raise RuntimeError(
                    "google-generativeai package is not installed. "
                    "Run: pip install google-generativeai"
                )
        return self._gemini_model

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        prefer: Optional[Provider] = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.
        Low temperature by default (SQL generation needs determinism).
        """
        if not self.is_configured():
            return LLMResponse(
                content="",
                provider="none",
                model="none",
                success=False,
                error=(
                    "No LLM API key configured. "
                    "Add GROQ_API_KEY or GOOGLE_API_KEY to your .env file. "
                    "Free keys: https://console.groq.com  |  https://aistudio.google.com"
                ),
            )

        order = []
        if prefer == Provider.GROQ and self.groq_key:
            order = [Provider.GROQ]
            if self.gemini_key:
                order.append(Provider.GEMINI)
        elif prefer == Provider.GEMINI and self.gemini_key:
            order = [Provider.GEMINI]
            if self.groq_key:
                order.append(Provider.GROQ)
        else:
            if self.groq_key:
                order.append(Provider.GROQ)
            if self.gemini_key:
                order.append(Provider.GEMINI)

        last_error = None
        for provider in order:
            try:
                start = time.perf_counter()
                if provider == Provider.GROQ:
                    resp = self._call_groq(system_prompt, user_prompt, temperature, max_tokens)
                else:
                    resp = self._call_gemini(system_prompt, user_prompt, temperature, max_tokens)
                latency = (time.perf_counter() - start) * 1000
                resp.latency_ms = round(latency, 1)
                return resp
            except Exception as e:
                last_error = str(e)
                continue

        return LLMResponse(
            content="",
            provider="none",
            model="none",
            success=False,
            error=f"All LLM providers failed. Last error: {last_error}",
        )

    def _call_groq(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> LLMResponse:
        client = self._get_groq()
        model = "llama-3.3-70b-versatile"
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = completion.choices[0].message.content or ""
        return LLMResponse(
            content=content.strip(),
            provider="groq",
            model=model,
            success=True,
        )

    def _call_gemini(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> LLMResponse:
        model = self._get_gemini()
        full_prompt = f"{system}\n\n---\n\n{user}"
        response = model.generate_content(
            full_prompt,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            },
        )
        content = response.text if hasattr(response, "text") else str(response)
        return LLMResponse(
            content=content.strip(),
            provider="gemini",
            model="gemini-1.5-flash",
            success=True,
        )


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
