#!/usr/bin/env python3
"""本地 AI 网关 —— 把 model-manager 验证过的供应商/模型统一成一个 OpenAI 兼容端点。

启动后在任何支持自定义 OpenAI 兼容地址的工具(Cursor / Cline / Continue / aider ...)里
填 http://127.0.0.1:8788/v1 即可使用全部已保存的模型。

零第三方依赖,Python 3.8+。与 model-manager 共用同一个 providers.json,改配置即时生效。

用法:
    python gateway.py                    # 默认 127.0.0.1:8788
    python gateway.py --port 9000        # 换端口
    python gateway.py --host 0.0.0.0 --key sk-xxx   # 局域网共享 + 要求密钥
"""

import argparse
import http.client
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VERIFY_LOCK = threading.Lock()

DASHBOARD = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>本地 AI 网关</title>
<style>
:root{color-scheme:dark;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
*{box-sizing:border-box} body{margin:0;background:#0d1117;color:#c9d1d9}
header{position:sticky;top:0;background:#161b22;border-bottom:1px solid #30363d;padding:12px 20px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
h1{font-size:18px;margin:0;color:#e6edf3} .accent{color:#58a6ff}
.pill{padding:3px 10px;border-radius:20px;font-size:12px;background:#21262d;border:1px solid #30363d}
.pill.ok{color:#7ee787;border-color:#2ea043} .pill.warn{color:#e3b34d;border-color:#9e6a03} .pill.err{color:#ff7b72;border-color:#da3633}
.pill.dim{color:#7d8597} main{padding:18px 20px;max-width:1100px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:18px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px}
.card b{color:#e6edf3} .card small{font-size:12px;color:#7d8597}
.toolbar{display:flex;gap:10px;margin-bottom:14px;align-items:center;flex-wrap:wrap}
input,select,button{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:7px 10px;border-radius:6px;font-size:13px}
input:focus,select:focus{border-color:#58a6ff;outline:none}
button{cursor:pointer;background:#21262d} button:hover{background:#30363d} button.primary{background:#238636;border-color:#2ea043;color:#fff}
button.primary:hover{background:#2ea043} button:disabled{opacity:.5;cursor:not-allowed}
table{width:100%;border-collapse:collapse;background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid #21262d;font-size:13px}
th{color:#7d8597;font-weight:600;background:#161b22;position:sticky;top:0}
td.m{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
tr:hover td{background:#1c2128} .tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;margin-right:5px}
pre.log{background:#0d1117;border:1px solid #30363d;padding:10px;border-radius:6px;max-height:160px;overflow:auto;font-size:11px;color:#7d8597}
a{color:#58a6ff}
</style></head>
<body>
<header>
  <h1>本地 <span class="accent">AI 网关</span></h1>
  <span id="base" class="pill dim"></span>
  <span id="keyflag" class="pill dim"></span>
  <span id="provs" class="pill dim"></span>
  <span id="verify" class="pill dim"></span>
</header>
<main>
  <div class="cards" id="cards"></div>
  <div class="toolbar">
    <input id="q" placeholder="搜索模型名 / 供应商..." style="flex:1;min-width:220px">
    <select id="f"><option value="all">全部</option><option value="ok">可用</option>
      <option value="think">有思考</option><option value="bad">不可用</option><option value="unverified">未验证</option></select>
    <select id="o"></select>
    <button class="primary" id="vbtn" onclick="toggleVerify()">▶ 验证全部模型</button>
    <button onclick="runTests()">用 mock 自测</button>
    <button onclick="copyBaseURL()">复制 Base URL</button>
  </div>
  <table>
    <thead><tr><th>模型</th><th>供应商</th><th>状态</th><th>耗时</th><th>深度思考</th><th>默认参数</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <h3 style="margin-top:22px">请求/错误统计</h3>
  <pre class="log" id="stats"></pre>
  <h3>网关日志</h3>
  <pre class="log" id="log">点击「验证全部模型」启动批量验证后这里会显示进度...</pre>
</main>
<script>
const $=s=>document.querySelector(s);
let data=null,lastRow=0;
function esc(s){return String(s??"").replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function paint(){
  if(!data)return;
  const g=data.gateway;
  $('#base').textContent=g.base_url;
  $('#base').onclick=()=>navigator.clipboard.writeText(g.base_url);
  $('#keyflag').textContent=g.key_required?'已配密钥':'无密钥(局域网务必配)';
  $('#provs').textContent=g.providers_file.split('\\\\').pop();
  $('#verify').textContent='更新: '+(data.verify_updated_at||'—');
  const st=data.stats||{};
  $('#cards').innerHTML=[['可用',st.requests??0,'ok'],['错误',st.errors??0,'warn'],
    ['模型',(data.models||[]).length,'dim'],['供应商',(data.providers||[]).length,'dim']]
    .map(([k,v,c])=>`<div class="card"><div class="small">${k}</div><b class="pill ${c}">${v}</b></div>`).join('');
  const q=$('#q').value.toLowerCase(),f=$('#f').value;
  const owners=[...new Set(data.models.map(m=>m.owner))].sort();
  $('#o').innerHTML='<option value="">全部供应商</option>'+owners.map(o=>`<option>${o}</option>`).join('');
  const owner=$('#o').value;
  let rows=data.models.filter(m=>{
    if(owner&&m.owner!==owner)return false;
    if(q&&!m.id.toLowerCase().includes(q)&&!m.owner.toLowerCase().includes(q))return false;
    if(f==='ok'&&(m.status!=='ok'))return false;
    if(f==='bad'&&m.status==='ok')return false;
    if(f==='unverified'&&m.status!=='unverified')return false;
    if(f==='think'){const t=m.thinking;return!(t==='yes'||t.startsWith('yes('));}
    return true;
  });
  $('#rows').innerHTML=rows.map(m=>{
    const sc=m.status==='ok'?'ok':(m.status==='unverified'?'dim':'err');
    const th=m.thinking.startsWith('yes')?'warn':(m.thinking==='no'||m.thinking.startsWith('no(')?'dim':'dim');
    const df=Object.keys(m.defaults||{}).length?esc(JSON.stringify(m.defaults)):'—';
    return `<tr><td class="m">${esc(m.id)}</td><td class="small">${esc(m.owner)}</td>
      <td><span class="pill ${sc}">${esc(m.status)}</span>${m.detail?' '+esc(m.detail):''}</td>
      <td>${m.latency!=null?m.latency+'s':'—'}</td>
      <td><span class="pill ${th}">${esc(m.thinking)}</span></td>
      <td class="small">${df}</td></tr>`;
  }).join('');
  $('#stats').textContent=JSON.stringify(st,null,2);
}
async function load(){
  try{
    data=await(await fetch('/api/status'+(data&&data.gateway.key_required?'?key='+new URLSearchParams(location.search).get('key'):'') ,{cache:'no-store'})).json();
    paint();
  }catch(e){$('#rows').innerHTML='<tr><td colspan=6>加载失败: '+e.message+'</td></tr>';}
  setTimeout(load,5000);
}
async function toggleVerify(){
  const b=$('#vbtn');b.disabled=true;
  try{const r=await fetch('/api/verify',{method:'POST'});const j=await r.json();
    $('#log').textContent=j.message||JSON.stringify(j);if(j.ok)setInterval(async()=>{const s=await(await fetch('/api/verify')).json();
    $('#log').textContent='验证'+(s.running?'运行中':'完成')+'  更新: '+(s.updated_at||'');
    if(!s.running){b.disabled=false;}},1500);else b.disabled=false;}
  catch(e){$('#log').textContent='启动失败: '+e.message;b.disabled=false;}
}
function copyBaseURL(){navigator.clipboard.writeText(data.gateway.base_url);}
function runTests(){$('#log').textContent='终端运行:  python mock_upstream.py 18801 18802  &  python gateway.py --providers test_providers.json --port 8790';}
$('#q').oninput=$('#f').onchange=$('#o').onchange=paint;
load();
</script>
</body>
</html>"""

# ---------- 路径 ----------

def default_providers_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "com.baozi.model-manager" / "providers.json"
    return Path.home() / ".config" / "com.baozi.model-manager" / "providers.json"

# ---------- 地址解析(与 model-manager 的 build_url 规则一致) ----------

def parse_base(base: str):
    """返回 (host, port, https, path前缀)。规则同 Rust 端 build_url:
    去掉末尾 /,以 # 结尾则原样使用,否则自动补 /v1。"""
    b = base.strip().rstrip("/")
    if not b:
        raise ValueError("base_url 为空")
    if b.endswith("#"):
        b = b[:-1]
    elif not b.endswith("/v1"):
        b += "/v1"
    if b.startswith("https://"):
        https, rest = True, b[len("https://"):]
    elif b.startswith("http://"):
        https, rest = False, b[len("http://"):]
    else:
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    hostpart, slash, prefix = rest.partition("/")
    host, _, portstr = hostpart.partition(":")
    if not host:
        raise ValueError("base_url 缺少主机名")
    try:
        port = int(portstr) if portstr else (443 if https else 80)
    except ValueError:
        raise ValueError("base_url 端口非法")
    return host, port, https, (prefix if not slash else "/" + prefix.rstrip("/"))

# ---------- 供应商配置热加载 ----------

class Store:
    """读取并缓存 providers.json,文件变化自动重载。
    routes: model_id -> [(provider, canonical_model)],同名模型可对应多个供应商(轮询+容灾)。"""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.providers = []
        self.routes = {}
        self.routes_ci = {}
        self.provider_names = {}     # 小写 provider name -> 真实 name
        self.rr = {}                 # model_id -> 轮询游标
        self._stamp = None
        self.stats = {"requests": 0, "errors": 0, "by_provider": {}}

    def _read_stamp(self):
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def reload_if_changed(self):
        stamp = self._read_stamp()
        if stamp is None:
            return
        with self.lock:
            same = stamp == self._stamp
        if not same:
            self.reload()

    def reload(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[gateway] 读取 {self.path} 失败: {e}", flush=True)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[gateway] {self.path} 不是合法 JSON: {e}", flush=True)
            return
        if not isinstance(data, list):
            print(f"[gateway] {self.path} 内容不是数组,忽略", flush=True)
            return
        providers, routes, routes_ci, names = [], {}, {}, {}
        for p in data:
            if not p.get("enabled", True) or not p.get("models"):
                continue
            try:
                host, port, https, prefix = parse_base(p.get("base_url", ""))
            except ValueError as e:
                print(f"[gateway] 跳过供应商 {p.get('name')}: {e}", flush=True)
                continue
            item = {
                "name": str(p.get("name") or f"p{p.get('id', '?')}"),
                "api_key": str(p.get("api_key", "")),
                "host": host, "port": port, "https": https, "prefix": prefix,
                "_models": [str(m) for m in p["models"]],
            }
            providers.append(item)
            names[item["name"].lower()] = item["name"]
            for m in p["models"]:
                routes.setdefault(m, []).append((item, m))
                routes_ci.setdefault(m.lower(), []).append((item, m))
        with self.lock:
            self.providers, self.routes, self.routes_ci, self.provider_names = providers, routes, routes_ci, names
            self._stamp = self._read_stamp()
        print(f"[gateway] 已加载 {len(providers)} 个供应商, {len(routes)} 个模型", flush=True)

    def candidates(self, model: str):
        """返回候选 [(provider, 真实模型名)](按轮询排序);支持 '供应商名/模型' 或 '供应商名::模型' 钉定。"""
        with self.lock:
            # 整体优先命中(模型名本身含 / 时,如 nvidia/xxx,原样匹配)
            cands = self.routes.get(model) or self.routes_ci.get(model.lower())
            # 未命中再尝试前缀钉定
            if not cands:
                for sep in ("::", "/"):
                    if sep not in model:
                        continue
                    pre, _, rest = model.partition(sep)
                    if pre.lower() not in self.provider_names:
                        continue
                    name = self.provider_names[pre.lower()]
                    for it in self.providers:
                        if it["name"] != name:
                            continue
                        hit = next((m for m in it["_models"] if m == rest or m.lower() == rest.lower()), None)
                        if hit:
                            return [(it, hit)]
                    break
            if not cands:
                return None
            n = len(cands)
            key = model.lower()
            start = self.rr.get(key, 0)
            self.rr[key] = (start + 1) % n
            return [cands[(start + i) % n] for i in range(n)]

# ---------- 网关自身配置(模型默认参数 / 思考能力覆盖) ----------

class RuntimeConfig:
    """gateway_config.json(热加载)。model_defaults 转发前合并进请求体,客户端同名字段优先:
    {
      "model_defaults": {
        "Qwen3.8-Flash-Next":   {"enable_thinking": false},
        "DeepSeek-V4-Pro":      {"thinking": {"type": "disabled"}},
        "some-o3-like":         {"reasoning_effort": "low"}
      },
      "reasoning_override": {"某模型": true}
    }"""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.model_defaults = {}
        self.reasoning_override = {}
        self._stamp = None
        self.loaded = False

    def reload_if_changed(self):
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        with self.lock:
            if stamp == self._stamp:
                return
            self._stamp = stamp
        defaults, override = {}, {}
        if stamp is not None:
            try:
                cfg = json.loads(self.path.read_text(encoding="utf-8"))
                defaults = {str(k): dict(v) for k, v in (cfg.get("model_defaults") or {}).items()
                            if isinstance(v, dict)}
                override = {str(k): bool(v) for k, v in (cfg.get("reasoning_override") or {}).items()}
                self.loaded = True
            except (OSError, json.JSONDecodeError, ValueError) as e:
                print(f"[gateway] 读取 {self.path.name} 失败(忽略): {e}", flush=True)
        with self.lock:
            self.model_defaults, self.reasoning_override = defaults, override

    def defaults_for(self, *names):
        """按请求原名与真实名两个键取默认参数,后者覆盖前者。"""
        out = {}
        with self.lock:
            for n in names:
                d = self.model_defaults.get(n)
                if d:
                    out = {**d, **out}
        return out

    def thinking_override(self, name):
        with self.lock:
            return self.reasoning_override.get(name)

# ---------- 模型验证结果(verify.py 产出,面板读取) ----------

class VerifyResults:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self._stamp = None
        self._data = {}

    def reload_if_changed(self):
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        with self.lock:
            if stamp == self._stamp:
                return
            self._stamp = stamp
        data = {}
        if stamp is not None:
            try:
                parsed = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    data = parsed
            except (OSError, json.JSONDecodeError):
                data = {}
        with self.lock:
            self._data = data

    def models(self):
        with self.lock:
            return dict(self._data.get("models") or {})

    @property
    def updated_at(self):
        with self.lock:
            return self._data.get("updated_at")

# ---------- 响应兼容:个别网关把内容包在 data 字段里 ----------

def unwrap_obj(obj):
    """{"data": {"choices": ...}} -> 内层;否则原样返回。"""
    if isinstance(obj, dict) and "choices" not in obj:
        inner = obj.get("data")
        if isinstance(inner, dict) and "choices" in inner:
            return inner
    return obj

def unwrap_json_bytes(data: bytes) -> bytes:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data
    fixed = unwrap_obj(obj)
    if fixed is obj:
        return data
    return json.dumps(fixed, ensure_ascii=False).encode("utf-8")

def unwrap_sse_line(line: bytes) -> bytes:
    s = line.decode("utf-8", "replace").strip()
    if not s.startswith("data:"):
        return line
    payload = s[5:].strip()
    if not payload.startswith("{"):
        return line
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return line
    fixed = unwrap_obj(obj)
    if fixed is obj:
        return line
    return ("data: " + json.dumps(fixed, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

# ---------- 转发核心 ----------

RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
UPSTREAM_TIMEOUT = 300

def send_upstream(up, path, body: bytes, stream: bool):
    headers = {
        "Host": up["host"],
        "Authorization": f"Bearer {up['api_key']}",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "Accept": "text/event-stream" if stream else "application/json",
    }
    if up["https"]:
        conn = http.client.HTTPSConnection(up["host"], up["port"], timeout=UPSTREAM_TIMEOUT)
    else:
        conn = http.client.HTTPConnection(up["host"], up["port"], timeout=UPSTREAM_TIMEOUT)
    conn.request("POST", up["prefix"] + path, body=body, headers=headers)
    return conn, conn.getresponse()

def openai_error(status, msg, err_type="gateway_error") -> bytes:
    return json.dumps({"error": {"message": msg, "type": err_type, "code": status}},
                      ensure_ascii=False).encode("utf-8")

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    store: Store = None
    runtime: RuntimeConfig = None
    verify: VerifyResults = None
    verify_proc = None          # 验证子进程句柄
    gateway_key = None

    # ---------- 小工具 ----------

    def log_message(self, fmt, *args):
        pass  # 用结构化日志替代默认访问日志

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _reply(self, status, ctype, body: bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.close_connection = True

    def _check_auth(self) -> bool:
        if not self.gateway_key:
            return True
        got = self.headers.get("Authorization", "")
        if got.startswith("Bearer "):
            got = got[7:]
        if not got:
            got = self.headers.get("x-api-key", "")
        if not got and "?" in self.path:  # 浏览器面板用 ?key=xxx 访问
            for kv in self.path.split("?", 1)[1].split("&"):
                if kv.startswith("key="):
                    got = kv[4:]
        if got and got == self.gateway_key:
            return True
        self._reply(401, "application/json; charset=utf-8",
                    openai_error(401, "网关密钥无效,请在 Authorization: Bearer <key> 或 x-api-key 中携带",
                                 "invalid_request_error"))
        return False

    # ---------- 路由 ----------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:
            print(f"[gateway] 未处理异常: {e!r}", flush=True)
            try:
                self._reply(500, "application/json; charset=utf-8",
                            openai_error(500, f"网关内部错误: {e}", "api_error"))
            except Exception:
                self.close_connection = True

    def _do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("", "/ui", "/dashboard"):
            self._reply(200, "text/html; charset=utf-8", DASHBOARD.encode("utf-8"))
        elif path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
        elif path == "/health":
            self.store.reload_if_changed()
            with self.store.lock:
                summary = {"status": "ok", "providers": len(self.store.providers),
                           "models": len(self.store.routes), "stats": self.store.stats}
            self._reply(200, "application/json; charset=utf-8",
                        json.dumps(summary, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/status":
            if not self._check_auth():
                return
            self._reply(200, "application/json; charset=utf-8", self._status_json())
        elif path == "/api/verify":
            if not self._check_auth():
                return
            running = Handler.verify_proc is not None and Handler.verify_proc.poll() is None
            self._reply(200, "application/json; charset=utf-8",
                        json.dumps({"running": running, "updated_at": self.verify.updated_at},
                                   ensure_ascii=False).encode("utf-8"))
        elif path.endswith("/models"):
            if not self._check_auth():
                return
            self.store.reload_if_changed()
            with self.store.lock:
                seen = {}
                for p in self.store.providers:
                    for m in p["_models"]:
                        seen.setdefault(m, p["name"])
            data = [{"id": m, "object": "model", "created": 0, "owned_by": owner}
                    for m, owner in sorted(seen.items(), key=lambda kv: kv[0].lower())]
            self._reply(200, "application/json; charset=utf-8",
                        json.dumps({"object": "list", "data": data}, ensure_ascii=False).encode("utf-8"))
        else:
            self._reply(404, "application/json; charset=utf-8",
                        openai_error(404, f"未知路径 {self.path}", "invalid_request_error"))

    def do_POST(self):
        try:
            self._do_POST()
        except Exception as e:  # 兜底:任何未处理异常都回 500,不能静默断连
            print(f"[gateway] 未处理异常: {e!r}", flush=True)
            try:
                self._reply(500, "application/json; charset=utf-8",
                            openai_error(500, f"网关内部错误: {e}", "api_error"))
            except Exception:
                self.close_connection = True

    def _do_POST(self):
        if not self._check_auth():
            return
        path = self.path.split("?", 1)[0]
        if path in ("/api/verify", "/verify"):
            self._start_verify()
            return
        ep = path.rsplit("/", 1)[-1]
        if ep not in ("chat/completions", "completions", "embeddings"):
            self._reply(404, "application/json; charset=utf-8",
                        openai_error(404, f"网关不支持 {path}(仅支持 /v1/models、/v1/chat/completions、"
                                          f"/v1/completions、/v1/embeddings)", "invalid_request_error"))
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 64 * 1024 * 1024:
            self._reply(400, "application/json; charset=utf-8",
                        openai_error(400, "请求体为空或过大", "invalid_request_error"))
            return
        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")  # 少数中文工具发 GBK,兜底转一次
        except UnicodeDecodeError:
            text = raw.decode("gb18030", "replace")
        try:
            req = json.loads(text)
        except json.JSONDecodeError:
            self._reply(400, "application/json; charset=utf-8",
                        openai_error(400, "请求体不是合法 JSON", "invalid_request_error"))
            return
        if not isinstance(req, dict) or not req.get("model"):
            self._reply(400, "application/json; charset=utf-8",
                        openai_error(400, "缺少 model 字段", "invalid_request_error"))
            return
        self.proxy(f"/{ep}", req, stream=bool(req.get("stream")))

    # ---------- 代理转发 ----------

    def proxy(self, up_path: str, req: dict, stream: bool):
        self.store.reload_if_changed()
        self.runtime.reload_if_changed()
        cands = self.store.candidates(str(req["model"]))
        if not cands:
            self._reply(404, "application/json; charset=utf-8",
                        openai_error(404, f"没有供应商提供模型 {req['model']}"
                                          f"(网关已加载 {len(self.store.routes)} 个模型,可调 /v1/models 查看)",
                                     "model_not_found"))
            return
        n = len(cands)
        last_err = ""
        for i, (up, canonical) in enumerate(cands):
            eff = {**req, "model": canonical} if canonical != req.get("model") else req
            # 网关侧默认参数(如关思考开关),客户端显式传的同名字段优先
            defaults = self.runtime.defaults_for(str(req.get("model", "")), canonical)
            if defaults:
                eff = {**defaults, **eff}
            body = json.dumps(eff, ensure_ascii=False).encode("utf-8")
            t0 = time.monotonic()
            conn = resp = None
            try:
                conn, resp = send_upstream(up, up_path, body, stream)
                retry = i + 1 < n and (stream or resp.status in RETRYABLE)
                if resp.status >= 400 and retry:
                    last_err = f"{up['name']}: HTTP {resp.status} {resp.read(1536).decode('utf-8', 'replace')[:180]}"
                    conn.close()
                    self.store.note(up["name"], False)
                    print(f"[gateway] {req['model']} -> {up['name']} HTTP {resp.status},切换下一家", flush=True)
                    continue
                if not stream:
                    data = resp.read()
                    ok = resp.status < 400
                    self.store.note(up["name"], ok)
                    print(f"[gateway] {req['model']} -> {up['name']} {resp.status} {time.monotonic()-t0:.2f}s", flush=True)
                    self._reply(resp.status,
                                resp.getheader("Content-Type") or "application/json; charset=utf-8",
                                unwrap_json_bytes(data))
                    return
                if resp.status >= 400:
                    # 流式请求但上游返回错误:按普通 JSON 错误回传
                    data = resp.read(16384)
                    self.store.note(up["name"], False)
                    print(f"[gateway] {req['model']} -> {up['name']} stream-err {resp.status}", flush=True)
                    self._reply(resp.status,
                                resp.getheader("Content-Type") or "application/json; charset=utf-8",
                                unwrap_json_bytes(data))
                    return
                self._stream_through(up, req["model"], resp, conn, t0)
                return
            except (OSError, http.client.HTTPException) as e:
                last_err = f"{up['name']}: {e}"
                self.store.note(up["name"], False)
                if i + 1 < n:
                    print(f"[gateway] {req['model']} -> {up['name']} 异常({e.__class__.__name__}),切换下一家", flush=True)
                    continue
                print(f"[gateway] {req['model']} -> {up['name']} 异常: {e}", flush=True)
            finally:
                if conn is not None and resp is not None and getattr(resp, "fp", None) is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
        self._reply(502, "application/json; charset=utf-8",
                    openai_error(502, f"上游供应商均失败: {last_err or '未知错误'}", "api_error"))

    def _stream_through(self, up, model, resp, conn, t0):
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.getheader("Content-Type") or "text/event-stream; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        ok = True
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                if b'"data"' in line and b'"choices"' in line:
                    line = unwrap_sse_line(line)
                self.wfile.write(b"%x\r\n" % len(line) + line + b"\r\n")
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            ok = False
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            pass
        self.close_connection = True
        try:
            conn.close()
        except Exception:
            pass
        self.store.note(up["name"], ok and resp.status < 400)
        print(f"[gateway] {model} -> {up['name']} stream {resp.status} {time.monotonic()-t0:.2f}s"
              f"{'' if ok else ' (客户端或上游中断)'}", flush=True)

    # ---------- 面板数据 / 验证控制 ----------

    def _status_json(self) -> bytes:
        store, rt, vf = self.store, self.runtime, self.verify
        store.reload_if_changed()
        rt.reload_if_changed()
        vf.reload_if_changed()
        with store.lock:
            seen = {}
            for p in store.providers:
                for m in p["_models"]:
                    seen.setdefault(m, p["name"])
            providers = [{"name": p["name"], "models": len(p["_models"])} for p in store.providers]
            stats = json.loads(json.dumps(store.stats))
        host, port = self.server.server_address[0], self.server.server_address[1]
        vf_models = vf.models()
        out = {
            "gateway": {
                "host": host, "port": port,
                "base_url": f"http://127.0.0.1:{port}/v1",
                "key_required": bool(self.gateway_key),
                "providers_file": str(store.path),
                "config_file": str(rt.path), "config_loaded": rt.loaded,
            },
            "providers": providers,
            "models": [],
            "stats": stats,
            "verify_updated_at": vf.updated_at,
            "verify_running": Handler.verify_proc is not None and Handler.verify_proc.poll() is None,
        }
        for m, owner in sorted(seen.items(), key=lambda kv: kv[0].lower()):
            r = vf_models.get(m) or {}
            thinking = r.get("thinking", "unknown")
            ov = rt.thinking_override(m)
            if ov is True:
                thinking = "yes(manual)"
            elif ov is False:
                thinking = "no(manual)"
            out["models"].append({
                "id": m, "owner": owner, "status": r.get("status", "unverified"),
                "latency": r.get("latency"), "thinking": thinking,
                "detail": (r.get("detail") or "")[:200], "checked_at": r.get("checked_at"),
                "defaults": rt.defaults_for(m),
            })
        return json.dumps(out, ensure_ascii=False).encode("utf-8")

    def _start_verify(self):
        script = SCRIPT_DIR / "verify.py"
        with VERIFY_LOCK:
            p = Handler.verify_proc
            if p is not None and p.poll() is None:
                self._reply(409, "application/json; charset=utf-8",
                            json.dumps({"ok": False, "message": "已有验证在运行中"},
                                       ensure_ascii=False).encode("utf-8"))
                return
            if not script.exists():
                self._reply(500, "application/json; charset=utf-8",
                            json.dumps({"ok": False, "message": f"{script} 不存在"},
                                       ensure_ascii=False).encode("utf-8"))
                return
            log = open(SCRIPT_DIR / "verify.log", "ab")
            args = [sys.executable, str(script),
                    "--gateway", f"http://127.0.0.1:{self.server.server_address[1]}"]
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
            try:
                Handler.verify_proc = subprocess.Popen(args, stdout=log,
                                                       stderr=subprocess.STDOUT, **kw)
            except OSError as e:
                self._reply(500, "application/json; charset=utf-8",
                            json.dumps({"ok": False, "message": f"启动验证失败: {e}"},
                                       ensure_ascii=False).encode("utf-8"))
                return
        self._reply(200, "application/json; charset=utf-8",
                    json.dumps({"ok": True, "message": "验证已启动,面板每 5 秒自动刷新"},
                               ensure_ascii=False).encode("utf-8"))

# ---------- 统计 ----------

def note(self, provider_name: str, ok: bool):
    with self.lock:
        self.stats["requests"] += 1
        if not ok:
            self.stats["errors"] += 1
        s = self.stats["by_provider"].setdefault(provider_name, {"requests": 0, "errors": 0})
        s["requests"] += 1
        if not ok:
            s["errors"] += 1

Store.note = note

# ---------- 入口 ----------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="本地 AI 网关:把 model-manager 的模型统一成 OpenAI 兼容端点")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址(默认仅本机;0.0.0.0 开放局域网)")
    ap.add_argument("--port", type=int, default=8788, help="监听端口(默认 8788)")
    ap.add_argument("--providers", type=Path, default=default_providers_path(),
                    help="providers.json 路径(默认取 model-manager 的数据目录)")
    ap.add_argument("--key", default=None, help="可选:网关访问密钥(设置后客户端须带 Bearer 密钥)")
    args = ap.parse_args()

    store = Store(args.providers)
    if not args.providers.exists():
        print(f"[gateway] 警告: {args.providers} 不存在,请先在 model-manager 里保存供应商", flush=True)
    store.reload()

    runtime = RuntimeConfig(SCRIPT_DIR / "gateway_config.json")
    runtime.reload_if_changed()
    verify = VerifyResults(SCRIPT_DIR / "verify_results.json")
    verify.reload_if_changed()

    Handler.store = store
    Handler.runtime = runtime
    Handler.verify = verify
    Handler.gateway_key = args.key
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    tip = "http://127.0.0.1" if args.host in ("0.0.0.0", "::") else f"http://{args.host}"
    print(f"[gateway] OpenAI 兼容地址: {tip}:{args.port}/v1  (模型列表 {tip}:{args.port}/v1/models)", flush=True)
    if args.key:
        print("[gateway] 已启用密钥校验", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[gateway] 已停止", flush=True)
        srv.server_close()

if __name__ == "__main__":
    main()
