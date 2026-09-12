# 构建 model-manager 绿色版：编译 -> 输出绿色版目录 -> 创建桌面快捷方式
param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDir = Join-Path $root 'src-tauri\target\release'
$outDir = Join-Path $root 'release'   # 固定目录名，桌面快捷方式始终指向这里

if (-not $SkipBuild) {
    Write-Host '[1/3] npx tauri build ...'
    Push-Location $root
    npx tauri build
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'tauri build failed' }
    Pop-Location
} else {
    Write-Host '[1/3] skip build (-SkipBuild)'
}

Write-Host '[2/3] 更新绿色版目录 release\ ...'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Copy-Item (Join-Path $srcDir 'model-manager.exe') $outDir -Force
Copy-Item (Join-Path $srcDir 'WebView2Loader.dll') $outDir -Force
$readme = Join-Path $root 'release-v0.2.3-x64\README.txt'
if (Test-Path $readme) { Copy-Item $readme $outDir -Force }

Write-Host '[3/3] 创建桌面快捷方式 ...'
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop '模型供应商管理.lnk'
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = Join-Path $outDir 'model-manager.exe'
$s.WorkingDirectory = $outDir
$s.Save()

Write-Host "完成: $outDir"
Write-Host "桌面快捷方式: $lnk"
