use eframe::egui;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::mpsc;
use std::time::{Duration, Instant};

// ---------- 主题 ----------

const ACCENT: egui::Color32 = egui::Color32::from_rgb(0x5B, 0x8D, 0xEF);
const ACCENT_DIM: egui::Color32 = egui::Color32::from_rgb(0x5B, 0x8D, 0xEF).gamma_multiply(0.18);
const OK_GREEN: egui::Color32 = egui::Color32::from_rgb(0x7A, 0xD1, 0x8E);
const BAD_RED: egui::Color32 = egui::Color32::from_rgb(0xE5, 0x74, 0x6C);
const WARN_YELLOW: egui::Color32 = egui::Color32::from_rgb(0xE3, 0xB3, 0x4D);
const MUTED: egui::Color32 = egui::Color32::from_rgb(0x9A, 0xA3, 0xB2);

// ---------- 数据模型 ----------

#[derive(Clone, Serialize, Deserialize)]
struct Provider {
    id: u64,
    name: String,
    base_url: String,
    api_key: String,
    /// 保存(勾选)的模型
    models: Vec<String>,
    enabled: bool,
}

#[derive(Clone, PartialEq)]
enum Status {
    Untested,
    Testing,
    Success(u64),
    Failed(String),
}

struct ModelRow {
    id: String,
    checked: bool,
    status: Status,
}

enum Screen {
    List,
    Editor(EditorState),
}

struct EditorState {
    /// 正在编辑的供应商 id,None 表示新增
    editing: Option<u64>,
    name: String,
    base_url: String,
    api_key: String,
    models: Vec<ModelRow>,
    fetching: bool,
    filter: String,
    error: Option<String>,
}

enum AppEvent {
    ModelsFetched(Vec<String>),
    TestDone {
        model: String,
        result: Result<u64, String>,
    },
}

// ---------- 工具函数 ----------

fn data_dir() -> PathBuf {
    dirs::data_dir().unwrap_or_else(|| PathBuf::from(".")).join("model-tester")
}

fn providers_path() -> PathBuf {
    data_dir().join("providers.json")
}

fn load_providers() -> Vec<Provider> {
    std::fs::read(providers_path())
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

/// 拼接 API 地址：末尾带 /v1 则直接用，否则自动补 /v1；以 # 结尾表示按输入原样使用。
fn build_url(base: &str, path: &str) -> String {
    let mut b = base.trim().trim_end_matches('/').to_string();
    if b.ends_with('#') {
        b.pop();
    } else if !b.ends_with("/v1") {
        b.push_str("/v1");
    }
    format!("{b}{path}")
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(max).collect::<String>())
    }
}

fn error_message_from_body(text: &str) -> String {
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(text) {
        if let Some(msg) = v["error"]["message"].as_str() {
            return truncate(msg, 300);
        }
        if let Some(msg) = v["error"].as_str() {
            return truncate(msg, 300);
        }
        if let Some(msg) = v["message"].as_str() {
            return truncate(msg, 300);
        }
    }
    if text.trim().is_empty() {
        "(服务器返回空内容)".to_string()
    } else {
        truncate(text.trim(), 300)
    }
}

fn make_client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(60))
        .connect_timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| format!("创建 HTTP 客户端失败: {e}"))
}

// ---------- 后台任务 ----------

