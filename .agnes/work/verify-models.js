// 模型可用性验证脚本 v2
// 所有假通过（200 但内容无效）直接归入"不通过"
const fs = require('fs');
const path = require('path');

const APPDATA = process.env.APPDATA;
const CFG = path.join(APPDATA, 'com.baozi.model-manager', 'providers.json');

function buildUrl(base, p) {
  let b = base.trim().replace(/\/+$/, '');
  if (b.endsWith('#')) b = b.slice(0, -1);
  else if (!b.endsWith('/v1')) b += '/v1';
  return b + p;
}

const PROMPT = '请用一句中文简短回答：你喜欢做什么？';

function extractContent(v) {
  if (!v) return '';
  const arr = Array.isArray(v.choices) ? v.choices
    : (v.data && Array.isArray(v.data.choices) ? v.data.choices : null);
  if (arr && arr.length) {
    const c0 = arr[0];
    if (c0 && c0.message && typeof c0.message.content === 'string') return c0.message.content;
    if (c0 && typeof c0.text === 'string') return c0.text;
    if (c0 && c0.data && typeof c0.data === 'string') return c0.data;
  }
  if (v.output != null) {
    if (typeof v.output === 'string') return v.output;
    if (v.output && v.output.text != null) return String(v.output.text);
  }
  return '';
}

function errMsg(v, text) {
  if (v && v.error) {
    const e = v.error;
    if (typeof e === 'string') return e;
    if (e && e.message) return e.message;
    if (e && e.code != null) return 'code=' + e.code;
  }
  if (v && typeof v.message === 'string') return v.message;
  if (v && v.data && v.data.error && v.data.error.message) return v.data.error.message;
  return text ? text.slice(0, 200) : '';
}

// ---------- 判定核心 ----------
// 统一返回 { usable: boolean, reason: string, category: string }
// category 值: 'quota' | 'empty' | 'http_error' | 'timeout' | 'non_chat' | 'suspicious' | 'ok'
function classify(status, content, httpErr, isTimeout, model) {
  // 1) 超时/异常
  if (isTimeout) return { usable: false, reason: '请求超时', category: 'timeout' };

  // 2) HTTP 非 2xx
  if (status < 200 || status >= 300) {
    const cat = (status === 401 || status === 403) ? 'auth'
      : (status === 429) ? 'rate_limit'
      : (status >= 500) ? 'server_error'
      : 'http_error';
    return { usable: false, reason: `HTTP ${status}${httpErr ? ': ' + httpErr : ''}`, category: cat };
  }

  // 3) HTTP 200 —— 检查内容（假通过直接判不通过）
  const c = (content || '').trim();

  // 空内容 → 假通过 → 不通过
  if (!c) return { usable: false, reason: '内容为空（假通过）', category: 'empty' };

  // 内容里是配额/额度提示 → 不通过
  const low = c.toLowerCase();
  if (low.includes('quota') || low.includes('recharge') || low.includes('topup') ||
      low.includes('free quota') || low.includes('prevention of abuse')) {
    return { usable: false, reason: '免费额度耗尽', category: 'quota' };
  }

  // 内容里是错误/限流信息 → 不通过
  const ERR_KW = ['quota', 'rate limit', 'rate_limit', 'insufficient', 'exceeded',
    'invalid api', 'invalid_api', 'unauthorized', 'forbidden', 'no available',
    'exhausted', 'overloaded', 'no credit', 'access denied',
    '未授权', '无权限', '余额不足', '配额', '限流'];
  const hit = ERR_KW.find(p => low.includes(p));
  if (hit) return { usable: false, reason: `内容含错误信息（"${hit}"）`, category: 'suspicious' };

  // 非对话类模型识别：安全/翻译/校准模型返回的是分类 JSON 或原文回显
  // 如果内容以 { 开头且包含 "User Safety" / "safe" 等 → 安全分类模型
  if (/^\{.*"(User Safety|Safety|Category)".*\}/.test(c)) {
    return { usable: false, reason: '安全/审核类模型，非对话模型', category: 'non_chat' };
  }
  // 翻译模型：直接回显原文或说 "Please translate..."
  if (/^(Please|In one sentence|Translate)/.test(c) && c.length < 120 && !c.includes('我')) {
    return { usable: false, reason: '翻译类模型，非对话模型', category: 'non_chat' };
  }

  // 4) 通过
  return { usable: true, reason: '', category: 'ok' };
}

async function chatTest(baseUrl, apiKey, model, timeoutMs) {
  const url = buildUrl(baseUrl, '/chat/completions');
  const body = { model, messages: [{ role: 'user', content: PROMPT }], max_tokens: 256, temperature: 0.3 };
  const start = Date.now();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + apiKey },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    const status = resp.status;
    const text = await resp.text();
    let v = null; try { v = JSON.parse(text); } catch {}
    const content = extractContent(v);
    const isTimeout = false;
    const httpErr = errMsg(v, text);
    const cls = classify(status, content, httpErr, isTimeout, model);
    return {
      usable: cls.usable, reason: cls.reason, category: cls.category,
      status, ms: Date.now() - start,
      snippet: cls.usable ? content.slice(0, 100) : content.slice(0, 100),
    };
  } catch (e) {
    const isTimeout = e.name === 'AbortError';
    const cls = classify(0, '', '', isTimeout, model);
    return {
      usable: cls.usable, reason: isTimeout ? '请求超时' : String(e.message || e), category: cls.category,
      status: 0, ms: Date.now() - start, snippet: '',
    };
  } finally { clearTimeout(timer); }
}

