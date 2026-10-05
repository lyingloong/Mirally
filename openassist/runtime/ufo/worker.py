"""One UFO task over private stdio; stdout is reserved for protocol frames."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import shutil
import tempfile
import threading
import time
import uuid

from openassist.core.execution import ExecutionCancelled, ExecutionRequest, ExecutionServices, Reobserve, Supplement

MAX_FRAME = 2_000_000


def prepare_worker_config(root, temporary_root):
    """Runtime policy from the product; credentials exclusively from the host."""
    source = root / "config/openassist/ufo"
    target = temporary_root / "config/openassist/ufo"
    target.mkdir(parents=True)
    for path in source.glob("*.yaml"):
        if path.name != "agents.yaml":
            shutil.copyfile(path, target / path.name)
    shutil.copyfile(source / "agents.yaml.template", target / "agents.yaml")
    return temporary_root


class WorkerChannel:
    def __init__(self, source, sink):
        self.source, self.sink = source, sink
        self.condition = threading.Condition()
        self.write_lock = threading.Lock()
        self.responses = {}
        self.pending = set()
        self.stopped = False
        self.paused = False
        self.revision = 0
        self.inputs = []
        self.answers = []
        self.first = None
        self.ready = threading.Event()

    def send(self, value):
        frame = json.dumps(value, ensure_ascii=False) + "\n"
        if len(frame.encode("utf-8")) > MAX_FRAME:
            raise ValueError("UFO protocol frame is too large")
        with self.write_lock:
            self.sink.write(frame)
            self.sink.flush()

    def listen(self):
        try:
            while True:
                line = self.source.readline(MAX_FRAME + 1)
                if not line:
                    break
                if len(line.encode("utf-8")) > MAX_FRAME:
                    raise ValueError("UFO protocol frame is too large")
                value = json.loads(line)
                with self.condition:
                    kind = value.get("type")
                    if kind == "run" and self.first is None:
                        self.first = value
                        self.ready.set()
                    elif kind == "cancel":
                        self.stopped = True
                        self.ready.set()
                    elif kind == "pause":
                        self.paused = True
                        self.revision += 1
                    elif kind == "resume":
                        self.paused = False
                        self.revision += 1
                    elif kind == "supplement":
                        text, receipt = value.get("text"), value.get("receipt_id")
                        if not isinstance(text, str) or not text or not isinstance(receipt, str):
                            raise ValueError("Invalid supplement")
                        self.inputs.append(Supplement(receipt, text))
                        self.revision += 1
                        self.send({"type": "accepted", "receipt_id": receipt})
                    elif kind == "response" and value.get("id") in self.pending:
                        self.responses[value["id"]] = value
                    else:
                        raise ValueError("Unexpected UFO protocol message")
                    self.condition.notify_all()
        except (OSError, ValueError, TypeError, KeyError):
            pass  # EOF/protocol loss revokes work; it never grants an action.
        finally:
            with self.condition:
                self.stopped = True
                self.ready.set()
                self.condition.notify_all()

    def check(self):
        with self.condition:
            if self.stopped:
                raise ExecutionCancelled()
            return self.revision

    def rpc(self, operation, **payload):
        identifier = uuid.uuid4().hex
        with self.condition:
            self.check()
            expected = self.revision
            self.pending.add(identifier)
            self.send({"type": "request", "id": identifier, "operation": operation,
                       "revision": expected, **payload})
            deadline = time.monotonic() + 300
            try:
                while identifier not in self.responses:
                    self.check()
                    if self.revision != expected:
                        raise Reobserve()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("The hosting runtime did not answer")
                    self.condition.wait(0.1)
                value = self.responses.pop(identifier)
                self.check()
                if self.revision != expected:
                    raise Reobserve()
                if not value.get("ok"):
                    raise RuntimeError(value.get("error") or "The hosting runtime refused the request")
                return value.get("value")
            finally:
                self.pending.discard(identifier)
                self.responses.pop(identifier, None)

    def take_inputs(self):
        with self.condition:
            values, self.inputs = self.inputs, []
            return values


class WorkerServices:
    def __init__(self, channel, frontend):
        self.channel, self.frontend = channel, frontend
        self.was_editing = False

    def checkpoint(self):
        waiting = False
        with self.channel.condition:
            while True:
                self.channel.check()
                editing = self.frontend.editing()
                if editing and not self.was_editing:
                    self.channel.revision += 1
                self.was_editing = editing
                if not self.channel.paused and not editing:
                    return self.channel.revision
                if not waiting:
                    self.channel.send({"type": "event", "event": {
                        "type": "phase", "phase": "paused",
                        "summary": "等待用户完成输入或恢复桌面执行"}})
                    waiting = True
                self.channel.condition.wait(0.1)

    def desktop_access(self, phase, *, expected_revision=None):
        if phase == "after":
            return self.channel.revision
        current = self.checkpoint()
        if expected_revision is not None and current != expected_revision:
            raise Reobserve()
        return current

    def ask(self, text):
        result = self.channel.rpc("ask", text=text)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise RuntimeError("No user answer was received")
        self.channel.answers.append(result["receipt_id"])
        return result["text"]

    def confirm(self, text, action):
        return self.channel.rpc("confirm", text=text, action=action) is True

    def authorize(self, name, arguments, kind, call_id, revision):
        if self.channel.check() != revision:
            raise Reobserve()
        if self.channel.rpc("authorize", name=name, arguments=arguments,
                            kind=kind, call_id=call_id) is not True:
            raise RuntimeError("Native tool was not authorized")

    def authorize_model(self, receipts, revision):
        if self.channel.check() != revision:
            raise Reobserve()
        if self.channel.rpc("authorize_model", receipt_ids=list(receipts)) is not True:
            raise ExecutionCancelled()

    def capture(self, phase):
        if phase == "before":
            if self.channel.rpc("authorize_observation") is not True:
                raise ExecutionCancelled()
            self.frontend.before()
        elif phase == "after":
            self.frontend.after()
        else:
            raise ValueError("Unknown capture phase")

    def build(self):
        return ExecutionServices(
            event=lambda event: self.channel.send({"type": "event", "event": event}),
            ask=self.ask, confirm=self.confirm, checkpoint=self.checkpoint,
            capture=self.capture, take_supplements=self.channel.take_inputs,
            desktop_access=self.desktop_access, cancelled=lambda: self.channel.stopped,
            revision=lambda: self.channel.revision,
            answer_receipt_ids=lambda: tuple(self.channel.answers),
            authorize_tool=self.authorize, authorize_model=self.authorize_model,
            excluded_process_ids=self.frontend.process_ids,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("UFO desktop execution requires Windows")
    source, sink = sys.stdin, sys.stdout
    # Native rich/log output must never be mistaken for a JSON protocol frame.
    sys.stdout = sys.stderr
    channel = WorkerChannel(source, sink)
    threading.Thread(target=channel.listen, daemon=True, name="ufo-control").start()
    channel.send({"type": "ready", "version": 1})
    channel.ready.wait()
    if not channel.first:
        return
    root = args.root.resolve()
    from openassist.runtime.ufo.config import configure_ufo_config_root
    from openassist.platform.frontend_registry import FrontendCapture
    from openassist.runtime.ufo.adapter import UfoExecutor
    from openassist.runtime.ufo.model_bridge import selected_model
    data = channel.first
    frontend = None
    scope = ExitStack()
    try:
        temporary = Path(scope.enter_context(tempfile.TemporaryDirectory(prefix="mirally-ufo-")))
        configure_ufo_config_root(prepare_worker_config(root, temporary), ufo_root=root / "UFO")
        os.chdir(root / "UFO")
        request = ExecutionRequest(data["run_id"], data["text"], data.get("context", {}))
        frontend = FrontendCapture(root)
        services = WorkerServices(channel, frontend)
        from openassist.core.assistant import Assistant
        from openassist.core.execution import TaskResult
        with selected_model(data["route"], lambda usage: channel.send({"type": "usage", **usage})) as model:
            additions, receipts = [], []
            while True:
                expected = services.checkpoint()
                updates = channel.take_inputs()
                additions.extend(item.text for item in updates)
                receipts.extend(item.receipt_id for item in updates)
                def completion(messages):
                    services.authorize_model(receipts, expected)
                    channel.send({"type": "event", "event": {"type": "phase", "phase": "planning",
                                 "summary": "正在理解当前要求"}})
                    instructions = request.conversation_context.get("engine_instructions")
                    if instructions:
                        messages = [*messages[:1], {"role": "system", "content": instructions}, *messages[1:]]
                    value = model.chat_completion(messages)[0][0]
                    channel.check()
                    if channel.revision != expected:
                        raise Reobserve()
                    return value
                try:
                    decision = Assistant(completion).respond(request.text, request.conversation_context, additions)
                    if channel.revision != expected:
                        raise Reobserve()
                    channel.send({"type": "event", "event": {"type": "context_applied",
                                 "receipt_ids": receipts, "source": "supplement"}})
                    break
                except Reobserve:
                    continue
            if decision.mode == "reply":
                result = TaskResult("completed", decision.message)
            else:
                request = ExecutionRequest(request.task_id, request.text, request.conversation_context,
                                           tuple(additions))
                result = asyncio.run(UfoExecutor(root).run(request, services.build()))
        value = result.as_dict()
        key = data.get("route", {}).get("key")
        if isinstance(key, str) and key:
            value["message"] = value["message"].replace(key, "[REDACTED]")
        channel.send({"type": "result", "result": value})
    except ExecutionCancelled:
        channel.send({"type": "result", "result": {"status": "cancelled", "message": "执行已停止"}})
    except Exception as error:
        # Provider exceptions may include credentials; don't echo them to the UI.
        channel.send({"type": "result", "result": {"status": "failed",
                     "message": "UFO 执行初始化失败，请检查依赖与执行配置。",
                     "reason": type(error).__name__}})
    finally:
        try:
            if frontend is not None:
                frontend.after()
        finally:
            scope.close()


if __name__ == "__main__":
    main()
