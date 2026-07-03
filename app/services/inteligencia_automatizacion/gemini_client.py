import asyncio
import base64
import json
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings


@dataclass(slots=True)
class GeminiCallResult:
    ok: bool
    latency_ms: int
    output_text: str
    error: str | None = None
    model: str | None = None


def _extract_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        return ""
    first = parts[0]
    if isinstance(first, dict) and isinstance(first.get("text"), str):
        return first["text"]
    return ""


def _try_parse_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    sliced = raw[start : end + 1]
    try:
        parsed = json.loads(sliced)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


class GeminiClient:
    def __init__(self) -> None:
        self._settings = get_settings()

    def _api_key(self) -> str:
        token = (self._settings.google_api_key or self._settings.ai_api_key or "").strip()
        if len(token) < 10:
            raise RuntimeError("GOOGLE_API_KEY no configurado")
        return token

    def _timeout(self) -> float:
        try:
            return float(self._settings.ai_timeout_s)
        except Exception:
            return 2.0

    async def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        inline_bytes: bytes | None = None,
        inline_mime_type: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 512,
    ) -> GeminiCallResult:
        api_key = self._api_key()
        base_url = "https://generativelanguage.googleapis.com/v1beta"
        url = f"{base_url}/models/{model}:generateContent"

        parts: list[dict[str, Any]] = [{"text": user}]
        if inline_bytes:
            mime = (inline_mime_type or "application/octet-stream").strip() or "application/octet-stream"
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime,
                        "data": base64.b64encode(inline_bytes).decode("ascii"),
                    }
                }
            )

        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }

        started = time.perf_counter()
        last_error: str | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self._timeout()) as client:
                    response = await client.post(
                        url,
                        params={"key": api_key},
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                response.raise_for_status()
                data = response.json()
                text = _extract_text(data if isinstance(data, dict) else {})
                latency_ms = int((time.perf_counter() - started) * 1000)
                return GeminiCallResult(ok=True, latency_ms=latency_ms, output_text=text, model=model)
            except Exception as exc:
                last_error = str(exc)[:500]
                if attempt == 1:
                    break
                sleep_s = min(0.35, 0.15 * math.pow(2, attempt))
                await asyncio.sleep(sleep_s)

        latency_ms = int((time.perf_counter() - started) * 1000)
        return GeminiCallResult(ok=False, latency_ms=latency_ms, output_text="", error=last_error, model=model)

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        inline_bytes: bytes | None = None,
        inline_mime_type: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 768,
    ) -> tuple[GeminiCallResult, dict[str, Any] | None]:
        res = await self.generate(
            model=model,
            system=system,
            user=user,
            inline_bytes=inline_bytes,
            inline_mime_type=inline_mime_type,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return res, _try_parse_json(res.output_text)
