"""Speech-to-text provider interfaces for Mirally."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

import requests


class SpeechProviderError(RuntimeError):
    """Raised when a speech provider cannot return a transcription."""


@dataclass(frozen=True)
class Transcript:
    """Normalized speech-to-text result exposed to the product layer."""

    text: str
    language: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class SpeechProvider(Protocol):
    """Provider contract used by recording and UI layers."""

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: Optional[str] = None,
    ) -> Transcript:
        ...


class OpenAICompatibleSpeechProvider:
    """Call an OpenAI-compatible ``audio/transcriptions`` endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        response_format: str = "json",
        session: Optional[requests.Session] = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("Speech API base_url must not be empty")
        if not api_key.strip():
            raise ValueError("Speech API api_key must not be empty")
        if not model.strip():
            raise ValueError("Speech API model must not be empty")
        if response_format not in {"json", "text"}:
            raise ValueError("Speech API response_format must be 'json' or 'text'")

        self.endpoint = base_url.rstrip("/") + "/audio/transcriptions"
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.response_format = response_format
        self.session = session or requests.Session()

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: Optional[str] = None,
    ) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise SpeechProviderError(f"Audio file not found: {audio_path}")

        mime_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
        data: Dict[str, str] = {
            "model": self.model,
            "response_format": self.response_format,
        }
        if language and language.lower() != "auto":
            data["language"] = language

        try:
            with audio_path.open("rb") as audio_file:
                response = self.session.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=data,
                    files={"file": (audio_path.name, audio_file, mime_type)},
                    timeout=self.timeout_seconds,
                )
        except requests.RequestException as exc:
            raise SpeechProviderError(f"Speech API request failed: {exc}") from exc

        if not response.ok:
            detail = response.text.strip()[:500]
            raise SpeechProviderError(
                f"Speech API returned HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )

        if self.response_format == "text":
            text = response.text.strip()
            raw: Optional[Dict[str, Any]] = None
        else:
            try:
                payload = response.json()
            except ValueError as exc:
                raise SpeechProviderError("Speech API returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise SpeechProviderError("Speech API returned an invalid response")
            text = str(payload.get("text") or "").strip()
            raw = payload

        if not text:
            raise SpeechProviderError("Speech API returned an empty transcription")
        return Transcript(text=text, language=language, raw=raw)
