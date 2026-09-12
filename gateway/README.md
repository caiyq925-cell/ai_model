# 本地 AI 网关 (gateway)

把 [model-manager](../model-manager) 里已经验证通过的供应商和模型，统一成一个**本机 OpenAI 兼容端点**。启动后，任何支持自定义 OpenAI 地址的工具（Cursor / Cline / Continue / aider / 任意 SDK）填一个本地地址，就能用上你保存的全部模型，不用逐个配 key。

零第三方依赖，只用 Python 标准库，`python gateway.py` 即可启动。

## 它解决什么

- **一个地址用全部模型**：13 家供应商、近百个模型，收进 `http://127.0.0.1:8788/v1` 一个端点
- **自动配 key**：请求发到网关，网关按模型找到对应供应商，注入正确的 `Authorization` 再转发
- **多供应商容灾**：同名模型若多家提供，请求轮询分摊；某家报错/限流自动切下一家
- **配置热加载**：model-manager 里改供应商/模型，网关下一个请求即生效，无需重启
- **响应兼容**：个别网关把内容包在 `{"data":{"choices":...}}` 里，这里流式/非流式都自动解包

## 快速开始

```bash
# 在 model-manager 里先保存好供应商和可用模型,然后:
python gateway.py
# 读取 %APPDATA%\com.baozi.model-manager\providers.json
# 默认监听 http://127.0.0.1:8788

# 换个端口
python gateway.py --port 9000

# 局域网共享,并要求访问密钥(别人必须带 Bearer 密钥才能用)
python gateway.py --host 0.0.0.0 --key sk-my-secret

# 指定 providers.json 路径
python gateway.py --providers "D:/some/where/providers.json"
```

启动自检：

```bash
▷ python gateway.py --port 8788
[gateway] 已加载 13 个供应商, 97 个模型
[gateway] OpenAI 兼容地址: http://127.0.0.1:8788/v1  (模型列表 http://127.0.0.1:8788/v1/models)

▷ curl http://127.0.0.1:8788/v1/models | python -m json.tool
{
  "object": "list",
  "data": [
    {"id": "gpt-4o-free", "object": "model", "created": 0, "owned_by": "aihubmix"},
    ...
  ]
}
```

`GET /` 或 `GET /health` 返回运行状态和按供应商统计的请求/错误数，方便观察哪家在报错。

## 在其他工具里配置

统一填这三项，模型名从 `/v1/models` 里挑：

| 项 | 值 |
| --- | --- |
| **Base URL / API Base** | `http://127.0.0.1:8788/v1` |
| **API Key** | `.env.example` 测试的 key，或你启动时 `--key` 指定的密钥（没设就随便填，非空即可） |
| **Model** | 见 `/v1/models` ｜ `gpt-4o-free` 或 `供应商名/模型名`（下文钉定用法） |

### Cursor

`Settings → Models & MCP → 勾选 API Key`，或 `Override OpenAI Base URL` 填 `http://127.0.0.1:8788/v1`，`OpenAI API Key` 填你的网关密钥，再 `Model` 里加一个来自 `/v1/models` 的模型名。开启 "Show more models" 可手动输入自定义模型名。

### Cline (VS Code)

`Settings → API Provider: OpenAI Compatible`：
- Base URL：`http://127.0.0.1:8788/v1`
- API Key：网关密钥（没设随便填）
- Model ID：`gpt-4o-free`（或 `商汤2/glm-5.2`）

### Continue / aider / OpenAI SDK

| 工具 | 配置 |
| --- | --- |
| aider | `aider --openai-api-base http://127.0.0.1:8788/v1 --openai-api-key xxx --model gpt-4o-free` |
| OpenAI Python SDK | `OpenAI(base_url="http://127.0.0.1:8788/v1", api_key="xxx")` |

## 选模型的两个技巧

**1. 钉定供应商。** 同名模型可能多家都有（网关默认轮询分摊）。想指定某一家，用 `供应商名/模型名`：

