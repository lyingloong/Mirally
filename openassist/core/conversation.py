"""Local, inspectable conversation records; never store screenshots or approvals."""
from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from openassist.core.text import public_text, message_text


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    """Bounded task summaries, separate from an Agent's live desktop state.

    A directory of sessions plus an active pointer allows explicit new/resume/
    clear actions. Passing no directory gives demo/tests an in-memory store.
    """

    def __init__(self, directory: Path | None = None):
        self.directory = directory
        self.error = ""
        self._lock = threading.RLock()
        self.data = self._empty()
        if directory:
            try:
                active = json.loads((directory / "active.json").read_text(encoding="utf-8"))
                self.select(active["id"])
            except FileNotFoundError:
                pass
            except (OSError, ValueError, KeyError, TypeError):
                self.error = "上次会话未能读取，已建立空白会话；原文件保留。"

    @staticmethod
    def _empty():
        return {"version": 1, "id": "conversation-" + uuid.uuid4().hex,
                "title": "新的会话", "updated": timestamp(), "older_summary": "", "tasks": []}

    def _path(self, session_id):
        if not re.fullmatch(r"conversation-[a-f0-9]{32}", str(session_id)):
            raise ValueError("Invalid conversation ID")
        return self.directory / (session_id + ".json")

    @staticmethod
    def _write(path, data):
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _save(self):
        if self.directory is None:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._write(self._path(self.data["id"]), self.data)
            self._write(self.directory / "active.json", {"id": self.data["id"]})
            self.error = ""
        except OSError:
            self.error = "会话暂时无法保存；本次窗口内仍可接续，重启后可能丢失。"

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.data)

    def new(self):
        with self._lock:
            self._save()
            self.data = self._empty()
            self._save()

    def clear(self):
        """Forget current session content, preserving other saved sessions."""
        with self._lock:
            session_id = self.data["id"]
            self.data = self._empty()
            self.data["id"] = session_id
            self._save()

    def select(self, session_id):
        with self._lock:
            if self.directory is None:
                if session_id != self.data["id"]:
                    raise ValueError("Unknown demo conversation")
                return
            data = json.loads(self._path(session_id).read_text(encoding="utf-8"))
            if data.get("id") != session_id or data.get("version") != 1 or not isinstance(data.get("tasks"), list):
                raise ValueError("Invalid conversation data")
            for task in data["tasks"]:
                if not isinstance(task, dict) or not all(isinstance(task.get(k), str) for k in ("id", "request", "status", "result")):
                    raise ValueError("Invalid task record")
                if not isinstance(task.get("inputs"), list) or not all(isinstance(t, str) for t in task["inputs"]):
                    raise ValueError("Invalid input records")
                if task["status"] == "running":
                    task["status"] = "interrupted"
                    task["result"] = "上次运行中断，完成情况需要重新检查。"
            self.data = data
            self._save()

    def sessions(self):
        if self.directory is None or not self.directory.exists():
            return [(self.data["id"], self.data["title"])]
        sessions = []
        for path in self.directory.glob("conversation-*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if path == self._path(value["id"]):
                    sessions.append((value.get("updated", ""), value["id"], public_text(value.get("title"), 40)))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return [(identifier, title) for _, identifier, title in sorted(sessions, reverse=True)[:30]]

    def begin(self, task_id, request, *, kind="desktop"):
        with self._lock:
            if not self.data["tasks"]:
                self.data["title"] = public_text(request, 40)
            self.data["tasks"].append({"id": task_id, "request": message_text(request, 4000), "kind": kind,
                                       "status": "running", "inputs": [], "result": ""})
            if len(self.data["tasks"]) > 40:
                old = self.data["tasks"].pop(0)
                line = public_text(f'{old["request"]} → {old["status"]}: {old["result"]}', 500)
                self.data["older_summary"] = (self.data.get("older_summary", "") + "\n" + line)[-5000:]
            self.data["updated"] = timestamp()
            self._save()

    def set_kind(self, task_id, kind):
        with self._lock:
            task = next((t for t in self.data["tasks"] if t["id"] == task_id), None)
            if task:
                task["kind"] = kind
                self._save()

    def note(self, task_id, text):
        with self._lock:
            task = next((t for t in self.data["tasks"] if t["id"] == task_id), None)
            if task:
                task["inputs"] = (task["inputs"] + [message_text(text, 4000)])[-20:]
                self._save()

    def finish(self, task_id, status, result, *, execution_result=None):
        with self._lock:
            task = next((t for t in self.data["tasks"] if t["id"] == task_id), None)
            if task:
                task.update(status=status, result=message_text(result, None))
                if execution_result is not None:
                    # Keep public result references, never prompts, screenshots,
                    # tool arguments or the full diagnostic payload.
                    task["reason"] = public_text(execution_result.reason, 200)
                    task["artifacts"] = [
                        {key: message_text(item[key], 4000)
                         for key in ("path", "source", "verified") if key in item}
                        for item in execution_result.artifacts if isinstance(item, dict) and item.get("path")
                    ]
                    task["event_log"] = message_text(execution_result.diagnostics.get("event_log", ""), 4000)
                self.data["updated"] = timestamp()
                self._save()

    def context(self, *, budget=14000):
        """Newest useful records first for selection, chronological in prompt."""
        with self._lock:
            recent, remaining = [], budget
            older = self.data.get("older_summary", "")[-2500:]
            tasks = [t for t in self.data["tasks"] if t["status"] != "running"]
            for index, task in enumerate(reversed(tasks[-8:])):
                entry = {k: copy.deepcopy(task[k]) for k in ("request", "status", "inputs", "result")}
                entry["kind"] = task.get("kind", "desktop")
                if task.get("artifacts"):
                    entry["artifacts"] = copy.deepcopy(task["artifacts"])
                if task.get("reason"):
                    entry["reason"] = task["reason"]
                # Bound each task so a long exchange cannot crowd out all others.
                entry["inputs"] = [message_text(text, 800) for text in entry["inputs"][-5:]]
                entry["request"] = message_text(entry["request"], 1500)
                # The most recent answer may be the text the user asks us to
                # paste elsewhere. Never substitute a truncated summary for it.
                entry["result"] = message_text(entry["result"], None if index == 0 else 2000)
                size = len(json.dumps(entry, ensure_ascii=False))
                if size > remaining and index > 0:
                    break
                recent.append(entry)
                remaining -= size
            older = older[-max(0, remaining):] if remaining > 0 else ""
            return {"conversation_id": self.data["id"], "older_summary": older,
                    "previous_tasks": list(reversed(recent)), "omitted_tasks": max(0, len(tasks) - len(recent)),
                    "latest_result_over_budget": remaining < 0}
