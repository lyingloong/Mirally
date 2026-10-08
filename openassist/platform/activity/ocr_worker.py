"""Windows.Media.Ocr worker; invoked only by ``ocr.py``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from io import BytesIO
from pathlib import Path


def _engine_for_languages(tags: list[str]):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    available = list(getattr(OcrEngine, "available_recognizer_languages", ()) or ())
    available_tags = {str(language.language_tag).lower(): language for language in available}
    for tag in tags:
        requested = str(tag).lower()
        for available_tag, language in available_tags.items():
            if available_tag == requested or available_tag.startswith(requested + "-") or requested.startswith(available_tag + "-"):
                engine = OcrEngine.try_create_from_language(language)
                if engine is not None:
                    return engine
        try:
            engine = OcrEngine.try_create_from_language(Language(tag))
        except Exception:
            engine = None
        if engine is not None:
            return engine
    return OcrEngine.try_create_from_user_profile_languages()


def _rect(value) -> dict:
    return {
        "x": float(getattr(value, "x", 0.0)),
        "y": float(getattr(value, "y", 0.0)),
        "width": float(getattr(value, "width", 0.0)),
        "height": float(getattr(value, "height", 0.0)),
    }


def run(image_path: Path, languages: list[str], max_dimension: int) -> dict:
    started = time.monotonic()
    try:
        from PIL import Image
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        with Image.open(image_path) as image:
            image = image.convert("RGBA")
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            encoded = BytesIO()
            image.save(encoded, format="PNG")
            width, height = image.size
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        writer.write_bytes(encoded.getvalue())
        writer.store_async().get()
        writer.flush_async().get()
        writer.detach_stream()
        stream.seek(0)
        decoder = BitmapDecoder.create_async(stream).get()
        bitmap = decoder.get_software_bitmap_converted_async(
            BitmapPixelFormat.BGRA8,
            BitmapAlphaMode.PREMULTIPLIED,
        ).get()
        engine = _engine_for_languages(languages)
        if engine is None:
            return {
                "status": "unavailable",
                "engine": "windows.native",
                "duration_ms": (time.monotonic() - started) * 1000,
                "missing_reasons": ["language_pack_missing"],
            }
        result = engine.recognize_async(bitmap).get()
        lines = []
        for line in result.lines:
            words = []
            for word in line.words:
                words.append({"text": str(word.text), "bounds": _rect(word.bounding_rect)})
            lines.append({"text": str(line.text), "words": words})
        return {
            "status": "ok",
            "text": str(result.text or ""),
            "lines": lines,
            "engine": "windows.native",
            "language": str(engine.recognizer_language.language_tag),
            "image_size": [width, height],
            "duration_ms": (time.monotonic() - started) * 1000,
            "missing_reasons": [],
        }
    except Exception as error:
        return {
            "status": "error",
            "engine": "windows.native",
            "duration_ms": (time.monotonic() - started) * 1000,
            "missing_reasons": [type(error).__name__],
        }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--language", action="append", default=[])
    parser.add_argument("--max-dimension", type=int, default=1_920)
    args = parser.parse_args()
    print(json.dumps(run(args.image, args.language, max(320, min(8_192, args.max_dimension))), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
