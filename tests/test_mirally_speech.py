from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from mirally.config import load_speech_config
from mirally.speech import OpenAICompatibleSpeechProvider


class _Response:
    ok = True
    status_code = 200
    text = '{"text":"打开记事本"}'

    def json(self):
        return {"text": "打开记事本", "usage": {"seconds": 1}}


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _Response()


class SpeechConfigTests(unittest.TestCase):
    def test_loads_env_api_key_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "speech.yaml"
            path.write_text(
                yaml.safe_dump(
                    {
                        "enabled": True,
                        "provider": "openai_compatible",
                        "base_url": "https://speech.example.com/v1",
                        "model": "example-asr",
                        "api_key": "${MIRALLY_SPEECH_API_KEY}",
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"MIRALLY_SPEECH_API_KEY": "test-key"}):
                config = load_speech_config(Path(directory), path=path)

        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.language, "auto")
        self.assertEqual(config.response_format, "json")

    def test_provider_uploads_audio_and_normalizes_response(self):
        session = _Session()
        provider = OpenAICompatibleSpeechProvider(
            base_url="https://speech.example.com/v1",
            api_key="test-key",
            model="example-asr",
            session=session,
        )
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "sample.wav"
            audio_path.write_bytes(b"RIFF-test")
            transcript = provider.transcribe(audio_path, language="auto")

        self.assertEqual(transcript.text, "打开记事本")
        self.assertEqual(len(session.calls), 1)
        args, kwargs = session.calls[0]
        self.assertEqual(args[0], "https://speech.example.com/v1/audio/transcriptions")
        self.assertEqual(kwargs["data"]["model"], "example-asr")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")


if __name__ == "__main__":
    unittest.main()