```
商汤2/glm-5.2        # 只走"商汤2"这一家的 glm-5.2
英伟达/gpt-oss-20b   # 也支持 :: 分隔: 英伟达::gpt-oss-20b
 newcom
```

匹配顺序：先按完整模型名整体命中（所以 `nvidia/xxx` 这类自带斜杠的模型名照常工作），未命中才解析前缀找供应商。

**2. 容灾。** 请求命中多家时，一家返回 `5xx/429` 或连接失败会自动换下一家；全部失败才回 `502`，错误信息里带最后一次的失败原因。

## 支持的路径

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/v1/models` | 列出全部模型（去重），`owned_by` 标出供应商 |
| `POST` | `/v1/chat/completions` | 转发，支持 `stream` 流式 |
| `POST` | `/v1/completions`、`/v1/embeddings` | 原样转发 |
| `GET` | `/`、`/health` | 状态与统计 |

请求体里的 `model` 必填。地址拼接规则与 model-manager 完全一致：末尾补 `/v1`，`base_url` 以 `#` 结尾则原样使用。

## 可视化面板

启动网关后浏览器打开 `http://127.0.0.1:8788/`（或 `http://<网关IP>:8788/`），无需任何依赖就能看到：

- **顶部状态条**：Base URL（点击复制）、是否配密钥、供应商文件路径、验证更新时间
- **统计卡片**：总请求数、错误数、模型数、供应商数
- **模型总表**：每个模型的供应商、状态（可用/不可用/未验证）、耗时、**深度思考**标记、已配置的默认参数；支持按名/供应商搜索，按状态/思考过滤
- **一键验证**：点「▶ 验证全部模型」启动后台批量验证，面板每 5 秒自动刷新进度；验证结果写入 `verify_results.json`，重启网关不丢
- **请求/错误统计**：按供应商聚合

面板是只读的，改配置请编辑下方两个 JSON 文件。

## 批量验证模型可用性

网关自带 `verify.py`，通过网关本身发起最小对话请求（不直连各供应商、不消耗额外额度），逐模型记录是否可用、耗时、是否输出深度思考：

```bash
python verify.py                                   # 默认网关 http://127.0.0.1:8788
python verify.py --gateway http://127.0.0.1:9000       # 指定网关
python verify.py --only qwen,gpt-4                    # 只测包含关键字的模型
python verify.py --workers 6 --timeout 150             # 并发 6、单请求 150 秒超时
```

也可以在面板里点「▶ 验证全部模型」触发，效果一样。

状态含义：`ok` 可用 / `no_choices` 上游返回 200 但无内容（模型名有误或限流）/ `http_error` 上游返回 4xx/5xx / `timeout` 超时 / `dead` 连接失败。

> 实测（当前 providers.json / 19 家供应商 / 97 个模型）：**56 个可用**，25 个无内容，16 个 HTTP 错误；3 个模型在最小请求下就输出了深度思考（`[次-流抗截]gemini-3.1-pro-preview`、`step-explore`、`z-ai/glm-5.3-free`）。

## 深度思考（reasoning / thinking）开关

不同上游对"是否启用深度思考"的开关字段各不相同，网关用 `gateway_config.json` 的 `model_defaults` 来统一：**配置后转发前自动合并进请求体**，客户端（Cursor / Cline 等）无需再关心上游差异；客户端显式传了同名字段时以客户端优先。

### 怎么知道某个模型有没有深度思考

两个信号，都在面板"深度思考"列显示：

1. **自动探测**：`verify.py` 跑最小对话请求，若上游返回了 `reasoning_content` / `reasoning` / `thinking` 字段，或内容里出现 `<think>` 标签，标 `yes`；都没观察到标 `unknown`（注意：有些模型默认关思考，最小请求没触发思考输出也会标 `unknown`，不代表没有该能力）。
2. **手动覆盖**：在 `gateway_config.json` 的 `reasoning_override` 里写 `{"模型名": true/false}`，面板会显示 `yes(manual)` / `no(manual)`。当你从文档/实测知道某模型有能力但自动探测没触发时，用此字段强制标记（只影响显示，不影响实际请求）。

