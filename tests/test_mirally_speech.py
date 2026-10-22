from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from mirally.config import configure_ufo_config_root, load_speech_config
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
    def test_configures_product_ufo_root_without_legacy_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_root = root / "product"
            (product_root / "config" / "mirally" / "ufo").mkdir(parents=True)
            legacy = root / "UFO" / "config" / "ufo" / "agents.yaml"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("APP_AGENT:\n  API_KEY: test\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=False):
                configured = configure_ufo_config_root(product_root)

            self.assertEqual(configured, product_root / "config" / "mirally")
            self.assertFalse(
                (product_root / "config" / "mirally" / "ufo" / "agents.yaml").exists()
            )
            self.assertTrue(legacy.exists())

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
