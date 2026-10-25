"""Real normal Host, Agent and background bridge against two isolated HTTP providers."""
from __future__ import annotations
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mirally.framework_host import FrameworkHost


def provider(label):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            self.send_response(200);self.send_header("Content-Type", "application/json");self.end_headers()
            self.wfile.write(json.dumps({"data":[{"id":label,"input_modalities":["text"]}]}).encode())
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            calls.append({"auth":self.headers.get("Authorization"),"body":data})
            self.send_response(200);self.send_header("Content-Type","text/event-stream");self.end_headers()
            for delta, finish in [({"role":"assistant","content":label+" route OK"},None),({},"stop")]:
                chunk={"id":"fixture","choices":[{"index":0,"delta":delta,"finish_reason":finish}],"model":data["model"]}
                self.wfile.write(("data: "+json.dumps(chunk)+"\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n");self.wfile.flush()
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    return server, calls, f"http://127.0.0.1:{server.server_port}/v1"


def main():
    first, chat_calls, chat_url=provider("chat-service")
    second, background_calls, background_url=provider("background-service")
    try:
        with tempfile.TemporaryDirectory(prefix="mirally-model-connections-") as directory:
            host=FrameworkHost(ROOT,Path(directory))
            runtime=host.start()
            def call(path, value=None):
                request=urllib.request.Request(runtime["url"]+path,data=None if value is None else json.dumps(value).encode(),headers={"Authorization":"Bearer "+runtime["token"],"Content-Type":"application/json"})
                with urllib.request.urlopen(request,timeout=30) as response:return json.loads(response.read())
            def rpc(method, **params):return call("/api/rpc",{"method":method,"params":params})["result"]
            try:
                rpc("settings.update",patch={"model":{"base_url":chat_url,"model_id":"chat-model","api_key":"chat-fixture-key"}})
                identity=rpc("model.connection.save",name="后台测试",model={"base_url":background_url,"model_id":"background-model","api_key":"background-fixture-key"})["connection_id"]
                rpc("model.routes.set",routes={"background":identity})
                assert "fixture-key" not in json.dumps(rpc("settings.get"))
                session=call("/api/sessions",{"title":"Named route acceptance"})
                call(f"/api/sessions/{session['id']}/messages",{"commandId":"routing-chat","text":"Answer briefly."})
                for _ in range(200):
                    history=call(f"/api/sessions/{session['id']}/history")
                    if any(message["role"]=="assistant" and "chat-service route OK" in message["text"] for message in history["messages"]):break
                    time.sleep(.05)
                else:raise AssertionError("native chat did not finish through the chat connection")
                assert chat_calls[-1]["auth"]=="Bearer chat-fixture-key"
                assert chat_calls[-1]["body"]["model"]=="chat-model"
                answer=call("/internal/completion",{"model":"background-model","prompt":"background memory job","purpose":"mirally-memory"})
                assert answer["text"]=="background-service route OK"
                assert background_calls[-1]["auth"]=="Bearer background-fixture-key"
                assert background_calls[-1]["body"]["model"]=="background-model"
                assert call("/api/models/test",{"connection_id":identity})["model"]=="background-model"
                assert call("/api/models/test",{})["model"]=="chat-model"
                assert call("/api/models/discover",{"connection_id":identity})["models"][0]["id"]=="background-service"
                count=len(background_calls)
                image="data:image/png;base64,"+base64.b64encode((ROOT/"mirally/assets/mirally-128.png").read_bytes()).decode()
                try:
                    call("/internal/completion",{"messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":image}}]}]})
                    raise AssertionError("text-only background route silently accepted an image")
                except urllib.error.HTTPError as error:
                    assert error.code==400
                    assert "unsupported_image_input" in error.read().decode()
                assert len(background_calls)==count
                print(json.dumps({"passed":True,"native_chat":"chat-service","memory_bridge":"background-service","named_probe":True,"legacy_default_probe":True,"image_rejected_before_network":True,"private_keys_not_echoed":True},indent=2))
            finally:
                try:call("/api/shutdown",{})
                except OSError:pass
                host.close()
    finally:
        first.shutdown();first.server_close();second.shutdown();second.server_close()


if __name__=="__main__":main()
