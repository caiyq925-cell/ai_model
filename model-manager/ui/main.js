const { invoke } = window.__TAURI__.core;

// ---------- 全局状态 ----------

let providers = [];
let showDisabled = false;

// 编辑页状态
let editor = null;
// { editing: number|null, name, base, key, filter, fetching, error,
//   models: [{ id, checked, status: 'idle'|'testing'|'ok'|'fail', ms, err }] }

const $ = (id) => document.getElementById(id);

// ---------- 工具 ----------

function toast(msg, isError = false, duration = 2600) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " error" : "");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add("hidden"), duration);
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // WebView 剪贴板 API 不可用时回退
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

// ---------- 列表页 ----------

async function refreshProviders() {
  providers = await invoke("load_providers");
  renderList();
}

function renderList() {
  const cards = $("cards");
  const list = showDisabled ? providers : providers.filter((p) => p.enabled);
  const enabledCount = providers.filter((p) => p.enabled).length;
  $("stat").textContent = `${enabledCount} 个启用 / 共 ${providers.length} 个`;
  $("empty").classList.toggle("hidden", list.length > 0);
  cards.innerHTML = "";

  for (const p of list) {
    const card = document.createElement("div");
    card.className = "card" + (p.enabled ? "" : " disabled");

    // 按供应商名生成专属颜色
    const hue = [...p.name].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7) % 360;
    const nameColor = `hsl(${hue}, 75%, 74%)`;

    const chips = p.models.slice(0, 6).map(
      (m) => `<span class="chip" title="${escapeHtml(m)}">${escapeHtml(truncate(m, 24))}</span>`
    ).join("") +
      (p.models.length > 6
        ? `<span class="chip more">+${p.models.length - 6}</span>`
        : "") ||
      `<span class="chip more">尚未选择模型，点击「编辑」</span>`;

    card.innerHTML = `
      <div class="card-head">
        <div class="card-head-left">
          <span class="card-dot" style="background:${nameColor}"></span>
          <span class="card-name" title="${escapeHtml(p.name)}" style="color:${nameColor}">${escapeHtml(p.name)}</span>
        </div>
        <label class="switch-label" title="${p.enabled ? "点击禁用" : "点击启用"}">
          <input type="checkbox" data-act="toggle" ${p.enabled ? "checked" : ""} />
          <span class="switch"></span>
        </label>
      </div>
      <div class="card-url" title="${escapeHtml(p.base_url)}">${escapeHtml(p.base_url)}</div>
      <div class="card-chips">${chips}</div>
      <div class="card-foot">
        <span class="card-count">共 ${p.models.length} 个模型</span>
        <div class="card-actions">
          <button class="btn small" data-act="duplicate" title="以此卡片为模板新建一个供应商（换个 Key 即可）">复制卡片</button>
          <button class="btn small" data-act="copy" title="复制配置 JSON 到剪贴板">复制配置</button>
          <button class="btn small" data-act="edit">编辑</button>
          <button class="btn small danger-text" data-act="delete">删除</button>
        </div>
      </div>`;

    card.querySelector('[data-act="toggle"]').addEventListener("change", async (e) => {
      p.enabled = e.target.checked;
      await invoke("save_providers", { providers });
      renderList();
    });
    card.querySelector('[data-act="duplicate"]').addEventListener("click", () => openEditor(p, true));
    card.querySelector('[data-act="edit"]').addEventListener("click", () => openEditor(p));
    card.querySelector('[data-act="copy"]').addEventListener("click", async (e) => {
      const ok = await copyText(JSON.stringify(p, null, 2));
      toast(ok ? "已复制供应商配置（含 API Key）到剪贴板" : "复制失败", !ok);
    });
    card.querySelector('[data-act="delete"]').addEventListener("click", async () => {
      if (!confirm(`确定删除供应商「${p.name}」吗？`)) return;
      providers = providers.filter((x) => x.id !== p.id);
      await invoke("save_providers", { providers });
      renderList();
      toast("已删除");
    });

    cards.appendChild(card);
  }
}

// ---------- 编辑页 ----------

function openEditor(provider, duplicate = false) {
  editor = provider
    ? {
        editing: duplicate ? null : provider.id,
        name: duplicate ? `${provider.name}（副本）` : provider.name,
        base: provider.base_url,
        key: provider.api_key,
        filter: "",
        fetching: false,
        error: null,
        models: provider.models.map((m) => ({ id: m, checked: true, status: "idle", ms: null, err: null })),
      }
    : {
        editing: null,
        name: "",
        base: "",
        key: "",
        filter: "",
        fetching: false,
        error: null,
        models: [],
      };

  $("editor-title").textContent =
    editor.editing != null ? "编辑供应商" : duplicate ? "复制供应商" : "新增供应商";
  $("f-name").value = editor.name;
  $("f-base").value = editor.base;
  $("f-key").value = editor.key;
  $("f-filter").value = "";
  $("only-passed").checked = false;
  renderEditor();
  switchView("editor-view");
  $("f-name").focus();
}