fn spawn_fetch_models(tx: mpsc::Sender<AppEvent>, base_url: String, api_key: String) {
    std::thread::spawn(move || {
        let result = (|| -> Result<Vec<String>, String> {
            let url = build_url(&base_url, "/models");
            let client = make_client()?;
            let resp = client
                .get(&url)
                .bearer_auth(&api_key)
                .send()
                .map_err(|e| format!("请求失败: {e}"))?;
            let status = resp.status();
            let text = resp.text().map_err(|e| format!("读取响应失败: {e}"))?;
            if !status.is_success() {
                return Err(format!("HTTP {status}: {}", error_message_from_body(&text)));
            }
            let v: serde_json::Value = serde_json::from_str(&text)
                .map_err(|_| format!("响应不是合法 JSON: {}", truncate(&text, 200)))?;
            let mut ids: Vec<String> = Vec::new();
            if let Some(arr) = v["data"].as_array() {
                for m in arr {
                    if let Some(id) = m["id"].as_str() {
                        ids.push(id.to_string());
                    }
                }
            }
            // 兼容 {"models": [...]} 形式
            if ids.is_empty() {
                if let Some(arr) = v["models"].as_array() {
                    for m in arr {
                        let id = m
                            .as_str()
                            .map(|s| s.to_string())
                            .or_else(|| m["id"].as_str().map(|s| s.to_string()))
                            .or_else(|| m["name"].as_str().map(|s| s.to_string()));
                        if let Some(id) = id {
                            ids.push(id);
                        }
                    }
                }
            }
            ids.sort();
            if ids.is_empty() {
                return Err("模型列表为空".to_string());
            }
            Ok(ids)
        })();
        let _ = tx.send(match result {
            Ok(ids) => AppEvent::ModelsFetched(ids),
            Err(e) => AppEvent::TestDone {
                model: String::new(),
                result: Err(e),
            },
        });
    });
}

fn spawn_test_model(tx: mpsc::Sender<AppEvent>, base_url: String, api_key: String, model: String) {
    std::thread::spawn(move || {
        let start = Instant::now();
        let result = (|| -> Result<(), String> {
            let url = build_url(&base_url, "/chat/completions");
            let client = make_client()?;
            let body = serde_json::json!({
                "model": model,
                "messages": [{"role": "user", "content": "你好，请只回复：ok"}]
            });
            let resp = client
                .post(&url)
                .bearer_auth(&api_key)
                .json(&body)
                .send()
                .map_err(|e| format!("请求失败: {e}"))?;
            let status = resp.status();
            let text = resp.text().map_err(|e| format!("读取响应失败: {e}"))?;
            if !status.is_success() {
                return Err(format!("HTTP {status}: {}", error_message_from_body(&text)));
            }
            let v: serde_json::Value = serde_json::from_str(&text)
                .map_err(|_| format!("响应不是合法 JSON: {}", truncate(&text, 200)))?;
            // 兼容标准响应和部分服务把内容包在 data 字段里的写法
            let choices = v["choices"]
                .as_array()
                .or_else(|| v["data"]["choices"].as_array());
            if choices.map_or(true, |c| c.is_empty()) {
                return Err(format!("响应中没有 choices: {}", truncate(&text, 200)));
            }
            Ok(())
        })();
        let latency_ms = start.elapsed().as_millis() as u64;
        let _ = tx.send(AppEvent::TestDone {
            model,
            result: match result {
                Ok(()) => Ok(latency_ms),
                Err(e) => Err(e),
            },
        });
    });
}

// ---------- 应用 ----------

struct App {
    providers: Vec<Provider>,
    next_id: u64,
    screen: Screen,
    show_disabled: bool,
    rx: mpsc::Receiver<AppEvent>,
    tx: mpsc::Sender<AppEvent>,
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        load_cjk_font(&cc.egui_ctx);
        apply_theme(&cc.egui_ctx);