### 怎么配置开关

编辑 `gateway_config.json` 的 `model_defaults`，按模型名指定参数。常见上游的写法：

| 上游/网关风格 | 关思考 | 开思考 |
| --- | --- | --- |
| vLLM / Qwen 兼容（多数 OpenAI 兼容网关） | `{"enable_thinking": false}` | `{"enable_thinking": true}` |
| DeepSeek 风格 | `{"thinking": {"type": "disabled"}}` | `{"thinking": {"type": "enabled"}}` |
| OpenRouter | `{"reasoning": {"enabled": false}}` | `{"reasoning": {"effort": "medium"}}` |
| OpenAI o 系列 | `{"reasoning_effort": "low"}` | `{"reasoning_effort": "high"}` |

示例（已预填了常见会默认开思考的几个模型）：

```json
{
  "model_defaults": {
    "Qwen3.8-Flash-Next": {"enable_thinking": false},
    "Qwen3.8-27B": {"enable_thinking": false},
    "DeepSeek-V4-Pro": {"thinking": {"type": "disabled"}},
    "DeepSeek-V4-Flash": {"thinking": {"type": "disabled"}},
    "kimi-k3": {"enable_thinking": false}
  },
  "reasoning_override": {}
}
```

改完保存，网关下一个请求即生效（热加载），无需重启。

> 注意：`-free` 类的免费模型很多会返回 200 但内容是限流/风控提示（如"只能尝试 10 次"），这被 `verify.py` 标为 `no_choices` 而不是 `ok`，是上游行为，网关只负责透传。

## 注意事项

- **仅监听本机**：默认 `127.0.0.1`，只有本机能连。需要局域网访问才加 `--host 0.0.0.0`，此时**务必配 `--key`**，否则同网段任何人都能用你的额度。
- **密钥明文**：网关直接读 `providers.json`，里面 API Key 是明文（沿用 model-manager 的存储）。`providers.json` 别提交到仓库。
- **上游额度/风控**：某些 `-free` 模型会有次数限制（返回 200 但内容是限流提示），这是上游行为，网关只负责透传。
- **流式超时**：单个上游连接 300 秒读超时。模型卡住不返回时到点会报错并（若还有其他候选）切换。

## 测试

不联网、不动真实额度，用本地 mock 上游验证容灾/解包/流式：

```bash
python mock_upstream.py 18801 18802 &                    # 两个 mock 上游(一个首次请求故意 500)
python gateway.py --providers test_providers.json --port 8790 &
curl http://127.0.0.1:8790/v1/models                     # 列表路由
curl -X POST .../v1/chat/completions -d '{"model":"dup-model",...}'   # 容灾:500→自动切下一家
curl -X POST .../v1/chat/completions -d '{"model":"only-b","stream":true,...}'  # 流式解包
curl http://127.0.0.1:8790/health                        # 按供应商统计
```

`mock_upstream.py` 里 18801 端口返回 `{"data":{"choices":...}}` 包裹格式且第一次请求故意 500，18802 返回标准响应且流式的最后一个 chunk 故意包裹，用来验证网关的解包与故障切换。已验证结果见下方「实测」。

## 实测

- 路由 `/v1/models`、`chat/completions`（含钉定/轮询/容灾）、非流式与流式 `data` 解包 → 通过
- 真实上游端到端：`gpt-4o-free` 经网关 → aihubmix `HTTP 200`（含 SSE 流式 `[DONE]`）→ 通过
- 上游 404/429 原样透传、GBK 编码请求体兜底 → 通过

## 目录结构

```
gateway.py              网关主程序(单文件,纯标准库)
verify.py              批量验证全部模型(通过网关,不碰各供应商 key)
gateway_config.json     模型默认参数 / 思考开关(热加载)
verify_results.json    验证结果产出(面板读取)
mock_upstream.py        测试用 mock 上游(容灾/解包/流式)
test_providers.json     mock 测试用的供应商配置
start.bat               Windows 双击启动
```
