#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::time::{Duration, Instant};
use tauri::Manager;

// ---------- 数据模型 ----------

#[derive(Serialize, Deserialize, Clone)]
struct Provider {
    id: u64,
    name: String,
    base_url: String,
    api_key: String,
    /// 保存(勾选)的模型
    models: Vec<String>,
    enabled: bool,
}

// ---------- 持久化 ----------

fn providers_file(app: &tauri::AppHandle) -> PathBuf {
    let dir = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."));
    let _ = std::fs::create_dir_all(&dir);
    dir.join("providers.json")
}

#[tauri::command]
fn load_providers(app: tauri::AppHandle) -> Vec<Provider> {
    std::fs::read(providers_file(&app))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

#[tauri::command]
fn save_providers(app: tauri::AppHandle, providers: Vec<Provider>) -> Result<(), String> {
    let json = serde_json::to_string_pretty(&providers).map_err(|e| e.to_string())?;
    std::fs::write(providers_file(&app), json).map_err(|e| e.to_string())
}

// ---------- API 工具 ----------

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

fn make_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .connect_timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| format!("创建 HTTP 客户端失败: {e}"))
}

// ---------- 命令 ----------

#[tauri::command]
async fn fetch_models(base_url: String, api_key: String) -> Result<Vec<String>, String> {
    let url = build_url(&base_url, "/models");
    let client = make_client()?;
    let resp = client
        .get(&url)
        .bearer_auth(&api_key)
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| format!("读取响应失败: {e}"))?;
    if !status.is_success() {
        if matches!(status.as_u16(), 401 | 403) {
            return Err(format!(
                "HTTP {status}: 密钥无效或无权限(鉴权失败) - {}",
                error_message_from_body(&text)
            ));
        }
        return Err(format!("HTTP {status}: {}", error_message_from_body(&text)));
    }
    let v: serde_json::Value =
        serde_json::from_str(&text).map_err(|_| format!("响应不是合法 JSON: {}", truncate(&text, 200)))?;

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
}

#[tauri::command]
async fn test_model(base_url: String, api_key: String, model: String) -> Result<u64, String> {
    let url = build_url(&base_url, "/chat/completions");
    let client = make_client()?;
    // 用开放式 prompt 模拟真实对话，避免"请回复ok"被假模型糊弄过
    let body = serde_json::json!({
        "model": model,
        "messages": [{"role": "user", "content": "你好，请用一句话简单介绍一下你自己"}],
        "max_tokens": 128
    });
    let start = Instant::now();
    let resp = client
        .post(&url)
        .bearer_auth(&api_key)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| format!("读取响应失败: {e}"))?;
    if !status.is_success() {
        // 401/403 单独点出来:这类"密钥无效/无权限"绝不算通过
        if matches!(status.as_u16(), 401 | 403) {
            return Err(format!(
                "HTTP {status}: 密钥无效或无权限(鉴权失败,判为不通过) - {}",
                error_message_from_body(&text)
            ));
        }
        return Err(format!("HTTP {status}: {}", error_message_from_body(&text)));
    }
    let v: serde_json::Value =
        serde_json::from_str(&text).map_err(|_| format!("响应不是合法 JSON: {}", truncate(&text, 200)))?;
    // 兼容标准响应和部分服务把内容包在 data 字段里的写法
    let choices = v["choices"]
        .as_array()
        .or_else(|| v["data"]["choices"].as_array());
    let choices = match choices {
        Some(c) if !c.is_empty() => c,
        _ => return Err(format!("响应中没有 choices: {}", truncate(&text, 200))),
    };

    // 提取助手回复的实际内容(兼容 message.content / text / data 几种形态)
    let first = &choices[0];
    let content = first["message"]["content"].as_str()
        .or_else(|| first["text"].as_str())
        .or_else(|| first["data"].as_str())
        .unwrap_or("");
    let content = content.trim();

    // 假通过检查①：内容为空(模型返回了 choices 但没有有效对话内容)
    if content.is_empty() {
        return Err("响应内容为空，模型未返回有效对话内容（假通过，判为不通过）".to_string());
    }

    // 假通过检查②：内容里实际是配额/额度耗尽或错误提示，而非真正对话
    let lower = content.to_lowercase();
    let hit_kw = [
        "quota", "recharge", "topup", "free quota", "prevention of abuse",
        "insufficient", "rate limit", "rate_limit", "exceeded", "exhausted",
        "no available", "overloaded", "no credit", "access denied", "invalid api",
        "未授权", "无权限", "余额不足", "配额", "限流",
    ]
    .iter()
    .find(|kw| lower.contains(*kw));
    if let Some(kw) = hit_kw {
        return Err(format!("内容含错误/限流信息（关键字「{}」）: {}", kw, truncate(content, 120)));
    }

    Ok(start.elapsed().as_millis() as u64)
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            load_providers,
            save_providers,
            fetch_models,
            test_model
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