        let (tx, rx) = mpsc::channel();
        let providers = load_providers();
        let next_id = providers.iter().map(|p| p.id).max().unwrap_or(0) + 1;
        Self {
            providers,
            next_id,
            screen: Screen::List,
            show_disabled: false,
            rx,
            tx,
        }
    }

    fn save_providers(&self) {
        let dir = data_dir();
        let _ = std::fs::create_dir_all(&dir);
        if let Ok(json) = serde_json::to_string_pretty(&self.providers) {
            let _ = std::fs::write(providers_path(), json);
        }
    }

    fn open_editor(&mut self, editing: Option<u64>) {
        let state = match self.providers.iter().find(|p| p.id == editing) {
            Some(p) => EditorState {
                editing: Some(p.id),
                name: p.name.clone(),
                base_url: p.base_url.clone(),
                api_key: p.api_key.clone(),
                models: p.models.iter().map(|m| ModelRow {
                    id: m.clone(),
                    checked: true,
                    status: Status::Untested,
                }).collect(),
                fetching: false,
                filter: String::new(),
                error: None,
            },
            None => EditorState {
                editing: None,
                name: String::new(),
                base_url: String::new(),
                api_key: String::new(),
                models: Vec::new(),
                fetching: false,
                filter: String::new(),
                error: None,
            },
        };
        self.screen = Screen::Editor(state);
    }

    fn start_fetch(tx: mpsc::Sender<AppEvent>, editor: &mut EditorState) {
        if editor.base_url.trim().is_empty() || editor.api_key.trim().is_empty() {
            editor.error = Some("请先填写 Base URL 和 API Key".into());
            return;
        }
        editor.fetching = true;
        editor.error = None;
        spawn_fetch_models(
            tx,
            editor.base_url.clone(),
            editor.api_key.trim().to_string(),
        );
    }

    fn poll_events(&mut self, ctx: &egui::Context) {
        while let Ok(ev) = self.rx.try_recv() {
            if let Screen::Editor(ed) = &mut self.screen {
                match ev {
                    AppEvent::ModelsFetched(ids) => {
                        ed.fetching = false;
                        let old: std::collections::HashMap<String, (bool, Status)> = ed
                            .models
                            .drain(..)
                            .map(|r| (r.id, (r.checked, r.status)))
                            .collect();
                        ed.models = ids
                            .into_iter()
                            .map(|id| {
                                let (checked, status) =
                                    old.get(&id).cloned().unwrap_or((false, Status::Untested));
                                ModelRow { id, checked, status }
                            })
                            .collect();
                    }
                    AppEvent::TestDone { model, result } => {
                        if model.is_empty() {
                            ed.fetching = false;
                            ed.error = result.err();
                            continue;
                        }
                        let status = match &result {
                            Ok(ms) => Status::Success(*ms),
                            Err(e) => Status::Failed(e.clone()),
                        };
                        if let Some(row) = ed.models.iter_mut().find(|r| r.id == model) {
                            row.status = status;
                        }
                    }
                }
            }
        }
        ctx.request_repaint_after(Duration::from_millis(100));
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll_events(ctx);
        match &mut self.screen {
            Screen::List => self.ui_list(ctx),
            Screen::Editor(_) => self.ui_editor(ctx),
        }
    }
}

// ---------- 列表页 ----------

