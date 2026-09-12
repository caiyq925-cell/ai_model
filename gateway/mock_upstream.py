#!/usr/bin/env python3
"""网关测试用 mock 上游:两个端口,一个返回包裹格式+一次失败后恢复(验容灾),一个返回标准格式。

python mock_upstream.py 18801   # 第1次请求 500,之后返回 {"data":{"choices":...}} 包裹格式
python mock_upstream.py 18802   # 标准 OpenAI 响应
"""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

fail_once = {"flag": True}
lock = threading.Lock()

class Base(BaseHTTPRequestHandler):
    port = 0
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        body = None
        if self.port == 18801:  # 故意用 self.port 区分(见下方 per-port 子类)
            with lock:
                if fail_once["flag"]:
                    fail_once["flag"] = False
                    body = json.dumps({"error": {"message": "mock 第1次故意 500"}}).encode()
                    self.send_response(500)
                else:
                    # 故意包裹在 data 里,验证网关解包
                    body = json.dumps({"data": {"id": "c1", "choices": [
                        {"message": {"role": "assistant", "content": "wrapped-ok"}}],
                        "finish_reason": "stop"}}, ensure_ascii=False).encode()
                    self.send_response(200)
        else:
            if req.get("stream"):
                # SSE 流,且最后一个 chunk 故意包裹,验证流式解包
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                c1 = {"id": "s1", "choices": [{"delta": {"content": "he"}, "index": 0}]}
                c2_wrapped = {"data": {"id": "s1", "choices": [{"delta": {"content": "llo"},
                              "finish_reason": "stop", "index": 0}]}}
                for obj in (c1, c2_wrapped):
                    self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n")
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            body = json.dumps({"id": "c2", "choices": [
                {"message": {"role": "assistant", "content": "standard-ok"}}],
                "finish_reason": "stop"}, ensure_ascii=False).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def serve(port):
    class H(Base):
        pass
    H.port = port  # 每个端口一个独立子类,端口号互不干扰
    HTTPServer(("127.0.0.1", port), H).serve_forever()

if __name__ == "__main__":
    ports = [int(x) for x in sys.argv[1:]] or [18801, 18802]
    for p in ports[1:]:
        threading.Thread(target=serve, args=(p,), daemon=True).start()
    serve(ports[0])
