"""Test-only helper: a tiny OpenAI-compatible /chat/completions server that
simulates a God orchestrator handing off to a SecurityAuditor specialist, then
calling system_info, then answering. Used by tests and manual smoke runs.

Usage:
  python3 tests/_fake_openai_server.py   # listens on 127.0.0.1:8951
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8951


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter
        pass

    def do_GET(self):
        if "/models" in self.path:
            data = json.dumps({"object": "list", "data": [{"id": "kira-3.5-flash"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        msgs = body.get("messages", [])
        tc = sum(1 for m in msgs if m.get("role") == "assistant" and m.get("tool_calls"))
        model = body.get("model", "m")

        if tc == 0:
            # God (the body) hands off to a body part via the SDK's auto-generated
            # (string-function-styled, lower-cased) handoff function name.
            tcs = [{"id": "call_1", "type": "function",
                    "function": {"name": "transfer_to_torso", "arguments": "{}"}}]
            content, fr = None, "tool_calls"
        elif tc == 1:
            tcs = [{"id": "call_2", "type": "function",
                    "function": {"name": "system_info", "arguments": "{}"}}]
            content, fr = None, "tool_calls"
        else:
            content = "Security audit complete: no exposed ports, users locked down."
            tcs, fr = None, "stop"

        resp = {
            "id": "1", "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant",
                                                 "content": content, "tool_calls": tcs},
                         "finish_reason": fr}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        if body.get("stream"):
            # OpenAI-compatible SSE streaming (used by streaming engines like
            # Hermes Agent). Emit the usage-less empty delta chunk, the content
            # delta, then the termination chunk + [DONE].
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            chunk = {
                "id": "1", "object": "chat.completion.chunk", "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            if content:
                delta = {
                    "id": "1", "object": "chat.completion.chunk", "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                }
                self.wfile.write(b"data: " + json.dumps(delta).encode() + b"\n\n")
            if tcs:
                tdelta = {
                    "id": "1", "object": "chat.completion.chunk", "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"tool_calls": tcs}, "finish_reason": None}],
                }
                self.wfile.write(b"data: " + json.dumps(tdelta).encode() + b"\n\n")
            done = {
                "id": "1", "object": "chat.completion.chunk", "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": fr}],
            }
            self.wfile.write(b"data: " + json.dumps(done).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"fake openai server up on 127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