impl App {
    fn ui_list(&mut self, ctx: &egui::Context) {
        egui::TopBottomPanel::top("header").show(ctx, |ui| {
            ui.add_space(14.0);
            ui.horizontal(|ui| {
                ui.add_space(6.0);
                ui.vertical(|ui| {
                    ui.label(egui::RichText::new("模型供应商").size(20.0).strong());
                    let enabled = self.providers.iter().filter(|p| p.enabled).count();
                    ui.label(
                        egui::RichText::new(format!("{enabled} 个启用 / 共 {} 个", self.providers.len()))
                            .size(12.0)
                            .color(MUTED),
                    );
                });
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    ui.add_space(6.0);
                    let add = egui::Button::new(
                        egui::RichText::new("  ＋ 新增供应商  ").strong(),
                    )
                    .fill(ACCENT)
                    .rounding(8.0);
                    if ui.add(add).clicked() {
                        self.open_editor(None);
                    }
                    ui.checkbox(&mut self.show_disabled, "显示已禁用");
                });
            });
            ui.add_space(12.0);
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            let visible: Vec<usize> = self
                .providers
                .iter()
                .enumerate()
                .filter(|(_, p)| self.show_disabled || p.enabled)
                .map(|(i, _)| i)
                .collect();

            if visible.is_empty() {
                ui.add_space(ui.available_height() / 2.5 - 40.0);
                ui.vertical_centered(|ui| {
                    ui.label(egui::RichText::new("还没有供应商").size(17.0).color(MUTED));
                    ui.add_space(6.0);
                    ui.label(egui::RichText::new("点击右上角「＋ 新增供应商」开始").size(13.0).color(MUTED));
                });
                return;
            }

            egui::ScrollArea::vertical().auto_shrink(false).show(ui, |ui| {
                let indices = visible.clone();
                ui.horizontal_wrapped(|ui| {
                    for i in indices {
                        self.provider_card(ui, i);
                    }
                });
            });
        });
    }

    fn provider_card(&mut self, ui: &mut egui::Ui, i: usize) {
        let enabled = self.providers[i].enabled;
        let alpha = |c: egui::Color32| if enabled { c } else { c.gamma_multiply(0.45) };

        let card_bg = if enabled {
            egui::Color32::from_rgb(0x23, 0x27, 0x30)
        } else {
            egui::Color32::from_rgb(0x1C, 0x1F, 0x25)
        };

        egui::Frame::default()
            .fill(card_bg)
            .stroke(egui::Stroke::new(1.0, egui::Color32::from_rgb(0x33, 0x38, 0x44)))
            .rounding(12.0)
            .inner_margin(egui::Margin::symmetric(16.0, 12.0))
            .outer_margin(egui::Margin::symmetric(6.0, 6.0))
            .show(ui, |ui| {
                ui.set_max_width(400.0);

                // 名称 + 启用开关
                ui.horizontal(|ui| {
                    let id = self.providers[i].id;
                    ui.label(
                        egui::RichText::new(&self.providers[i].name)
                            .size(17.0)
                            .strong()
                            .color(alpha(egui::Color32::WHITE)),
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let mut on = self.providers[i].enabled;
                        if ui
                            .add(
                                egui::Switch::new(&mut on)
                                    .text(egui::RichText::new("启用").small().color(MUTED)),
                            )
                            .changed()
                        {
                            self.providers[i].enabled = on;
                            self.save_providers();
                        }
                        let _ = id;
                    });
                });
                ui.add_space(4.0);

                // Base URL
                ui.label(
                    egui::RichText::new(truncate(&self.providers[i].base_url, 46))
                        .size(12.0)
                        .color(alpha(MUTED)),
                );
                ui.add_space(8.0);

                // 模型 chips
                let models = self.providers[i].models.clone();
                if models.is_empty() {
                    ui.label(
                        egui::RichText::new("尚未选择模型，点击「编辑」获取并勾选")
                            .size(12.0)
                            .color(alpha(BAD_RED)),
                    );
                } else {
                    ui.horizontal_wrapped(|ui| {
                        let shown = models.iter().take(6);
                        for m in shown {
                            chip(ui, m, alpha(ACCENT_DIM), alpha(ACCENT));
                        }
                        if models.len() > 6 {
                            chip(ui, &format!("+{}", models.len() - 6),
                                 egui::Color32::from_rgb(0x2C, 0x30, 0x3A), alpha(MUTED));
                        }
                    });
                }
                ui.add_space(4.0);
                ui.label(
                    egui::RichText::new(format!("共 {} 个模型", models.len()))
                        .size(11.0)
                        .color(alpha(MUTED)),
                );
                ui.add_space(8.0);

                // 操作
                ui.horizontal(|ui| {
                    let pid = self.providers[i].id;
                    if ui
                        .add(egui::Button::new("编辑").small().rounding(6.0))
                        .clicked()
                    {
                        self.open_editor(Some(pid));
                    }
                    if ui
                        .add(egui::Button::new(
                            egui::RichText::new("删除").small().color(BAD_RED),
                        ).rounding(6.0))
                        .clicked()
                    {
                        self.providers.retain(|p| p.id != pid);
                        self.save_providers();
                    }
                });
            });
    }
}

fn chip(ui: &mut egui::Ui, text: &str, fill: egui::Color32, color: egui::Color32) {
    egui::Frame::default()
        .fill(fill)
        .rounding(6.0)
        .inner_margin(egui::Margin::symmetric(7.0, 2.5))
        .show(ui, |ui| {
            ui.label(egui::RichText::new(text).size(11.0).color(color));
        });
}

