#!/usr/bin/env python3
"""批量验证网关上的全部模型:逐个发起最小对话请求,记录可用性与是否输出深度思考。

通过网关本身测试(同时验证整条链路),不碰各供应商的 key:
    python verify.py                          # 默认网关 http://127.0.0.1:8788
    python verify.py --gateway http://127.0.0.1:9000
    python verify.py --key sk-xxx             # 网关启动时带了 --key,这里必须给同一个
    python verify.py --only qwen,gpt-4        # 只测包含关键字的模型
    python verify.py --workers 12 --timeout 180

结果写入 verify_results.json(网关面板会读取),边跑边写,面板实时刷新。
状态: ok 可用 / auth_error 鉴权失败(401/403) / no_choices 无内容 / quota 疑似限流
      / http_error / timeout / dead 连接失败
只有 ok 算通过;auth_error、no_choices、quota、http_error、timeout、dead 一律算不通过。
thinking: yes 上游返回了 reasoning 字段或 think 标签 / no 未观察到 / 手动覆盖见 gateway_config.json

整体失败(如网关要求密钥但未提供、或网关未启动)时,会把 error 字段写进结果文件,
面板据此提示"上次验证失败",避免上一次的成功结果被误当成当前结论。
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
AUTH_CODES = (401, 403)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http_json(url, payload=None, timeout=60, key=None):
    req = urllib.request.Request(url, method="POST" if payload is not None else "GET")
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    if key:
        # 与真实客户端一致地带上网关密钥,否则测试和实际访问走的不是同一条鉴权路径
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(req, data, timeout=timeout) as r:
            return getattr(r, "status", 200), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def error_text(body_text):
    """从错误响应体里抽出人类可读的一句话。"""
    try:
        obj = json.loads(body_text)
    except json.JSONDecodeError:
        return body_text.strip()[:160] or "(空响应)"
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, str):
            return err[:160]
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or ""
            if msg:
                return str(msg)[:160]
        if obj.get("message"):
            return str(obj["message"])[:160]
    return body_text.strip()[:160]


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


def test_one(gateway, model, timeout, key=None):
    t0 = time.monotonic()
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 64, "temperature": 0}
    try:
        status, raw = http_json(f"{gateway}/v1/chat/completions", payload, timeout=timeout, key=key)
        text = raw.decode("utf-8", "replace")
        latency = round(time.monotonic() - t0, 2)
        if status in AUTH_CODES:
            # 401/403 = 密钥无效或无权限,属于不通过,且要和限流/无内容区分开
            hint = "网关密钥无效" if status == 401 and "网关密钥" in text else "鉴权失败(密钥无效或无权限)"
            return {"status": "auth_error", "latency": latency, "thinking": "unknown",
                    "detail": f"HTTP {status}: {hint} - {error_text(text)}",
                    "checked_at": now_iso()}
        if status == 404 and "model_not_found" in text:
            return {"status": "http_error", "latency": latency, "thinking": "unknown",
                    "detail": text[:160], "checked_at": now_iso()}
        if status >= 400:
            st, detail, th = classify(text)
            if st == "ok":  # 4xx/5xx 却给出正常内容,按 HTTP 错误处理
                st = "http_error"
            return {"status": st, "latency": latency, "thinking": th,
                    "detail": detail or f"HTTP {status}", "checked_at": now_iso()}
        st, detail, th = classify(text)
        return {"status": st, "latency": latency, "thinking": th, "detail": detail,
                "checked_at": now_iso()}
    except urllib.error.HTTPError as e:
        return {"status": "http_error", "latency": None, "thinking": "unknown",
                "detail": f"HTTP {e.code}", "checked_at": now_iso()}
    except Exception as e:
        name = e.__class__.__name__
        s = str(e).lower()
        if "timeout" in s or "timed out" in s or name == "TimeoutError":
            return {"status": "timeout", "latency": None, "thinking": "unknown",
                    "detail": s[:160], "checked_at": now_iso()}
        return {"status": "dead", "latency": None, "thinking": "unknown",
                "detail": f"{name}: {e}", "checked_at": now_iso()}


def main():
    ap = argparse.ArgumentParser(description="批量验证网关上的全部模型")
    ap.add_argument("--gateway", default="http://127.0.0.1:8788", help="网关地址(不带尾斜杠)")
    ap.add_argument("--key", default=None,
                    help="网关访问密钥(网关启动时带了 --key 就必须填同一个,否则全部 401)")
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

    old = {}
    if args.out.exists():
        try:
            old = json.loads(args.out.read_text(encoding="utf-8")).get("models", {})
        except Exception:
            old = {}
    results = dict(old)

    def save(error=None):
        out = {"updated_at": now_iso(), "gateway": args.gateway,
               "key_used": bool(args.key), "error": error, "models": results}
        try:
            args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            print(f"[verify] 写入结果失败: {e}", flush=True)

    code, raw = http_json(f"{args.gateway}/v1/models", key=args.key)
    if code in AUTH_CODES:
        # 关键:测试和实际访问必须是同一条鉴权路径。这里过不去,本次验证整体作废,
        # 把失败写进结果文件,不能被上一次的成功结果冒名顶替。
        detail = error_text(raw.decode("utf-8", "replace"))
        err = (f"网关拒绝访问(HTTP {code}): {detail}。"
               f"{'本次未带 --key,请用启动网关时的同一个密钥运行 verify.py' if not args.key else '请核对 --key 与启动网关时的密钥是否一致'}")
        save(err)
        sys.exit(f"[verify] 验证失败: {err}")
    if code != 200:
        err = f"读取 {args.gateway}/v1/models 失败: HTTP {code} {error_text(raw.decode('utf-8', 'replace'))}"
        save(err)
        sys.exit(f"[verify] 验证失败: {err}")

    try:
        models = [m["id"] for m in json.loads(raw)["data"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        err = f"{args.gateway}/v1/models 返回的不是预期格式"
        save(err)
        sys.exit(f"[verify] 验证失败: {err}")
    if args.only:
        keys = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        models = [m for m in models if any(k in m.lower() for k in keys)]
    if not models:
        err = "网关没有可验证的模型(providers.json 里没有已保存/启用的模型?)"
        save(err)
        sys.exit(f"[verify] 验证失败: {err}")
    print(f"[verify] {len(models)} 个模型, 并发 {args.workers}, 超时 {args.timeout:.0f}s", flush=True)

    # 开头先清掉上一次的 error,面板不会再显示过期告警
    save(None)
    pool = cf.ThreadPoolExecutor(max_workers=args.workers)
    done = 0

    with pool as ex:
        futs = {ex.submit(test_one, args.gateway, m, args.timeout, args.key): m for m in models}
        for fut in cf.as_completed(futs):
            m = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"status": "dead", "latency": None, "thinking": "unknown",
                     "detail": repr(e)[:160], "checked_at": now_iso()}
            results[m] = r
            done += 1
            tag = r["status"] + (f" {r['latency']}s" if r.get("latency") else "")
            extra = f" [{r['detail']}]" if r.get("detail") and r["status"] != "ok" else ""
            print(f"[verify {done}/{len(models)}] {m}: {tag}{extra}", flush=True)
            save(None)

    tested = [results[m] for m in models]
    ok = sum(1 for r in tested if r.get("status") == "ok")
    bad = [m for m in models if results[m].get("status") != "ok"]
    th = sum(1 for r in tested if r.get("thinking") == "yes")
    auth = [m for m in models if results[m].get("status") == "auth_error"]
    print(f"[verify] 完成: 通过 {ok}/{len(models)}, 不通过 {len(bad)}, 观察到思考输出 {th} -> {args.out}",
          flush=True)
    if auth:
        print(f"[verify] 其中鉴权失败(401/403) {len(auth)} 个: {', '.join(auth[:10])}"
              f"{' ...' if len(auth) > 10 else ''}", flush=True)


if __name__ == "__main__":
    main()
