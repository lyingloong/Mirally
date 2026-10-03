"""Exercise the actual conversation UI with deterministic offline responses."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab
from openassist.assistant import AssistantDecision
from openassist.conversation import ConversationStore
from openassist.shell import OpenAssistShell


def main():
    output = ROOT / ".artifacts" / "interface"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openassist-chat-") as directory:
        app = OpenAssistShell(Path(directory), demo=True, startup=False)
        app.conversation = app.controller.conversation = ConversationStore(Path(directory) / "conversations")
        errors, contexts = [], []
        app.root.report_callback_exception = lambda *error: errors.append(error)
        answers = ["你好！很高兴见到你。\n\n我们可以讨论一个想法，或者一起把事情做好。",
                   "递归就是：把一个问题拆成更小的同类问题，直到遇到能够直接解决的情况。\n\n比如俄罗斯套娃，打开一层还有一层，最小的那个就是终止条件。",
                   "当然，换成日常例子：\n\n找钥匙时，你先检查一个盒子；如果里面还有小盒子，就用同样的方法继续检查，直到找到钥匙或没有盒子。\n\n每次处理方式一样，但范围越来越小。"]

        def respond(request, context, additions):
            contexts.append(context)
            time.sleep(0.3)
            return AssistantDecision("reply", answers[len(contexts) - 1])

        app.controller.assistant = Mock(respond=respond)
        app.controller.runner_factory = Mock(side_effect=AssertionError("Conversation touched the desktop"))
        app.controller.desktop_available = False

        def pump(seconds=0.1):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.root.update()
                time.sleep(0.01)
            assert not errors, errors

        def snapshot(name):
            pump(0.2)
            x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + app.root.winfo_width(), y + app.root.winfo_height())).save(output / name)

        try:
            pump()
            geometry = app.root.geometry()
            for index, request in enumerate(("你好", "给我解释一下递归", "再换一个日常例子呢？")):
                app.composer.insert("1.0", request)
                app._submit()
                pump(0.1)
                assert app.edge.mode == "workspace"
                assert app.root.geometry() == geometry, "Question shrank/moved the window"
                assert app.chat_text.winfo_ismapped()
                assert not app.controls.winfo_ismapped(), "Desktop controls dominate ordinary chat"
                deadline = time.monotonic() + 3
                while app.controller.active and time.monotonic() < deadline:
                    pump(0.04)
                pump()
                assert app._phase == "completed"
                assert answers[index] in app.chat_text.get("1.0", "end")
                assert not app.stop_reply_button.winfo_ismapped()
            snapshot("18-conversation.png")
            assert contexts[-1]["previous_tasks"][-1]["result"] == answers[1]
            restored = ConversationStore(Path(directory) / "conversations")
            assert restored.snapshot()["tasks"][-1]["result"] == answers[-1]
            app.root.geometry(f"{round(450 * app.scale)}x{round(650 * app.scale)}")
            pump()
            assert app.chat_text.winfo_height() > round(140 * app.scale)
            assert app.send_button.winfo_rooty() + app.send_button.winfo_height() < app.root.winfo_rooty() + app.root.winfo_height()
            snapshot("19-conversation-small.png")
            app._new_conversation()
            assert "俄罗斯套娃" not in app.chat_text.get("1.0", "end")
            print("PASS: real chat UI, greeting/question/follow-up, stable window, retained context, small layout, new session.")
            print("Screenshots:", output)
        finally:
            app.controller.stop()
            while app.controller.active:
                pump(0.04)
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
