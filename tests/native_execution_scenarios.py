"""Subprocess harness for test_ufo_native_execution; not a manual desktop test."""

from contextlib import ExitStack
import json
import logging
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


ROOT, UFO_ROOT, SCENARIO = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(ROOT))

from mirally.runtime.config import configure_ufo_config_root

configure_ufo_config_root(Path.cwd(), ufo_root=UFO_ROOT)

from PIL import Image
from pywinauto.win32structures import RECT

from mirally.core.assistant import Assistant
from mirally.core.conversation import ConversationStore
from mirally.runtime.native_compat import native_compatibility


BODY = "Integration fixture body: preserve this exact sentence."
UPDATED_BODY = "Updated requirement: save a draft only."
ANSWER = "The test form now contains the requested text."
OWN_NAME = "MIRALLY_SELF_MUST_NOT_REACH_MODEL"


class Harness:
    def __init__(self):
        self.controller = None
        self.events = []
        self.prompts = []
        self.actions = []
        self.focus = []
        self.captures = []
        self.isolated = 0
        self.app_calls = 0
        self.host_calls = 0
        self.model_waiting = threading.Event()
        self.model_release = threading.Event()
        self.capture_count_at_change = 0
        self.changed = False
        self.resumed = False
        self.final_finished = False
        self.failed_final_captures = 0
        self.entry = Window(self, "Test editor", 424242, 101, "Edit")
        self.window = Window(self, "Integration test window", 424242, 100)
        self.own = Window(self, OWN_NAME, os.getpid(), 102)

    def image(self, source):
        assert self.isolated > 0, f"Native {source} capture bypassed the UI isolation handshake"
        if SCENARIO == "final_capture_failure" and self.final_finished:
            self.failed_final_captures += 1
            raise RuntimeError("Deterministic final capture failure")
        self.captures.append(source)
        return Image.new("RGB", (640, 480), "white")

    def completion(self, messages, agent, use_backup_engine=False):
        # This is the sole scripted model boundary. Prompt construction, response
        # parsing, native state transitions and action execution remain real.
        name = getattr(agent, "value", str(agent))
        content = json.dumps(messages, ensure_ascii=False)
        self.prompts.append((name, content))
        assert OWN_NAME not in content, "Mirally leaked through native control/window enumeration"
        if "HOST" in name.upper():
            self.host_calls += 1
            if not self.actions or (SCENARIO == "partial_failure" and self.host_calls == 2):
                response = {
                    "observation": "The test editor is available.", "thought": "Use the test editor.",
                    "status": "ASSIGN", "current_subtask": (
                        "Perform the second requested subtask." if self.actions
                        else "Fill the test editor with the requested body."),
                    "message": ["Use the body supplied by the user."], "plan": [],
                    "function": "select_application_window",
                    "arguments": {"id": "1", "name": self.window.name},
                    "result": "The test window is selected for editing.",
                }
            else:
                self.final_finished = True
                response = {
                    "observation": "The form contains the requested text.",
                    "thought": "The requested work is complete.", "status": "FINISH",
                    "plan": [], "result": None if SCENARIO == "empty_result" else ANSWER,
                }
        else:
            self.app_calls += 1
            if SCENARIO == "model_error" or (SCENARIO == "partial_failure" and self.app_calls == 2):
                raise RuntimeError("Deterministic model provider failure")
            if self.app_calls == 1 and SCENARIO in {"model_stop", "supplement", "takeover"}:
                self.model_waiting.set()
                assert self.model_release.wait(15), "Test driver failed to release the model"
            body = UPDATED_BODY if self.app_calls > 1 and SCENARIO in {"supplement", "confirmation_change"} else BODY
            actions = [{"function": "set_edit_text", "arguments": {
                "id": "1", "name": self.entry.name, "text": body,
            }, "status": "FINISH"}]
            if SCENARIO == "batch_stop":
                actions[0]["status"] = "CONTINUE"
                actions.append({"function": "set_edit_text", "arguments": {
                    "id": "1", "name": self.entry.name, "text": "MUST NOT EXECUTE",
                }, "status": "FINISH"})
            if SCENARIO == "confirmation_change" and self.app_calls == 1:
                actions[0]["status"] = "CONFIRM"
            response = {
                "observation": "The editor is empty.", "thought": "Fill the requested text.",
                "action": actions, "plan": [], "result": None if SCENARIO == "empty_result" else ANSWER,
                "save_screenshot": {"save": False, "reason": ""},
            }
        return json.dumps(response), 0.0

    def drain(self):
        self.controller.pulse()
        while True:
            try:
                event = self.controller.events.get_nowait()
            except queue.Empty:
                return
            self.events.append(event)
            if (SCENARIO == "confirmation_change" and not self.changed
                    and event["type"] == "state" and event.get("phase") == "waiting_confirmation"):
                # Deliberately serialize an accepted answer before revision
                # changes. The worker may dequeue it, but cannot validate it
                # until this lock is released with the old plan invalidated.
                with self.controller._condition:
                    assert self.controller.reply(event["request_id"], True)
                    assert self.controller.supplement(UPDATED_BODY)
                    self.changed = True
                    self.capture_count_at_change = len(self.captures)
            if event["type"] == "ui":
                call = event["call"]
                if not call.expired:
                    if call.operation == "capture_begin":
                        self.isolated += 1
                    elif call.operation == "capture_end":
                        self.isolated -= 1
                        assert self.isolated >= 0
                call.done.set()

    def drive(self):
        deadline = time.monotonic() + 55
        while self.controller.active:
            self.drain()
            if self.model_waiting.is_set() and not self.changed:
                self.changed = True
                self.capture_count_at_change = len(self.captures)
                if SCENARIO == "model_stop":
                    self.controller.stop()
                    assert self.controller.phase == "stopping"
                elif SCENARIO == "takeover":
                    self.controller.pause(takeover=True)
                    assert self.controller.phase in {"pausing", "takeover"}
                else:
                    assert self.controller.supplement(UPDATED_BODY)
                self.model_release.set()
            if SCENARIO == "takeover" and self.changed and not self.resumed and self.controller.phase == "takeover":
                assert not self.actions, "A stale action ran before granting takeover"
                self.capture_count_at_change = len(self.captures)
                self.resumed = self.controller.resume()
                assert self.resumed
            assert time.monotonic() < deadline, "Native scenario failed to terminate"
            self.controller.worker.join(0.01)
        self.drain()