function switchView(id) {
  $("list-view").classList.toggle("hidden", id !== "list-view");
  $("editor-view").classList.toggle("hidden", id !== "editor-view");
}

function renderEditor() {
  if (!editor) return;
  $("f-name").value = editor.name;
  $("f-base").value = editor.base;
  $("f-key").value = editor.key;
  $("f-filter").value = editor.filter;

  const errEl = $("editor-error");
  if (editor.error) {
    errEl.textContent = "⚠ " + editor.error;
    errEl.classList.remove("hidden");
  } else {
    errEl.classList.add("hidden");
  }

  $("btn-fetch").disabled = editor.fetching;
  $("btn-fetch").textContent = editor.fetching ? "获取中…" : "获取模型列表";

  const checked = editor.models.filter((m) => m.checked).length;
  const passed = editor.models.filter((m) => m.status === "ok").length;
  $("model-stat").textContent = editor.models.length
    ? `已勾选 ${checked} / ${editor.models.length} · 通过 ${passed}`
    : "";

  $("btn-test-all").disabled = !editor.models.length;
  $("btn-check-passed").disabled = !editor.models.length;

  const listEl = $("model-list");
  if (!editor.models.length) {
    listEl.innerHTML = `<div class="model-empty">${
      editor.fetching ? "正在获取模型列表…" : "填写信息后按回车或点击「获取模型列表」"
    }</div>`;
    return;
  }

  const filter = editor.filter.trim().toLowerCase();
  const visible = editor.models.filter(
    (m) =>
      (!filter || m.id.toLowerCase().includes(filter)) &&
      (!editor.onlyPassed || m.status === "ok")
  );

  listEl.innerHTML = "";
  visible.forEach((m, idx) => {
    const row = document.createElement("div");
    row.className = "model-row" + (idx % 2 ? " striped" : "");

    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "m-check";
    check.checked = m.checked;
    check.addEventListener("change", () => { m.checked = check.checked; renderStatOnly(); });

    const idWrap = document.createElement("div");
    idWrap.className = "model-id-wrap";
    const idEl = document.createElement("span");
    idEl.className = "model-id";
    idEl.textContent = m.id;
    idEl.title = m.id;
    const copyBtn = document.createElement("button");
    copyBtn.className = "btn icon-btn";
    copyBtn.title = "复制模型 ID";
    copyBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    copyBtn.addEventListener("click", async () => {
      const ok = await copyText(m.id);
      toast(ok ? `已复制模型 ID：${m.id}` : "复制失败", !ok);
    });
    idWrap.append(idEl, copyBtn);

    const badge = document.createElement("span");
    badge.className = "badge " + m.status;
    if (m.status === "idle") badge.innerHTML = "—";
    else if (m.status === "testing") badge.innerHTML = `<span class="spinner"></span>测试中…`;
    else if (m.status === "ok") badge.innerHTML = `✓ 通过 · ${m.ms}ms`;
    else {
      badge.innerHTML = `<span class="msg">✕ ${escapeHtml(truncate(m.err || "失败", 30))}</span>`;
      badge.title = m.err || "";
    }

    const btn = document.createElement("button");
    btn.className = "btn small";
    btn.textContent = "测试";
    btn.disabled = m.status === "testing";
    btn.addEventListener("click", () => testOne(m.id));

    row.append(check, idWrap, badge, btn);
    listEl.appendChild(row);
  });
}

function renderStatOnly() {
  if (!editor) return;
  const checked = editor.models.filter((m) => m.checked).length;
  const passed = editor.models.filter((m) => m.status === "ok").length;
  $("model-stat").textContent = editor.models.length
    ? `已勾选 ${checked} / ${editor.models.length} · 通过 ${passed}`
    : "";
}

// 获取模型列表
async function fetchModels() {
  const base = $("f-base").value.trim();
  const key = $("f-key").value.trim();
  if (!base || !key) {
    editor.error = "请先填写 Base URL 和 API Key";
    renderEditor();
    return;
  }
  editor.base = base;
  editor.key = key;
  editor.fetching = true;
  editor.error = null;
  // 同时重置筛选与"只看通过"，避免列表清空后残留旧状态
  editor.filter = "";
  editor.onlyPassed = false;
  $("only-passed").checked = false;
  $("f-filter").value = "";
  renderEditor();
  try {
    // 每次获取都清空旧模型列表并重新加载（不保留上次的勾选与测试结果）
    const ids = await invoke("fetch_models", { baseUrl: base, apiKey: key });
    editor.models = ids.map((id) => ({ id, checked: false, status: "idle", ms: null, err: null }));
    editor.pending = 0;
  } catch (e) {
    editor.error = String(e);
  }
  editor.fetching = false;
  renderEditor();
}

