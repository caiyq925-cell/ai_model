// 复刻 Rust test_model 新逻辑，验证假通过是否被正确拦截
const fs = require('fs');
const path = require('path');
const CFG = path.join(process.env.APPDATA, 'com.baozi.model-manager', 'providers.json');
const providers = JSON.parse(fs.readFileSync(CFG, 'utf8'));

function buildUrl(base, p) {
  let b = base.trim().replace(/\/+$/, '');
  if (b.endsWith('#')) b = b.slice(0, -1);
  else if (!b.endsWith('/v1')) b += '/v1';
  return b + p;
}
function extractContent(v) {
  if (!v) return '';
  const arr = Array.isArray(v.choices) ? v.choices : (v.data && Array.isArray(v.data.choices) ? v.data.choices : null);
  if (arr && arr.length) {
    const c0 = arr[0];
    if (c0 && c0.message && typeof c0.message.content === 'string') return c0.message.content;
    if (c0 && typeof c0.text === 'string') return c0.text;
    if (c0 && c0.data && typeof c0.data === 'string') return c0.data;
  }
  return '';
}
// 与 Rust 代码完全一致的关键字表
const KWS = ['quota','recharge','topup','free quota','prevention of abuse','insufficient','rate limit','rate_limit','exceeded','exhausted','no available','overloaded','no credit','access denied','invalid api','未授权','无权限','余额不足','配额','限流'];

// 复刻 Rust test_model 判定（prompt 用原代码的 "你好，请只回复：ok"）
async function rustLogic(base, key, model) {
  const url = buildUrl(base, '/chat/completions');
  const body = { model, messages: [{ role: 'user', content: '你好，请只回复：ok' }] };
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60000);
  try {
    const resp = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key }, body: JSON.stringify(body), signal: ctrl.signal });
    const status = resp.status;
    const text = await resp.text();
    if (status < 200 || status >= 300) return { pass: false, why: 'HTTP ' + status };
    let v = null; try { v = JSON.parse(text); } catch {}
    const choices = Array.isArray(v && v.choices) ? v.choices : (v && v.data && Array.isArray(v.data.choices) ? v.data.choices : null);
    if (!choices || !choices.length) return { pass: false, why: '无 choices' };
    const content = extractContent(v).trim();
    // 检查① 空内容
    if (!content) return { pass: false, why: '假通过拦截: 内容为空' };
    // 检查② 关键字
    const low = content.toLowerCase();
    const hit = KWS.find(k => low.includes(k));
    if (hit) return { pass: false, why: '假通过拦截: 内容含错误关键字「' + hit + '」, 原文=' + content.slice(0, 80) };
    return { pass: true, why: '通过, 内容=' + content.slice(0, 60) };
  } catch (e) {
    return { pass: false, why: e.name === 'AbortError' ? '超时' : String(e.message || e) };
  } finally { clearTimeout(timer); }
}

async function main() {
  const aih = providers.find(p => p.name === 'aihubmix');
  const st2 = providers.find(p => p.name === '商汤2');
  let out = '';
  // aihubmix 抽 3 个 quota 模型
  out += '=== aihubmix (quota 假通过，应全部拦截) ===\n';
  for (const m of ['gpt-4.1-free', 'gpt-5.5-free', 'coding-glm-5.3-free']) {
    const r = await rustLogic(aih.base_url, aih.api_key, m);
    out += (r.pass ? '[通过]' : '[不通过] ') + m + '  → ' + r.why + '\n';
  }
  // 商汤2 glm-5.2 (空内容假通过，应拦截) + deepseek-v4-pro (真可用，应通过)
  out += '\n=== 商汤2 (glm-5.2 应拦截, deepseek-v4-pro 应通过) ===\n';
  for (const m of ['glm-5.2', 'deepseek-v4-pro']) {
    const r = await rustLogic(st2.base_url, st2.api_key, m);
    out += (r.pass ? '[通过]' : '[不通过] ') + m + '  → ' + r.why + '\n';
  }
  fs.writeFileSync('D:/Users/ai_model/.agnes/work/rust-logic-check.txt', out);
  console.log(out);
}
main();
