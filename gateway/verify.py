#!/usr/bin/env python3
"""批量验证网关上的全部模型:逐个发起最小对话请求,记录可用性与是否输出深度思考。

通过网关本身测试(同时验证整条链路),不碰各供应商的 key:
    python verify.py                          # 默认网关 http://127.0.0.1:8788
    python verify.py --gateway http://127.0.0.1:9000
    python verify.py --only qwen,gpt-4        # 只测包含关键字的模型
    python verify.py --workers 12 --timeout 180

结果写入 verify_results.json(网关面板会读取),边跑边写,面板实时刷新。
状态: ok 可用 / no_choices 无内容 / quota 疑似限流 / http_error / timeout / dead 连接失败
thinking: yes 上游返回了 reasoning 字段或 think 标签 / no 未观察到 / 手动覆盖见 gateway_config.json
"""
import argparse
import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

THINK_TAG = re.compile(r"<(/?)think\b", re.I)
QUOTA_KEYS = ("quota", "recharge", "only try", "insufficient", "topup", "额度", "余额", "欠费", "余额不足")


def http_json(url, payload=None, timeout=60):
    req = urllib.request.Request(url, method="POST" if payload is not None else "GET")
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=timeout) as r:
            return getattr(r, "status", 200), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def classify(body_text):
    """判断响应内容。返回 (状态, 错误详情, 是否观察到思考输出)。"""
    try:
        obj = json.loads(body_text)
    except json.JSONDecodeError:
        low = body_text.lower()
        if any(k in low for k in QUOTA_KEYS):
            return "quota", body_text[:160], "unknown"
        return "no_choices", body_text[:160], "unknown"
    if not isinstance(obj, dict):
        return "no_choices", body_text[:160], "unknown"
    if obj.get("error"):
        msg = obj["error"] if isinstance(obj["error"], str) else str(obj["error"].get("message", ""))[:160]
        low = msg.lower()
        status = "quota" if any(k in low for k in QUOTA_KEYS) else "http_error"
        return status, msg, "unknown"
    choices = obj.get("choices")
    if not choices and isinstance(obj.get("data"), dict):  # 兼容 data 包裹
        choices = obj["data"].get("choices")
    if not choices:
        return "no_choices", body_text[:160], "unknown"
    msg0 = choices[0].get("message") or choices[0].get("delta") or {}
    reasoning = ""
    for key in ("reasoning_content", "reasoning", "thinking"):
        v = msg0.get(key)
        if isinstance(v, str) and v.strip():
            reasoning = v
            break
        if isinstance(v, list) and v:
            reasoning = json.dumps(v, ensure_ascii=False)
            break
    content = msg0.get("content") or ""
    think = "yes" if (reasoning or THINK_TAG.search(content)) else "unknown"
    if not content.strip() and not reasoning:
        if obj.get("finish_reason") == "length" or choices[0].get("finish_reason") == "length":
            return "ok", "(只有思考或被截断)", "yes" if reasoning else "unknown"
        return "no_choices", "(响应无内容)", "unknown"
    return "ok", "", think


def test_one(gateway, model, timeout):
    t0 = time.monotonic()
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 64, "temperature": 0}
    try:
        status, raw = http_json(f"{gateway}/v1/chat/completions", payload, timeout=timeout)
        text = raw.decode("utf-8", "replace")
        latency = round(time.monotonic() - t0, 2)
        if status == 404 and "model_not_found" in text:
            return {"status": "http_error", "latency": latency, "thinking": "unknown",
                    "detail": text[:160]}
        if status >= 400:
            st, detail, th = classify(text)
            return {"status": "http_error" if st == "ok" else st, "latency": latency,
                    "thinking": th, "detail": detail or f"HTTP {status}"}
        st, detail, th = classify(text)
        return {"status": st, "latency": latency, "thinking": th, "detail": detail}
    except urllib.error.HTTPError as e:
        return {"status": "http_error", "latency": None, "thinking": "unknown",
                "detail": f"HTTP {e.code}"}
    except Exception as e:
        name = e.__class__.__name__
        s = str(e).lower()
        if "timeout" in s or "timed out" in s or name == "TimeoutError":
            return {"status": "timeout", "latency": None, "thinking": "unknown", "detail": s[:160]}
        return {"status": "dead", "latency": None, "thinking": "unknown", "detail": f"{name}: {e}"}


def main():
    ap = argparse.ArgumentParser(description="批量验证网关上的全部模型")
    ap.add_argument("--gateway", default="http://127.0.0.1:8788", help="网关地址(不带尾斜杠)")
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("verify_results.json"),
                    help="结果输出文件(网关面板读取)")
    ap.add_argument("--workers", type=int, default=8, help="并发数(默认 8,免费额度紧张时调小)")
    ap.add_argument("--timeout", type=float, default=150, help="单请求超时秒数(默认 150)")
    ap.add_argument("--only", default=None, help="逗号分隔关键字,只测包含其一的模型")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    code, raw = http_json(f"{args.gateway}/v1/models")
    if code != 200:
        sys.exit(f"读取 {args.gateway}/v1/models 失败: HTTP {code}")
    models = [m["id"] for m in json.loads(raw)["data"]]
    if args.only:
        keys = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        models = [m for m in models if any(k in m.lower() for k in keys)]
    print(f"[verify] {len(models)} 个模型, 并发 {args.workers}, 超时 {args.timeout:.0f}s", flush=True)

    old = {}
    if args.out.exists():
        try:
            old = json.loads(args.out.read_text(encoding="utf-8")).get("models", {})
        except Exception:
            old = {}
    results = dict(old)
    pool = cf.ThreadPoolExecutor(max_workers=args.workers)
    done = 0

    def save():
        out = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "gateway": args.gateway, "models": results}
        try:
            args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            print(f"[verify] 写入结果失败: {e}", flush=True)

    save()
    with pool as ex:
        futs = {ex.submit(test_one, args.gateway, m, args.timeout): m for m in models}
        for fut in cf.as_completed(futs):
            m = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"status": "dead", "latency": None, "thinking": "unknown", "detail": repr(e)[:160]}
            results[m] = r
            done += 1
            tag = r["status"] + (f" {r['latency']}s" if r.get("latency") else "")
            extra = f" [{r['detail']}]" if r.get("detail") and r["status"] != "ok" else ""
            print(f"[verify {done}/{len(models)}] {m}: {tag}{extra}", flush=True)
            save()

    ok = sum(1 for r in results.values() if r.get("status") == "ok")
    th = sum(1 for r in results.values() if r.get("thinking") == "yes")
    print(f"[verify] 完成: 可用 {ok}/{len(models)}, 观察到思考输出 {th} -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