// ---------- 编辑页 ----------

impl App {
    fn ui_editor(&mut self, ctx: &egui::Context) {
        egui::CentralPanel::default().show(ctx, |ui| {
            let (title, editing) = {
                if let Screen::Editor(ed) = &self.screen {
                    (
                        if ed.editing.is_some() { "编辑供应商" } else { "新增供应商" },
                        ed.editing,
                    )
                } else {
                    return;
                }
            };

            // 顶栏
            ui.horizontal(|ui| {
                if ui.add(egui::Button::new("← 返回").rounding(6.0)).clicked() {
                    self.screen = Screen::List;
                    return;
                }
                ui.label(egui::RichText::new(title).size(18.0).strong());
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    let save_btn = egui::Button::new(egui::RichText::new("  保存  ").strong())
                        .fill(ACCENT)
                        .rounding(8.0);
                    if ui.add(save_btn).clicked() {
                        self.save_editor();
                    }
                });
            });
            ui.add_space(10.0);
            ui.separator();
            ui.add_space(10.0);

            let err = (|| {
                if let Screen::Editor(ed) = &self.screen {
                    ed.error.clone()
                } else {
                    None
                }
            })();

            // 基本信息
            egui::Frame::default()
                .fill(egui::Color32::from_rgb(0x23, 0x27, 0x30))
                .stroke(egui::Stroke::new(1.0, egui::Color32::from_rgb(0x33, 0x38, 0x44)))
                .rounding(12.0)
                .inner_margin(egui::Margin::symmetric(16.0, 12.0))
                .show(ui, |ui| {
                    ui.set_max_width(ui.available_width());
                    egui::Grid::new("info")
                        .num_columns(3)
                        .spacing([10.0, 10.0])
                        .show(ui, |ui| {
                            let mut fetch = false;
                            if let Screen::Editor(ed) = &mut self.screen {
                                ui.label("名称");
                                ui.add(
                                    egui::TextEdit::singleline(&mut ed.name)
                                        .hint_text("例如 OpenAI / 硅基流动 / 本地服务")
                                        .desired_width(380.0),
                                );
                                ui.end_row();

                                ui.label("Base URL");
                                let resp = ui.add(
                                    egui::TextEdit::singleline(&mut ed.base_url)
                                        .hint_text("https://api.openai.com（自动补 /v1，末尾加 # 表示原样使用）")
                                        .desired_width(520.0),
                                );
                                fetch |= resp.lost_focus()
                                    && ui.input(|i| i.key_pressed(egui::Key::Enter));
                                ui.end_row();

                                ui.label("API Key");
                                let resp = ui.add(
                                    egui::TextEdit::singleline(&mut ed.api_key)
                                        .password(true)
                                        .hint_text("sk-...（按回车获取模型列表）")
                                        .desired_width(520.0),
                                );
                                fetch |= resp.lost_focus()
                                    && ui.input(|i| i.key_pressed(egui::Key::Enter));
                                let btn = egui::Button::new(if ed.fetching { "获取中…" } else { "获取模型列表" })
                                    .fill(ACCENT)
                                    .rounding(8.0);
                                if ui.add_enabled(!ed.fetching, btn).clicked() {
                                    fetch = true;
                                }
                                ui.end_row();

                                if fetch {
                                    let base = ed.base_url.clone();
                                    let key = ed.api_key.clone();
                                    self.start_fetch(ed);
                                    let _ = (base, key);
                                }
                            }
                        });
                });

            if let Some(e) = err {
                ui.add_space(6.0);
                ui.label(egui::RichText::new(format!("⚠ {e}")).color(BAD_RED).size(13.0));
            }
            ui.add_space(12.0);

            // 模型选择区
            if let Screen::Editor(ed) = &mut self.screen {
                let has_models = !ed.models.is_empty();
                egui::Frame::default()
                    .fill(egui::Color32::from_rgb(0x23, 0x27, 0x30))
                    .stroke(egui::Stroke::new(1.0, egui::Color32::from_rgb(0x33, 0x38, 0x44)))
                    .rounding(12.0)
                    .inner_margin(egui::Margin::symmetric(16.0, 12.0))
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.label(egui::RichText::new("模型列表").size(15.0).strong());
                            let checked = ed.models.iter().filter(|m| m.checked).count();
                            ui.label(
                                egui::RichText::new(format!(
                                    "已勾选 {checked} / {} | ✅ 通过 {}",
                                    ed.models.len(),
                                    ed.models.iter().filter(|m| matches!(m.status, Status::Success(_))).count()
                                ))
                                .size(12.0)
                                .color(MUTED),
                            );
                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                let ctx = ui.ctx().clone();
                                if ui
                                    .add_enabled(
                                        has_models,
                                        egui::Button::new("一键勾选测试通过").rounding(6.0),
                                    )
                                    .clicked()
                                {
                                    for m in &mut ed.models {
                                        m.checked = matches!(m.status, Status::Success(_));
                                    }
                                    ctx.request_repaint();
                                }
                                if ui
                                    .add_enabled(has_models, egui::Button::new("测试全部").rounding(6.0))
                                    .clicked()
                                {
                                    let targets: Vec<String> = ed
                                        .models
                                        .iter()
                                        .filter(|m| !matches!(m.status, Status::Testing))
                                        .map(|m| m.id.clone())
                                        .collect();
                                    let base = ed.base_url.clone();
                                    let key = ed.api_key.trim().to_string();
                                    for id in targets {
                                        if let Some(row) = ed.models.iter_mut().find(|r| r.id == id) {
                                            row.status = Status::Testing;
                                        }
                                        spawn_test_model(self.tx.clone(), base.clone(), key.clone(), id);
                                    }
                                }
                                ui.add(
                                    egui::TextEdit::singleline(&mut ed.filter)
                                        .hint_text("筛选模型")
                                        .desired_width(180.0),
                                );
                            });
                        });
                        ui.add_space(8.0);

                        if ed.models.is_empty() {
                            ui.add_space(20.0);
                            ui.vertical_centered(|ui| {
                                ui.label(
                                    egui::RichText::new(if ed.fetching {
                                        "正在获取模型列表…"
                                    } else {
                                        "填写信息后按回车或点击「获取模型列表」"
                                    })
                                    .color(MUTED),
                                );
                            });
                            ui.add_space(20.0);
                            return;
                        }

                        let filter = ed.filter.to_lowercase();
                        let visible: Vec<usize> = ed
                            .models
                            .iter()
                            .enumerate()
                            .filter(|(_, m)| filter.is_empty() || m.id.to_lowercase().contains(&filter))
                            .map(|(i, _)| i)
                            .collect();

                        egui::ScrollArea::vertical()
                            .auto_shrink(false)
                            .max_height(ui.available_height() - 20.0)
                            .show(ui, |ui| {
                                egui::Grid::new("models")
                                    .striped(true)
                                    .num_columns(4)
                                    .min_col_width(60.0)
                                    .spacing([12.0, 4.0])
                                    .show(ui, |ui| {
                                        ui.strong("选择");
                                        ui.strong("模型 ID");
                                        ui.strong("连通性");
                                        ui.strong("操作");
                                        ui.end_row();

                                        for i in visible.clone() {
                                            let (id, checked, testing) = match ed.models.get(i) {
                                                Some(m) => (
                                                    m.id.clone(),
                                                    m.checked,
                                                    matches!(m.status, Status::Testing),
                                                ),
                                                None => continue,
                                            };

                                            ui.checkbox(&mut ed.models[i].checked, "");
                                            ui.label(&id);

                                            match &ed.models[i].status {
                                                Status::Untested => {
                                                    ui.label(egui::RichText::new("—").color(MUTED))
                                                }
                                                Status::Testing => ui
                                                    .label(egui::RichText::new("⏳ 测试中…").color(WARN_YELLOW)),
                                                Status::Success(ms) => ui.label(
                                                    egui::RichText::new(format!("✅ 通过 · {}ms", ms))
                                                        .color(OK_GREEN),
                                                ),
                                                Status::Failed(e) => ui
                                                    .label(
                                                        egui::RichText::new(format!("❌ {}", truncate(e, 70)))
                                                            .color(BAD_RED),
                                                    )
                                                    .on_hover_text(e),
                                            };

                                            if ui
                                                .add_enabled(!testing, egui::Button::new("测试").small().rounding(6.0))
                                                .clicked()
                                            {
                                                ed.models[i].status = Status::Testing;
                                                spawn_test_model(
                                                    self.tx.clone(),
                                                    ed.base_url.clone(),
                                                    ed.api_key.trim().to_string(),
                                                    id,
                                                );
                                            }
                                            ui.end_row();
                                        }
                                    });
                            });
                    });
            }
        });
    }

    fn save_editor(&mut self) {
        if let Screen::Editor(ed) = &mut self.screen {
            if ed.name.trim().is_empty() {
                ed.error = Some("请填写供应商名称".into());
                return;
            }
            if ed.base_url.trim().is_empty() {
                ed.error = Some("请填写 Base URL".into());
                return;
            }
            if ed.api_key.trim().is_empty() {
                ed.error = Some("请填写 API Key".into());
                return;
            }
            let models: Vec<String> = ed
                .models
                .iter()
                .filter(|m| m.checked)
                .map(|m| m.id.clone())
                .collect();
            let p = Provider {
                id: match ed.editing {
                    Some(id) => id,
                    None => {
                        let id = self.next_id;
                        self.next_id += 1;
                        id
                    }
                },
                name: ed.name.trim().to_string(),
                base_url: ed.base_url.trim().to_string(),
                api_key: ed.api_key.trim().to_string(),
                models,
                enabled: match ed.editing {
                    Some(id) => self
                        .providers
                        .iter()
                        .find(|p| p.id == id)
                        .map(|p| p.enabled)
                        .unwrap_or(true),
                    None => true,
                },
            };
            match self.providers.iter().position(|x| x.id == p.id) {
                Some(i) => self.providers[i] = p,
                None => self.providers.push(p),
            }
            self.save_providers();
            self.screen = Screen::List;
        }
    }
}

