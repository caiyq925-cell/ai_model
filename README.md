# ai_model —— 模型供应商管理 + 本地 AI 网关

一个 Windows 本地的「AI 模型接入工具箱」，由两个组件组成，配合使用把你手头一堆 OpenAI 兼容的模型供应商（各家 API Key）统一接入到任意 Agent 工具（ZCode / Cursor / Cline / aider 等）。

```
model-manager  (Tauri 桌面端)        gateway  (Python 本地网关)
┌─────────────────────────┐  共用     ┌──────────────────────────────┐
│ 录入供应商 BaseURL/Key   │──────▶   │ 统一成 OpenAI 兼容端点         │
│ 拉模型列表 / 逐个测连通  │ providers │ http://127.0.0.1:8788/v1     │
│ 勾选保存可用模型         │  .json   │ 路由 / 容灾 / 深度思考开关     │
└─────────────────────────┘          │ 面板 / 批量验证               │
                                     └──────────────┬───────────────┘
                                                    │
                                                    ▼
                              任意 Agent 工具填一个 Base URL 就能用上全部模型
```

| 组件 | 目录 | 技术 | 作用 |
| --- | --- | --- | --- |
| **model-manager** | `model-manager/` | Tauri 2 + Rust + 原生 HTML/JS | 桌面端管理供应商/模型，测试连通性 |
| **gateway** | `gateway/` | Python（纯标准库，零依赖） | 本地网关，把模型统一成 OpenAI 兼容 API |
| model-tester | `model-tester/` | Rust (egui) | 早期版本，功能已并入 model-manager |

两个组件**解耦**：通过同一份 `%APPDATA%\com.baozi.model-manager\providers.json` 交换数据。model-manager 负责「整理数据」，gateway 负责「消费数据」。

---

## 快速上手（日常使用）

### 1. 整理供应商 / 测试模型（可选，一次性）

1. 双击运行 model-manager 的桌面端（见下方「打包」拿到 `.exe`）
2. 录入各家 Base URL + API Key，自动拉模型列表
3. 「测试全部」→ 只留勾选可用的模型 → 保存
   保存后写入 `providers.json`

### 2. 启动网关

```bash
cd D:\Users\ai_model\gateway
python gateway.py          # 默认 127.0.0.1:8788
```

或双击桌面 `启动AI网关.bat`（会自动起网关并打开面板）。

### 3. 在 Agent 工具里配置

| 项 | 值 |
| --- | --- |
| **Base URL** | `http://127.0.0.1:8788/v1` |
| **API Key** | 任意非空字符串（网关默认不校验） |
| **Model** | 从 `http://127.0.0.1:8788/v1/models` 挑一个 |

- **ZCode**：`Settings → 添加 Provider → 类型选 "OpenAI 兼容"`，Base URL 填上面那行，模型填挑好的 id
- **Cursor**：`Settings → Models → API Key / Base URL`
- **Cline (VS Code)**：`API Provider = OpenAI Compatible`
- **aider**：`aider --openai-api-base http://127.0.0.1:8788/v1 --openai-api-key sk-local --model gpt-4o`

### 4. 看面板

浏览器打开 `http://127.0.0.1:8788/`：模型总表（状态/耗时/深度思考）、统计、一键批量验证、复制 Base URL。

---

## 组件一：model-manager（桌面端）

**详细文档**：见 [model-manager/README.md](model-manager/README.md)

功能要点：
- 卡片式供应商列表，支持编辑 / 删除 / 启用禁用
- 拉模型列表（兼容 `{"data":[...]}` 与 `{"models":[...]}` 两种返回）
- 逐个或批量测试连通性（真发 `/chat/completions` 最小对话）
- 勾选保存，一键复制整份配置 JSON 到剪贴板（含 Key，方便备份迁移）

数据存 `providers.json`，地址拼接规则：
- 末尾补 `/v1`；填到 `/v1` 结尾直接用；以 `#` 结尾则按输入原样用（兼容路径不规范的网关）

---

## 组件二：gateway（本地网关）

**详细文档**：见 [gateway/README.md](gateway/README.md)

能力：
- 读 `providers.json`，**热加载**（在 model-manager 改完下一个请求即生效）
- **按模型路由**：同名模型多家提供时**轮询分摊 + 故障自动切换**（某家 5xx/429/断连自动换下一家）
- **钉定供应商**：`供应商名/模型名`（或 `供应商名::模型名`）强制走某一家
- **兼容包裹响应**：个别网关把内容包在 `{"data":{"choices":...}}` 里，流式/非流式都自动解包
- **深度思考开关**：`gateway_config.json` 的 `model_defaults` 在转发前自动合并进请求体（见下）
- **可视化面板** `GET /` + **批量验证** `verify.py`

### 深度思考（thinking / reasoning）

不同上游开关字段不一，网关用 `gateway_config.json` 统一处理，客户端不用关心上游差异：

```json
{
  "model_defaults": {
    "Qwen3.8-Flash-Next": {"enable_thinking": false},
    "DeepSeek-V4-Pro":    {"thinking": {"type": "disabled"}},
    "kimi-k3":            {"enable_thinking": false}
  },
  "reasoning_override": {}
}
```