class Window:
    """OS-boundary stand-in; native UI receiver still calls its real methods."""
    def __init__(self, harness, name, pid, handle, kind="Window"):
        self.harness, self.name, self.pid, self.handle = harness, name, pid, handle
        self.text = name
        self.element_info = SimpleNamespace(
            name=name, class_name="IntegrationFixture", control_type=kind,
            control_id=handle, automation_id=str(handle), process_id=pid,
            rectangle=RECT(0, 0, 640, 480),
        )

    def rectangle(self):
        return self.element_info.rectangle

    def window_text(self):
        return self.text

    def class_name(self):
        return "IntegrationFixture"

    friendly_class_name = class_name

    def process_id(self):
        return self.pid

    def is_visible(self):
        return True

    is_enabled = is_normal = is_active = is_visible

    def is_minimized(self):
        return False

    is_maximized = is_minimized

    def set_focus(self):
        self.harness.focus.append(self.handle)

    def capture_as_image(self):
        if SCENARIO == "capture_fallback":
            raise RuntimeError("Deterministic window capture failure")
        return self.harness.image("window")

    def set_edit_text(self, text):
        self.text = text
        self.harness.actions.append(text)
        if SCENARIO == "batch_stop":
            self.harness.controller.stop()
        return text


def main():
    harness = Harness()
    with ExitStack() as stack:
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex

        def local_only(method):
            def connect(sock, address):
                # Windows asyncio creates its wake-up socketpair on loopback.
                # No provider, HTTP, or other remote connection is permitted.
                if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                    return method(sock, address)
                raise AssertionError("Unexpected network access in deterministic native integration test")
            return connect

        # Local MCP is in memory and needs no socket. Blocking these endpoints
        # catches accidental provider/API calls before they leave this process.
        stack.enter_context(patch.object(socket.socket, "connect", local_only(real_connect)))
        stack.enter_context(patch.object(socket.socket, "connect_ex", local_only(real_connect_ex)))
        stack.enter_context(patch("config.config_loader.get_galaxy_config", side_effect=AssertionError("Galaxy is out of scope")))
        with native_compatibility():
            from ufo.automator.ui_control import inspector, screenshot, controller as ui_controller
            from ufo.llm import llm_call
            from ufo.client.mcp.local_servers.ui_mcp_server import UIServerState
            from ufo.module.sessions.session import Session
            from ufo.agents.agent.basic import BasicAgent
            from ufo.client.computer import Computer

        previous_handle = BasicAgent.handle
        previous_action = Computer._run_action
        previous_capture = screenshot.ControlPhotographer.capture
        previous_windows = inspector.ControlInspectorFacade.get_desktop_windows
        previous_handlers = logging.getLogger().handlers[:]
        if SCENARIO == "prepared_route":
            from mirally.runtime.ufo.model_bridge import selected_model
            from ufo.llm.base import BaseService
            previous_factory = vars(BaseService)["get_service"]
            selected_calls = []
            usage = []
            class SyntheticClient:
                def __init__(self, **options):
                    assert options["api_key"] == "synthetic-key"
                    assert options["base_url"] == "http://127.0.0.1:1/v1"
                    self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.complete))
                def with_options(self, **options):
                    return self
                def close(self):
                    pass
                def complete(self, **options):
                    assert options["model"] == "synthetic-vision"
                    assert options["temperature"] == 0.25
                    agent = "APP_AGENT" if len(selected_calls) == 1 else "HOST_AGENT"
                    selected_calls.append(agent)
                    text, _ = harness.completion(options["messages"], agent)
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                                           usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30))
            stack.enter_context(patch("openai.OpenAI", SyntheticClient))
            stack.enter_context(selected_model({"api": "openai-completions", "model": "synthetic-vision",
                "base_url": "http://127.0.0.1:1/v1", "key": "synthetic-key", "parameters": {"temperature": 0.25}}, usage.append))
        else:
            stack.enter_context(patch.object(llm_call, "get_completion", harness.completion))
        stack.enter_context(patch.object(inspector.UIABackendStrategy, "get_desktop_windows",
                                        lambda *args, **kwargs: [harness.window, harness.own]))
        stack.enter_context(patch.object(inspector.UIABackendStrategy, "find_control_elements_in_descendants",
                                        lambda *args, **kwargs: [harness.entry, harness.own]))
        stack.enter_context(patch.object(inspector.ControlInspectorFacade, "get_application_root_name",
                                        staticmethod(lambda window: "INTEGRATION.EXE")))
        stack.enter_context(patch.object(screenshot.ImageGrab, "grab", lambda *args, **kwargs: harness.image("desktop")))
        stack.enter_context(patch.object(screenshot, "_win32_print_window",
                                        lambda *args: None if SCENARIO == "capture_fallback" else harness.image("print_window")))
        stack.enter_context(patch.object(screenshot, "_win32_grab_screen", lambda *args: harness.image("win32_desktop")))
        for method in ("click", "write", "hotkey", "press", "keyDown", "keyUp", "moveTo", "dragTo", "scroll", "vscroll", "hscroll"):
            stack.enter_context(patch.object(ui_controller.pyautogui, method, side_effect=AssertionError("Unexpected real input")))
        stack.enter_context(patch.object(ui_controller.keyboard, "send_keys", side_effect=AssertionError("Unexpected real keyboard input")))

        from mirally.application import create_controller
        conversation = ConversationStore(Path.cwd() / "conversations")

        def conversational_completion(messages):
            if messages[-1]["content"] == "Write a test paragraph.":
                return json.dumps({"mode": "reply", "message": BODY})
            return json.dumps({"mode": "desktop", "message": "Filling the test form."})

        assistant = Assistant(completion=conversational_completion)
        harness.controller = create_controller(product_root=Path.cwd(), assistant=assistant,
                                               conversation=conversation, ui_timeout=5.0)
        assert harness.controller.start("Write a test paragraph.")
        harness.drive()
        assert harness.controller.phase == "completed"
        assert not harness.prompts and not harness.captures and not harness.actions, "Ordinary conversation touched the desktop"
        assert conversation.snapshot()["tasks"][-1]["result"] == BODY
        harness.events.clear()
        assert harness.controller.start("Put the paragraph above in the test form.")
        harness.drive()

        assert harness.host_calls, "The native Host processor did not run"
        assert harness.app_calls, "The native App processor did not run"
        assert harness.captures, "The real screenshot tool was not invoked"
        assert harness.isolated == 0, "Capture isolation was not restored"
        assert harness.own.handle not in harness.focus, "Mirally was targeted"
        assert BasicAgent.handle is previous_handle, "Native handle patch leaked"
        assert Computer._run_action is previous_action, "Native tool boundary patch leaked"
        assert screenshot.ControlPhotographer.capture is previous_capture, "Native capture patch leaked"
        assert inspector.ControlInspectorFacade.get_desktop_windows is previous_windows, "Native window filter leaked"
        assert UIServerState().selected_app_window is None and UIServerState().control_dict is None, "Task targets leaked into a later run"
        assert logging.getLogger().handlers == previous_handlers, "Task changed unrelated logging handlers"
        assert "galaxy" not in sys.modules
        phases = [event["phase"] for event in harness.events if event["type"] == "state"]
        assert {"observing", "planning", "executing"} <= set(phases), phases
        terminal = [phase for phase in phases if phase in {"completed", "partial", "incomplete", "failed", "cancelled"}]
        assert terminal == [harness.controller.phase], "The product emitted duplicate or inconsistent terminal states"
        event_files = list(Path.cwd().glob("logs/**/mirally-events.jsonl"))
        assert len(event_files) == 1, event_files
        native_events = [json.loads(line) for line in event_files[0].read_text(encoding="utf-8").splitlines()]
        sequence = [event["sequence"] for event in native_events]
        assert sequence == sorted(set(sequence)), "Native events are not ordered uniquely"
        finished = [event for event in native_events if event["type"] == "finished"]
        assert len(finished) == 1 and finished[0]["status"] == harness.controller.phase, finished
        if SCENARIO in {"model_stop", "batch_stop"}:
            assert harness.controller.phase == "cancelled", harness.controller.phase
            expected = [] if SCENARIO == "model_stop" else [BODY]
            assert harness.actions == expected, harness.actions
        elif SCENARIO in {"model_error", "empty_result", "partial_failure"}:
            expected_status = {"model_error": "failed", "empty_result": "incomplete", "partial_failure": "partial"}[SCENARIO]
            assert harness.controller.phase == expected_status, (harness.controller.phase, finished)
            assert harness.actions == ([] if SCENARIO == "model_error" else [BODY]), harness.actions
            assert conversation.snapshot()["tasks"][-1]["status"] == expected_status
            if SCENARIO == "partial_failure":
                assert ANSWER in conversation.snapshot()["tasks"][-1]["result"], finished
        else:
            assert harness.controller.phase == "completed", (harness.controller.phase, harness.events[-8:])
            expected = [UPDATED_BODY] if SCENARIO in {"supplement", "confirmation_change"} else [BODY]
            assert harness.actions == expected, harness.actions
            result = conversation.snapshot()["tasks"][-1]["result"]
            assert ANSWER in result, result
            assert all(BODY in prompt for _, prompt in harness.prompts), "History body did not reach every native model request"
        if SCENARIO in {"supplement", "confirmation_change"}:
            assert harness.app_calls >= 2, "The stale plan was not replanned"
            assert len(harness.captures) > harness.capture_count_at_change, "No observation after requirement change"
            app_prompts = [prompt for name, prompt in harness.prompts if "APP" in name.upper()]
            assert UPDATED_BODY not in app_prompts[0] and UPDATED_BODY in app_prompts[-1]
            receipts = [event for event in harness.events if event["type"] == "receipt"]
            assert [event["status"] for event in receipts] == ["queued", "applied"], receipts
            assert receipts[0]["receipt_id"] == receipts[1]["receipt_id"]
        if SCENARIO == "takeover":
            assert harness.resumed, "The executor never granted safe takeover"
            assert phases.index("pausing") < phases.index("takeover"), phases
            assert harness.app_calls >= 2, "Resume reused the stale plan"
            assert len(harness.captures) > harness.capture_count_at_change, "Resume did not collect a fresh observation"
        if SCENARIO == "capture_fallback":
            assert all(source == "desktop" for source in harness.captures), harness.captures
            assert len(harness.captures) >= 3, "Window fallback was not exercised"
        if SCENARIO == "final_capture_failure":
            assert harness.failed_final_captures, "The final capture failure was not exercised"
    if SCENARIO == "prepared_route":
        assert vars(BaseService)["get_service"] is previous_factory, "Prepared model patch leaked"
        assert selected_calls == ["HOST_AGENT", "APP_AGENT", "HOST_AGENT"], selected_calls
        assert len(usage) == 3 and all(item["total"] == 30 for item in usage), usage
    print(f"native execution OK: {SCENARIO}")


if __name__ == "__main__":
    main()
