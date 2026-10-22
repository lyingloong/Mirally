"""Small live-model routing check. Never creates a desktop executor or screenshots."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "UFO"))
from mirally.config import configure_ufo_config_root
from mirally.assistant import Assistant
from mirally.conversation import ConversationStore


def main():
    configure_ufo_config_root(ROOT)
    os.chdir(ROOT / "UFO")
    attempts = []
    def completion(messages):
        raw = Assistant._completion(messages)
        attempts.append(raw)
        path = ROOT / ".artifacts" / "assistant-live-attempts.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
        return raw
    assistant, memory = Assistant(completion), ConversationStore()
    cases = [("你好", "reply"), ("用两句话解释递归", "reply"),
             ("再换成一个生活中的例子", "reply"), ("怎么打开记事本？", "reply"),
             ("请帮我打开记事本", "desktop")]
    results = []
    for index, (request, expected) in enumerate(cases):
        decision = assistant.respond(request, memory.context())
        results.append({"request": request, "mode": decision.mode, "message": decision.message})
        print(f"{index + 1}: expected={expected}, actual={decision.mode}", flush=True)
        assert decision.mode == expected, "Unexpected intent routing"
        if index == 2:
            assert len(decision.message) > 35 and not any(text in decision.message for text in ("只返回JSON", "遵守JSON", "按格式输出")), "Follow-up lost the actual question"
        # A desktop acknowledgment is not a completed action and is not saved as one.
        if decision.mode == "reply":
            memory.begin(str(index), request, kind="conversation")
            memory.finish(str(index), "completed", decision.message)
    path = ROOT / ".artifacts" / "assistant-live.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: live configured model; conversation and desktop intent distinguished; no desktop actions.")


if __name__ == "__main__":
    main()