// ---------- 主题与字体 ----------

fn apply_theme(ctx: &egui::Context) {
    let mut vis = egui::Visuals::dark();
    vis.panel_fill = egui::Color32::from_rgb(0x17, 0x19, 0x1F);
    vis.window_fill = egui::Color32::from_rgb(0x1C, 0x1F, 0x25);
    vis.extreme_bg_color = egui::Color32::from_rgb(0x12, 0x14, 0x18);
    vis.faint_bg_color = egui::Color32::from_rgb(0x20, 0x23, 0x2B);
    let r6 = egui::Rounding::same(6.0);
    vis.widgets.inactive.rounding = r6;
    vis.widgets.hovered.rounding = r6;
    vis.widgets.active.rounding = r6;
    vis.widgets.noninteractive.rounding = r6;
    vis.widgets.open.rounding = r6;
    vis.selection.stroke.color = ACCENT;
    vis.widgets.hovered.fg_stroke.color = egui::Color32::WHITE;
    ctx.set_visuals(vis);
}

fn load_cjk_font(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    let candidates = ["msyh.ttc", "msyh.ttf", "simhei.ttf", "simsun.ttc"];
    for name in candidates {
        let path = PathBuf::from(r"C:\Windows\Fonts").join(name);
        if let Ok(data) = std::fs::read(&path) {
            fonts
                .font_data
                .insert("cjk".into(), egui::FontData::from_owned(data));
            for family in fonts.families.values_mut() {
                family.insert(0, "cjk".into());
            }
            break;
        }
    }
    ctx.set_fonts(fonts);
}

fn main() -> eframe::Result {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("模型供应商管理")
            .with_inner_size([1080.0, 720.0]),
        ..Default::default()
    };
    eframe::run_native(
        "模型供应商管理",
        options,
        Box::new(|cc| Ok(Box::new(App::new(cc)))),
    )
}