- `model_defaults`：转发前合并进请求体，**客户端显式传的同名字段优先**
- `reasoning_override`：手动标记某模型「有/无思考」（只影响面板显示）
- 改完保存即热生效，无需重启

常见上游写法：

| 上游风格 | 关思考 |
| --- | --- |
| vLLM / Qwen 兼容 | `{"enable_thinking": false}` |
| DeepSeek | `{"thinking": {"type": "disabled"}}` |
| OpenRouter | `{"reasoning": {"enabled": false}}` |
| OpenAI o 系列 | `{"reasoning_effort": "low"}` |

### 批量验证模型可用性

```bash
python verify.py                          # 通过网关逐模型发最小对话,记录可用/耗时/是否思考
python verify.py --key sk-my-secret       # 网关启动带了 --key 时必须给同一个,否则全部 401
python verify.py --only qwen,gpt-4        # 只测关键字
python verify.py --workers 6 --timeout 150 # 并发/超时
```

也可在面板点「▶ 验证全部模型」（面板会自动带上网关密钥）。结果写 `verify_results.json`，面板每 5 秒自动刷新。

状态：`ok` 可用 / `auth_error` 鉴权失败(401/403) / `no_choices` 上游 200 但无内容（限流/模型名错）/ `quota` 额度耗尽 / `http_error` 4xx/5xx / `timeout` / `dead`。**只有 `ok` 算通过**；验证整体没跑通时结果文件带 `error`，面板顶部红条提示并把历史结果置灰，不会拿旧结论冒充通过。

> 实测（19 家供应商 / 97 模型）：可用约 56 个。`-free` 免费模型大多额度耗尽会返回"只能尝试 10 次"，属上游行为，网关只透传。

---

## 打包

### model-manager（Tauri 桌面端）

**前置**：Rust stable、Node.js 18+、Windows 10/11（自带 WebView2）。

```bash
cd D:\Users\ai_model\model-manager
npm install                # 安装 @tauri-apps/cli
npx tauri dev              # 开发调试(热更新)
npx tauri build            # 打 release
```

`npx tauri build` 产物在 `src-tauri/target/release/`：

| 文件 | 说明 |
| --- | --- |
| `bundle/nsis/*.exe` | NSIS 安装程序（一键安装，当前配置的默认产物） |
| `model-manager.exe` | 单文件可执行（绿色版，直接拷贝即可用） |

- 默认 bundle 目标是 **NSIS 安装程序**（`tauri.conf.json` 里 `bundle.targets: ["nsis"]`），中文界面
- 想要**绿色单文件**：直接拿 `model-manager.exe`（依赖 WebView2，Win11 自带）
- 已打的版本：`model-manager-v0.2.1-windows-x64.zip`（仓库根目录，含 exe）

改版本：`src-tauri/tauri.conf.json` 的 `version` + `Cargo.toml` 的 `version` 一起改。

### gateway（Python 网关）

**无需打包**——纯标准库单文件，拷过去 `python gateway.py` 就能跑。要分发给别人：

```bash
# 绿色版:把这几个文件拷到对方机器,对方装 Python 3.8+ 即可
gateway/gateway.py
gateway/gateway_config.json
gateway/verify.py

# 或打成 exe(可选,免 Python 环境)
pip install pyinstaller
cd gateway
pyinstaller -F -n ai-gateway gateway.py
# 产物在 dist/ai-gateway.exe,启动后同样读 providers.json
```

> PyInstaller 打包时注意把 `gateway_config.json` 一起带上（它是运行时读取的外部文件），或让对方把配置放脚本同级目录。

### 一键启动脚本

桌面 `启动AI网关.bat`（内容见 `gateway/start.bat`）：起网关 + 3 秒后自动打开面板。

---

## 目录结构

```
ai_model/
├── README.md                 本文件(项目总览)
├── model-manager/            Tauri 桌面端(供应商管理)
│   ├── README.md             详细文档
│   ├── ui/                    前端(原生 HTML/CSS/JS)
│   ├── src-tauri/            Rust 后端(Tauri 2)
│   │   └── target/release/   打包产物
│   └── model-manager-v0.2.1-windows-x64.zip
├── gateway/                  Python 本地网关
│   ├── README.md             详细文档
│   ├── gateway.py            网关主程序(面板/验证/思考开关)
│   ├── verify.py            批量验证全部模型
│   ├── gateway_config.json   模型默认参数/思考开关(热加载)
│   ├── verify_results.json   验证结果(面板读取)
│   ├── mock_upstream.py       mock 上游(自测容灾/解包/流式)
│   ├── test_providers.json    mock 测试用供应商
│   └── start.bat            一键启动
└── model-tester/            早期 Rust 版本(已并入 model-manager)
```

---

## 安全注意

- `providers.json` 与各配置里 **API Key 为明文**，别提交到仓库、别把 `0.0.0.0` 网关暴露到公网
- 局域网共享网关时务必加密钥：`python gateway.py --host 0.0.0.0 --key sk-xxxx`（客户端需带 `Bearer sk-xxxx`）
