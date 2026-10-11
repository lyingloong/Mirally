"""Complete clipboard reads with an isolated deadline and sequence checks."""

import json
import subprocess
import sys


class WindowsClipboardSource:
    available = sys.platform == "win32"

    def read(self, *, sequence: int = 0, after_sequence: int = 0,
             timeout_ms: int = 1500, max_chars: int = 12000) -> dict:
        if not self.available:
            return {"status": "unavailable", "missing_reasons": ["not_windows"]}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "openassist.platform.activity.clipboard_worker",
                 "--sequence", str(sequence), "--after-sequence", str(after_sequence),
                 "--max-chars", str(max_chars)], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=timeout_ms / 1000,
            )
            if result.returncode:
                raise ValueError("clipboard worker response")
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise ValueError("clipboard worker response")
            value["text"] = str(value.get("text", "")) if value.get("status") == "ok" else None
            return value
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "missing_reasons": ["worker_deadline"]}
        except (OSError, ValueError) as error:
            return {"status": "error", "missing_reasons": [type(error).__name__]}
