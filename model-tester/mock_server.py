import json, time
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "mock-a"}, {"id": "mock-b"}]})
        else:
            self._send({})

    def do_POST(self):
        time.sleep(6)  # 模拟慢速推理,便于观察测试中的状态
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        # mock-b 故意返回失败,便于验证筛选项
        if "b" in req.get("model", "")[-1:]:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"error": {"message": "mock-b 故意失败"}}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # 故意把 choices 包在 data 里,验证兼容逻辑
        self._send({"data": {"choices": [{"message": {"content": "ok"}}], "finish_reason": "stop"}})

HTTPServer(("127.0.0.1", 8799), H).serve_forever()
