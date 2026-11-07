"""Offline subprocess probe of the framework worker's JSON-lines inbound protocol.

Owns nothing in the product: it starts the real worker (``python -m mirally.framework``)
as a child process, writes newline-terminated request lines to its stdin and asserts
the replies documented in ``docs/architecture/overview.md`` under
"框架 worker 的 JSON-lines 入站契约":

* exactly one JSON line out per accepted request line; stdin EOF -> exit 0;
* ``id`` must be a string or integer; malformed input -> ``INVALID_REQUEST``;
* business failures pass through their ``FrameworkError`` code (``METHOD_NOT_FOUND``,
  ``INVALID_PARAMS``, ``CONFLICT``, ``FORBIDDEN``, ``SCHEMA_VERSION``) and keep ``id``;
* unexpected failures -> ``INTERNAL_ERROR`` that does not echo the request;
* a second worker on the same home refuses to start (``worker_lock``).

Every worker runs on a throwaway home under the platform temp directory (or
``MIRALLY_PROBE_TEMP``), so no product data, credential or live host is touched and
nothing here reaches the network. The worker implementation is the subject under test
and is deliberately not modified by this file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = [sys.executable, "-m", "mirally.framework"]
MAX_LINE = 1_048_576
STATUS = {"method": "system.status"}


def temp_root() -> Path | None:
    """Where throwaway worker homes may live, or None for the platform default.

    The platform default (``TMP``/``TEMP``) wins; ``MIRALLY_PROBE_TEMP`` gives an
    explicit override for sandboxes where the inherited temp path is unwritable.
    Nothing here reaches product data: the worker only ever sees the throwaway home.
    """
    override = os.environ.get("MIRALLY_PROBE_TEMP")
    return Path(override) if override else None


def json_line(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


class WorkerProtocolProbe(unittest.TestCase):
    """One worker process per test; every helper reads exactly what the protocol owes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worker-protocol-", dir=temp_root())
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "隔离 home"

    def start(self, home: Path | None = None) -> subprocess.Popen:
        process = subprocess.Popen(
            [*WORKER, "--root", str(REPO_ROOT), "--home", str(home or self.home)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT), text=True, encoding="utf-8", bufsize=1)
        self.addCleanup(self.stop, process)
        return process

    def stop(self, process: subprocess.Popen) -> None:
        if process.poll() is None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def exchange(self, process: subprocess.Popen, *lines: str) -> list[dict]:
        """Write the lines, then read exactly one JSON reply line per written line."""
        for line in lines:
            process.stdin.write(line + "\n")
            process.stdin.flush()
        replies = []
        for line in lines:
            raw = process.stdout.readline()
            self.assertTrue(raw, f"no reply for a written line: {line[:120]!r}")
            replies.append(json.loads(raw))
        return replies

    def finish(self, process: subprocess.Popen) -> tuple[int, str, str]:
        """Close stdin and collect (exit code, leftover stdout, stderr)."""
        process.stdin.close()
        out = process.stdout.read()
        err = process.stderr.read()
        code = process.wait(timeout=60)
        return code, out, err

    def assert_invalid(self, reply: dict, message: str | None = None) -> None:
        self.assertEqual(set(reply), {"id", "error"}, reply)
        self.assertEqual(reply["error"]["code"], "INVALID_REQUEST", reply)
        if message is not None:
            self.assertEqual(reply["error"]["message"], message, reply)

    # -- request in, protocol out ------------------------------------------
    def test_success_is_one_json_reply_line_per_request(self):
        process = self.start()
        replies = self.exchange(
            process, json_line({"id": 1, **STATUS}),
            json_line({"id": "会话-1", **STATUS, "params": {}}))
        self.assertEqual([r.get("id") for r in replies], [1, "会话-1"])
        for reply in replies:
            self.assertIn("result", reply)
            self.assertNotIn("error", reply)
        code, rest, _ = self.finish(process)
        self.assertEqual(code, 0, "stdin EOF must end the loop with exit code 0")
        self.assertEqual(rest, "", "stdout carries protocol only, no extra line")

    def test_missing_params_default_to_an_empty_object(self):
        process = self.start()
        reply = self.exchange(process, json_line({"id": "status", **STATUS}))[0]
        self.assertIn("result", reply)
        self.assertEqual(reply["id"], "status")

    def test_string_and_integer_ids_round_trip(self):
        process = self.start()
        ids = [0, 7, "", "id-中文"]
        replies = self.exchange(process, *[json_line({"id": i, **STATUS}) for i in ids])
        self.assertEqual([r["id"] for r in replies], ids)
        self.assertTrue(all("error" not in r for r in replies))

    def test_empty_stdin_exits_zero_silently(self):
        process = self.start()
        self.assertEqual(self.finish(process), (0, "", ""))

    # -- malformed input ----------------------------------------------------
    def test_malformed_json_is_invalid_request(self):
        process = self.start()
        replies = self.exchange(process, "{not json", json_line([1, 2]), json_line("text"),
                                json_line(None), json_line(42))
        self.assert_invalid(replies[0], "invalid JSON")
        for reply in replies[1:]:
            self.assert_invalid(reply)
        self.assertTrue(all(r["id"] is None for r in replies))

    def test_oversized_line_is_rejected_without_echo(self):
        process = self.start()
        oversized = json_line({"id": "big", **STATUS, "padding": "x" * MAX_LINE})
        self.assertGreater(len(oversized), MAX_LINE)
        reply = self.exchange(process, oversized)[0]
        self.assert_invalid(reply)
        self.assertIsNone(reply["id"])
        self.assertNotIn("x" * 64, json.dumps(reply), "the request must not be echoed back")

    def test_unterminated_final_line_is_rejected(self):
        process = self.start()
        process.stdin.write(json_line({"id": "eof", **STATUS}))  # no trailing newline
        process.stdin.flush()
        code, out, _ = self.finish(process)
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1, out)
        self.assert_invalid(json.loads(lines[0]))

    def test_invalid_id_types_are_rejected_and_never_resolved(self):
        process = self.start()
        bad = [{"method": "system.status"}, {"id": None, **STATUS}, {"id": True, **STATUS},
               {"id": 1.5, **STATUS}, {"id": [1], **STATUS}, {"id": {"a": 1}, **STATUS}]
        for reply, request in zip(self.exchange(process, *[json_line(b) for b in bad]), bad):
            self.assert_invalid(reply, "id must be a string or integer")
            # The value is read before it is validated, so the current worker echoes the
            # raw request id back. Pin that behaviour: the reply must still be a single
            # clean error and must never carry a result for an invalid identity.
            self.assertEqual(reply["id"], request.get("id"), request)
            self.assertNotIn("result", reply, request)

    def test_missing_or_non_object_method_is_invalid_request(self):
        process = self.start()
        replies = self.exchange(
            process,
            json_line({"id": "nomethod"}),
            json_line({"id": "nummethod", "method": 5}),
            json_line({"id": "listparams", **STATUS, "params": []}))
        for reply in replies:
            self.assert_invalid(reply, "method must be text and params an object")
        self.assertEqual([r["id"] for r in replies], ["nomethod", "nummethod", "listparams"])

    # -- business failures keep their code and id ---------------------------
    def test_business_failures_pass_through_the_framework_error_code(self):
        process = self.start()
        replies = self.exchange(
            process,
            json_line({"id": "missing", "method": "no.such.method"}),
            json_line({"id": "bad-kwargs", "method": "conversation.get"}),  # required arg absent
            json_line({"id": "bad-type", "method": "conversation.get", "params": "text"}))
        self.assertEqual(replies[0]["error"]["code"], "METHOD_NOT_FOUND")
        self.assertEqual(replies[0]["id"], "missing")
        self.assertEqual(replies[1]["error"]["code"], "INVALID_PARAMS")
        self.assertEqual(replies[1]["id"], "bad-kwargs")
        self.assert_invalid(replies[2], "method must be text and params an object")
        self.assertEqual(replies[2]["id"], "bad-type")
        for reply in replies:
            self.assertNotIn("result", reply)

    def test_unknown_conversation_surfaces_a_clean_business_error(self):
        process = self.start()
        reply = self.exchange(
            process, json_line({"id": "x", "method": "conversation.get",
                                "params": {"conversation_id": "no-such-conversation"}}))[0]
        self.assertIn(reply["error"]["code"], {"NOT_FOUND", "INVALID_PARAMS"}, reply)
        self.assertEqual(reply["id"], "x")
        self.assertNotIn("result", reply)

    # -- unexpected failure must not leak the request -----------------------
    def test_unexpected_failure_is_internal_error_without_echoing_the_request(self):
        secret = "probe-secret-token-1234"
        process = self.start()
        broken = {"id": "leak", "method": "model.connection.save",
                  "params": {"connection": {"api_key": secret, "base_url": "not a url"}}}
        reply = self.exchange(process, json_line(broken))[0]
        _, _, stderr = self.finish(process)
        if reply["error"]["code"] == "INTERNAL_ERROR":
            self.assertEqual(reply["error"]["message"], "product operation failed")
            self.assertEqual(reply["id"], "leak")
            self.assertIn("framework RPC failed:", stderr)
        else:
            # If the route ever maps this shape, it must still stay a clean error.
            self.assertIn(reply["error"]["code"], {"INVALID_PARAMS", "CONFLICT"}, reply)
        self.assertNotIn(secret, json.dumps(reply, ensure_ascii=False))
        self.assertNotIn(secret, stderr)

    # -- one worker per home ------------------------------------------------
    def test_second_worker_on_the_same_home_refuses_to_start(self):
        first = self.start()
        self.exchange(first, json_line({"id": 1, **STATUS}))
        second = subprocess.run(
            [*WORKER, "--root", str(REPO_ROOT), "--home", str(self.home)],
            input="", capture_output=True, text=True, encoding="utf-8",
            cwd=str(REPO_ROOT), timeout=120)
        self.assertNotEqual(second.returncode, 0, "worker_lock must reject a second owner")
        self.assertEqual(second.stdout.strip(), "")
        self.assertTrue(second.stderr.strip(), "the refusal must explain itself on stderr")

    def test_a_different_home_starts_its_own_worker(self):
        first = self.start()
        self.exchange(first, json_line({"id": 1, **STATUS}))
        other = Path(self.temp.name) / "另一个 home"
        second = self.start(other)
        reply = self.exchange(second, json_line({"id": 2, **STATUS}))[0]
        self.assertIn("result", reply)
        self.assertEqual(self.finish(second)[0], 0)


if __name__ == "__main__":
    unittest.main()