// 测试完成后(没有进行中的测试)给出汇总提示
function maybeFinishToast() {
  if (!editor || editor.pending > 0) return;
  const total = editor.models.length;
  const ok = editor.models.filter((m) => m.status === "ok").length;
  const fail = editor.models.filter((m) => m.status === "fail").length;
  const untested = total - ok - fail;
  if (!total || untested === total) return;
  let msg = `测试完成：✅ 通过 ${ok}`;
  if (fail) msg += ` · ❌ 失败 ${fail}`;
  if (untested) msg += ` · 未测 ${untested}`;
  toast(msg, fail > 0, 4000);
}

// 测试单个模型
async function testOne(id) {
  const m = editor.models.find((x) => x.id === id);
  if (!m) return;
  m.status = "testing";
  m.err = null;
  editor.pending = (editor.pending || 0) + 1;
  renderEditor();
  const base = $("f-base").value.trim();
  const key = $("f-key").value.trim();
  try {
    m.ms = await invoke("test_model", { baseUrl: base, apiKey: key, model: id });
    m.status = "ok";
  } catch (e) {
    m.status = "fail";
    m.err = String(e);
  }
  editor.pending--;
  renderEditor();
  maybeFinishToast();
}

// 保存
async function saveEditor() {
  editor.name = $("f-name").value;
  editor.base = $("f-base").value;
  editor.key = $("f-key").value;
  if (!editor.name.trim()) return showEditorError("请填写供应商名称");
  if (!editor.base.trim()) return showEditorError("请填写 Base URL");
  if (!editor.key.trim()) return showEditorError("请填写 API Key");

  const models = editor.models.filter((m) => m.checked).map((m) => m.id);
  const existing = editor.editing != null ? providers.find((p) => p.id === editor.editing) : null;
  const provider = {
    id: existing ? existing.id : (providers.reduce((a, p) => Math.max(a, p.id), 0) + 1),
    name: editor.name.trim(),
    base_url: editor.base.trim(),
    api_key: editor.key.trim(),
    models,
    enabled: existing ? existing.enabled : true,
  };
  if (existing) {
    providers.splice(providers.indexOf(existing), 1, provider);
  } else {
    providers.push(provider);
  }
  await invoke("save_providers", { providers });
  switchView("list-view");
  editor = null;
  renderList();
  toast("已保存");
}

function showEditorError(msg) {
  editor.error = msg;
  renderEditor();
}

// ---------- 事件绑定 ----------

$("btn-add").addEventListener("click", () => openEditor(null));
$("btn-key-show").addEventListener("click", () => {
  const keyInput = $("f-key");
  const show = keyInput.type === "password";
  keyInput.type = show ? "text" : "password";
  $("btn-key-show").textContent = show ? "隐藏" : "显示";
});
$("btn-back").addEventListener("click", () => { editor = null; switchView("list-view"); renderList(); });
$("btn-save").addEventListener("click", saveEditor);
$("btn-fetch").addEventListener("click", fetchModels);
$("btn-test-all").addEventListener("click", () => {
  editor.models.filter((m) => m.status !== "testing").forEach((m) => testOne(m.id));
});
$("btn-check-passed").addEventListener("click", () => {
  const passed = editor.models.filter((m) => m.status === "ok");
  if (!passed.length) return toast("还没有测试通过的模型", true);
  const before = editor.models.filter((m) => m.checked).length;
  const unchecked = before - passed.length; // 原已勾选但本次不通过的
  editor.models.forEach((m) => (m.checked = m.status === "ok"));
  renderEditor();
  const extra = unchecked > 0 ? `，已去掉 ${unchecked} 个未通过/未测的` : "";
  toast(`已勾选 ${passed.length} 个测试通过的模型${extra}`);
});
$("show-disabled").addEventListener("change", (e) => {
  showDisabled = e.target.checked;
  renderList();
});
$("f-filter").addEventListener("input", (e) => {
  editor.filter = e.target.value;
  renderEditor();
});
$("only-passed").addEventListener("change", (e) => {
  editor.onlyPassed = e.target.checked;
  renderEditor();
});
$("f-name").addEventListener("input", (e) => { if (editor) editor.name = e.target.value; });
$("f-base").addEventListener("input", (e) => { if (editor) editor.base = e.target.value; });

// API Key 输入框回车 → 获取模型列表
$("f-key").addEventListener("keydown", (e) => {
  if (e.key === "Enter") fetchModels();
});
// Base URL 回车 → 跳到 API Key
$("f-base").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("f-key").focus();
});

// ---------- 启动 ----------

refreshProviders();