async function fetchModelList(baseUrl, apiKey) {
  const url = buildUrl(baseUrl, '/models');
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 30000);
  try {
    const resp = await fetch(url, { headers: { 'Authorization': 'Bearer ' + apiKey }, signal: ctrl.signal });
    const text = await resp.text();
    if (!resp.ok) return { error: 'HTTP ' + resp.status, ids: [] };
    let v = null; try { v = JSON.parse(text); } catch {}
    let ids = [];
    if (v) {
      if (Array.isArray(v.data)) ids = v.data.map(m => m.id).filter(Boolean);
      if (!ids.length && Array.isArray(v.models)) ids = v.models.map(m => (typeof m === 'string' ? m : (m.id || m.name))).filter(Boolean);
    }
    return { ids: [...new Set(ids)].sort() };
  } catch (e) { return { error: String(e.message || e), ids: [] }; }
  finally { clearTimeout(timer); }
}

async function pool(items, limit, fn, onProgress) {
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (i < items.length) { const idx = i++; await fn(items[idx], idx); onProgress && onProgress(idx + 1, items.length); }
  }));
}

async function main() {
  const providers = JSON.parse(fs.readFileSync(CFG, 'utf8'));
  const results = [];
  const skipped = [];

  // 构建测试计划
  const plan = [];
  for (const p of providers) {
    let models = p.models.slice();
    let fetched = false;
    if (!models.length) {
      const r = await fetchModelList(p.base_url, p.api_key);
      if (r.error) { skipped.push({ provider: p.name, reason: '模型列表获取失败: ' + r.error }); continue; }
      models = r.ids.slice(0, 60);
      fetched = true;
    }
    for (const m of models) plan.push({ provider: p.name, base_url: p.base_url, key: p.api_key, model: m, fetched });
  }

  console.log('待测模型总数: ' + plan.length + ' | 跳过 provider: ' + skipped.length);

  await pool(plan, 10, async (item) => {
    // 统一 90s 超时，避免大模型误判
    const r = await chatTest(item.base_url, item.key, item.model, 90000);
    results.push({
      provider: item.provider, base_url: item.base_url, model: item.model,
      fetched: item.fetched || false,
      ...r,
    });
  }, (done, total) => { if (done % 10 === 0) console.log(`进度 ${done}/${total}`); });

  // ---------- 输出 ----------
  // 按 provider 分组
  const byProvider = {};
  for (const r of results) (byProvider[r.provider] = byProvider[r.provider] || []).push(r);

  // 统计
  const usable = results.filter(r => r.usable).length;
  const broken = results.length - usable;

  // 按原因分类统计
  const catCount = {};
  for (const r of results) if (!r.usable) catCount[r.category] = (catCount[r.category] || 0) + 1;

  let out = [];
  out.push('# 模型可用性验证报告 v2');
  out.push('');
  out.push(`**总模型数: ${results.length} | ✅ 通过: ${usable} | ❌ 不通过: ${broken}**`);
  out.push('');
  out.push('## 不通过原因分布');
  const catNames = { quota: '免费额度耗尽', empty: '内容为空（假通过）', auth: '鉴权失败(401/403)', rate_limit: '限流(429)', server_error: '服务端错误(5xx)', http_error: 'HTTP错误', timeout: '超时', non_chat: '非对话类模型', suspicious: '内容含错误信息' };
  for (const [c, n] of Object.entries(catCount)) out.push(`- ${catNames[c] || c}: ${n} 个`);
  out.push('');

  // 按 provider 输出
  for (const [prov, arr] of Object.entries(byProvider)) {
    const ok = arr.filter(x => x.usable).length;
    out.push(`\n## ${prov}（${ok}/${arr.length} 通过）`);
    out.push('');
    // 通过
    const passArr = arr.filter(x => x.usable);
    if (passArr.length) {
      out.push('### ✅ 通过');
      out.push('| 模型 | 耗时 | 回复摘要 |');
      out.push('|---|---|---|');
      for (const x of passArr) out.push(`| ${x.model} | ${x.ms}ms | ${x.snippet.slice(0, 60)} |`);
      out.push('');
    }
    // 不通过
    const failArr = arr.filter(x => !x.usable);
    if (failArr.length) {
      out.push(`### ❌ 不通过（${failArr.length} 个）`);
      out.push('| 模型 | 原因 | 分类 |');
      out.push('|---|---|---|');
      for (const x of failArr) out.push(`| ${x.model} | ${x.reason} | ${catNames[x.category] || x.category} |`);
      out.push('');
    }
  }

  // 跳过的 provider
  if (skipped.length) {
    out.push('\n## 跳过的 Provider');
    for (const s of skipped) out.push(`- ${s.provider}: ${s.reason}`);
  }

  const report = out.join('\n');
  fs.writeFileSync(path.join(__dirname, 'report.md'), report);
  fs.writeFileSync(path.join(__dirname, 'results.json'), JSON.stringify({ total: results.length, usable, broken, categoryBreakdown: catCount, results }, null, 2));
  console.log('\n报告已写入 report.md');
  console.log(report);
}

main().catch(e => { console.error(e); process.exit(1); });
