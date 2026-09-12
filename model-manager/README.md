# 模型供应商管理 (model-manager)

一个用 **Tauri 2 + 原生 HTML/CSS/JS** 编写的 Windows 桌面小工具,用于管理多个 OpenAI 兼容的模型供应商:录入 Base URL 和 API Key,自动拉取可用模型列表,逐个或批量测试连通性,勾选保存可用的模型。

![Tauri](https://img.shields.io/badge/Tauri-2-blue) ![Rust](https://img.shields.io/badge/Rust-stable-orange) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## 功能

- **供应商列表**:卡片式展示,每个供应商显示名称、Base URL、已保存的模型,支持编辑、删除、启用/禁用(默认只显示启用的)
- **新增/编辑供应商**:右上角 ＋ 号进入;填好名称、Base URL、API Key 后回车(或点按钮)自动拉取模型列表
- **连通性测试**:每个模型可单独测试,也可「测试全部」;测试实际调用 `/chat/completions` 发起一次最小对话请求,验证完整链路(鉴权 + 推理),并显示耗时
- **勾选保存**:勾选可用的模型后点「保存」;支持「一键勾选测试通过」
- **只看通过**:测试完可切换只显示通过的模型
- **复制配置**:卡片上可一键复制整份供应商配置(含 API Key)JSON 到剪贴板,方便备份迁移
- 供应商配置保存在 `%APPDATA%\com.baozi.model-manager\providers.json`(API Key 为明文,注意保密)

## 地址拼接规则

- 填 `https://api.openai.com` → 自动补为 `https://api.openai.com/v1/...`
- 填到 `/v1` 结尾 → 直接使用
- 以 `#` 结尾 → 按输入原样使用(兼容路径不规范的网关)

## 兼容性

- 响应兼容 `{"choices": [...]}` 与部分网关把内容包在 `{"data": {"choices": [...]}}` 两种格式
- 模型列表兼容 `{"data": [...]}` 与 `{"models": [...]}` 两种返回
- 错误信息优先提取 OpenAI 风格的 `error.message`

## 开发

```bash
npm install          # 安装 @tauri-apps/cli
npx tauri dev        # 开发调试
npx tauri build      # 构建 release(产物在 src-tauri/target/release/)
```

要求:Rust stable、Node.js 18+、Windows 10/11(自带 WebView2)。

## 目录结构

```
ui/                 前端(原生 HTML/CSS/JS,无框架)
src-tauri/          Rust 后端(Tauri 2)
  src/main.rs       fetch_models / test_model / load_providers / save_providers 四个命令
assets/             图标源文件
make_icon.py        图标生成脚本(纯 Python 画 PNG,无第三方依赖)
```

## License

MIT
