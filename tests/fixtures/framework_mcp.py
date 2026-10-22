"""Offline MCP stdio fixture for real SDK interoperability acceptance."""
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method, params = request["method"], request.get("params", {})
    results = {
        "initialize": {"protocolVersion": params.get("protocolVersion", "2025-03-26"), "serverInfo": {"name": "Mirally offline fixture", "version": "1.0.0"}, "capabilities": {"tools": {}, "resources": {}, "prompts": {}}},
        "ping": {},
        "tools/list": {"tools": [{"name": "echo", "description": "Return explicit fixture text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, "annotations": {"readOnlyHint": True}}]},
        "tools/call": {"content": [{"type": "text", "text": "MCP fixture echoed: " + str(params.get("arguments", {}).get("text", ""))}]},
        "resources/list": {"resources": [{"uri": "fixture://record", "name": "Acceptance record", "mimeType": "text/plain"}]},
        "resources/templates/list": {"resourceTemplates": [{"uriTemplate": "fixture://records/{id}", "name": "Record template"}]},
        "resources/read": {"contents": [{"uri": params.get("uri", "fixture://record"), "mimeType": "text/plain", "text": "Real MCP resource content"}]},
        "prompts/list": {"prompts": [{"name": "greeting", "description": "An explicit user-selected template", "arguments": [{"name": "name", "required": True}]}]},
        "prompts/get": {"description": "Fixture prompt", "messages": [{"role": "user", "content": {"type": "text", "text": "Hello " + str(params.get("arguments", {}).get("name", "reader"))}}]},
    }
    if method in results:
        response = {"jsonrpc": "2.0", "id": request["id"], "result": results[method]}
    else:
        response = {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unknown fixture method"}}
    print(json.dumps(response), flush=True)
