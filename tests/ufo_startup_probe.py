"""Real import chain in a hidden process; no model call, Session or desktop."""
from contextlib import ExitStack
from pathlib import Path
import asyncio
import json
import os
import sys
import tempfile
import threading

from openassist.runtime.ufo.diagnostics import startup_diagnostics
from openassist.runtime.ufo.worker import prepare_worker_config
from openassist.runtime.ufo.config import configure_ufo_config_root

root = Path(sys.argv[1])
sink = sys.stdout
sys.stdout = sys.stderr
def emit(event):
    print(json.dumps(event), file=sink, flush=True)

channel = None
if "--worker-shape" in sys.argv[2:]:
    # Match the production cwd and its concurrent, blocking stdio reader.
    from openassist.runtime.ufo.worker import stdio_channel
    channel = stdio_channel(sys.stdin, sink)
    threading.Thread(target=channel.listen, daemon=True).start()
    os.chdir(root / "UFO")


async def native_imports(mark, scopes):
    mark("session_import")
    from ufo.module.sessions.session import Session
    assert Session is not None
    mark("native_compatibility")
    from openassist.runtime.ufo.compat import native_compatibility
    scopes.enter_context(native_compatibility())
    mark("initialized")

with tempfile.TemporaryDirectory() as directory, ExitStack() as scopes:
    with startup_diagnostics(emit, interval=2) as mark:
        mark("configuring")
        configure_ufo_config_root(prepare_worker_config(root, Path(directory)), ufo_root=root / "UFO")
        mark("model_binding")
        from openassist.runtime.ufo.model_bridge import selected_model
        scopes.enter_context(selected_model({"api":"openai-completions", "model":"synthetic",
            "base_url":"http://127.0.0.1:1/v1", "key":"synthetic-test-only"}))
        asyncio.run(native_imports(mark, scopes))
        if channel is not None:
            # The parent keeps stdin open throughout the imports, then sends
            # cancellation to verify that the same pipe reader remains live.
            assert channel.ready.wait(10), "control channel stopped responding"
            assert channel.stopped, "cancellation was not received"
            emit({"type": "cancel_received"})
