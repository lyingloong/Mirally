"""Structured native execution events and conservative business results."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from mirally.core.execution import TaskResult
from mirally.core.text import message_text


class EventStream:
    def __init__(self, task_id, callback):
        self.task_id = task_id
        self.callback = callback
        self._lock = threading.RLock()
        self._sequence = 0
        self._file = None

    def open(self, directory):
        path = Path(directory) / "mirally-events.jsonl"
        self._file = path.open("a", encoding="utf-8")
        return str(path)

    def emit(self, kind, **values):
        with self._lock:
            self._sequence += 1
            event = {"type": kind, "task_id": self.task_id, "run_id": self.task_id,
                     "sequence": self._sequence, "timestamp": time.time(), **values}
            # Only callers' structured public metadata reaches this stream.
            if self._file:
                self._file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
                self._file.flush()
            self.callback(event)

    def close(self):
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None


def answer_text(value):
    if value in (None, "", {}, []):
        return ""
    if isinstance(value, str):
        return message_text(value, None)
    if isinstance(value, dict):
        # Do not present a whole internal response including observation/thought.
        for key in ("answer", "message", "summary", "result"):
            if value.get(key):
                return answer_text(value[key])
        public = {key: item for key, item in value.items()
                  if key.lower() not in {"thought", "reasoning", "observation", "screenshot"}}
        return message_text(json.dumps(public, ensure_ascii=False), None) if public else ""
    return message_text(json.dumps(value, ensure_ascii=False, default=str), None)


def session_result(session, *, declined=False, action_count=0, errors=(),
                   terminal_answer=None, terminal_status=None):
    """A FINISH with an answer is reported completion, not independent proof."""
    from ufo.module.context import ContextNames

    # ROUND_RESULT may still contain an earlier ASSIGN response. Only the
    # final Host response can supply the final answer; never reuse that cache.
    answer = answer_text(terminal_answer) if terminal_status == "FINISH" else ""
    current_round = session.current_round
    if current_round is None and getattr(session, "_rounds", None):
        current_round = list(session._rounds.values())[-1]
    state = current_round.state.name() if current_round else ""
    diagnostics = {"native_state": state, "actions_completed": action_count,
                   "completion_evidence": "native_report", "independently_verified": False}
    subtasks = session.context.get(ContextNames.PREVIOUS_SUBTASKS) or []
    achieved = [answer_text(item.get("result")) for item in subtasks
                if item.get("status") == "FINISH" and item.get("result")]
    partial = bool(achieved)
    diagnostics["completed_subtask_reports"] = achieved
    if declined:
        return TaskResult("partial" if partial else "incomplete",
                          answer or "已按你的选择跳过未获允许的操作，任务未全部完成。",
                          reason="confirmation_declined", diagnostics=diagnostics)
    if session.is_error() or state in {"ERROR", "FAIL"} or (errors and terminal_status != "FINISH"):
        detail = answer or (errors[-1] if errors else "原生执行遇到错误，未能完成任务。")
        if achieved:
            detail = "已完成部分：\n" + "\n".join(achieved) + "\n\n未完成原因：" + detail
        return TaskResult("partial" if partial else "failed", detail,
                          reason="native_error", diagnostics=diagnostics)
    if state == "FINISH" and terminal_status == "FINISH" and answer:
        artifacts = []
        if isinstance(terminal_answer, dict):
            for item in terminal_answer.get("artifacts", []) or []:
                path = item.get("path") if isinstance(item, dict) else item
                if isinstance(path, str) and not path.startswith(("\\\\", "//")):
                    candidate = Path(path)
                    if candidate.is_absolute() and candidate.is_file():
                        artifacts.append({"path": str(candidate), "source": "native_report", "verified": "exists"})
        return TaskResult("completed", answer, tuple(artifacts), diagnostics=diagnostics)
    detail = answer or "本次执行已结束，但没有取得可确认的完成结果。"
    if achieved:
        detail = "已完成部分：\n" + "\n".join(achieved) + "\n\n" + detail
    return TaskResult("partial" if partial else "incomplete", detail,
                      reason="no_completion_evidence", diagnostics=diagnostics)
