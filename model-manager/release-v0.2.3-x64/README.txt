模型供应商管理 (model-manager) — 绿色版 Release
================================================

【运行方式】
  双击 model-manager.exe 即可运行，无需安装。
  必须与 WebView2Loader.dll 放在同一目录下。

【文件清单】
  model-manager.exe     主程序 (Windows x64)
  WebView2Loader.dll    WebView2 运行时加载器，程序依赖，不可删除

【前置要求】
  - Windows 10 / 11，需已安装 WebView2 Runtime
    （未安装可访问 https://developer.microsoft.com/microsoft-edge/webview2/ 下载，
     或安装任意 Microsoft Edge 浏览器即自带）

【配置说明】
  供应商配置保存在:
    %APPDATA%\com.baozi.model-manager\providers.json
  （API Key 为明文，注意保密）

【版本】v0.2.3  (2026-09-10)
