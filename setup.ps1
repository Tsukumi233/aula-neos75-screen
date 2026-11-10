# Neos75 屏幕：一次性安装 / 修复（可以反复运行，已经装好的步骤会跳过）。
#   右键 → 使用 PowerShell 运行，或：powershell -ExecutionPolicy Bypass -File setup.ps1
# 只在本仓库里安装（sdk\.venv、app\node_modules、design\official、design\vendor），不改系统设置，不刷固件。
param(
    [switch]$SkipApp,       # 不装桌面应用（只用命令行 / 智能体）
    [switch]$SkipDevice     # 不检查键盘屏幕
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root 'sdk\.venv\Scripts\python.exe'
$failed = @()

function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "   好  $t" -ForegroundColor Green }
function Warn($t) { Write-Host "   注意  $t" -ForegroundColor Yellow }
function Run($exe, [string[]]$a) {
    & $exe @a
    if ($LASTEXITCODE -ne 0) { throw "$exe $($a -join ' ') 失败（退出码 $LASTEXITCODE）" }
}

Step 'Python 环境（sdk\.venv）'
try {
    if (-not (Test-Path $py)) {
        $base = $null
        foreach ($v in '3.11', '3.12', '3.13') {
            try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $base = @('py', "-$v"); break } } catch {}
        }
        if (-not $base) { $base = @('python') }
        $exe = $base[0]; $rest = @(); if ($base.Count -gt 1) { $rest = $base[1..($base.Count - 1)] }
        Run $exe ($rest + @('-m', 'venv', (Join-Path $root 'sdk\.venv')))
        Ok '已创建 sdk\.venv'
    } else { Ok 'sdk\.venv 已存在' }
    Run $py @('-m', 'pip', 'install', '-q', '--upgrade', 'pip')
    Run $py @('-m', 'pip', 'install', '-q', '-e', ((Join-Path $root 'sdk') + '[widgets,check,test]'))
    Ok 'neos75screen SDK、小组件依赖（psutil、媒体信息、音频电平与频谱）、页面检查用的 Playwright 已安装'
} catch { Warn $_; $failed += 'Python' }

Step '浏览器（渲染小组件用）'
try {
    $chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
                "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
                "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($chrome) { Ok "使用系统 Chrome：$chrome" }
    else {
        Run $py @('-m', 'playwright', 'install', 'chromium')
        Ok '没有系统 Chrome，已安装 Playwright 自带的 Chromium'
    }
} catch { Warn $_; $failed += '浏览器' }

Step '官方屏幕 UI 素材（design\official，个人使用，不进 git）'
try {
    if (Test-Path (Join-Path $root 'design\official\jpg\page1_bg.jpg')) { Ok '已存在' }
    else { Run $py @((Join-Path $root 'tools\ui_pack.py')); Ok '已从 Aula Hub 下载并解包' }
} catch { Warn "$_（页面会缺背景图；联网后重跑本脚本）"; $failed += 'UI 素材' }

if (-not $SkipApp) {
    Step '桌面应用（app，Electron）'
    try {
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $npm) { throw '没有找到 Node.js / npm：请先安装 Node.js LTS（https://nodejs.org），再重跑本脚本' }
        Push-Location (Join-Path $root 'app')
        try { Run $npm.Source @('install', '--no-fund', '--no-audit') } finally { Pop-Location }
        # Electron 45 fetches its binary in app's postinstall (node node_modules/electron/install.js)
        $electron = Join-Path $root 'app\node_modules\electron\dist\electron.exe'
        if (-not (Test-Path $electron)) {
            throw "Electron 程序没有下载成功（$electron 不存在）：检查网络后重跑本脚本"
        }
        Ok '依赖已安装（含 Electron）'
        $fonts = Join-Path $root 'app\node_modules\misans\lib\Normal'
        $dest = Join-Path $root 'design\vendor\misans\Normal'
        if ((Test-Path $fonts) -and -not (Test-Path (Join-Path $dest 'MiSans-Light.min.css'))) {
            New-Item -ItemType Directory -Force $dest | Out-Null
            Copy-Item (Join-Path $fonts '*') $dest -Recurse -Force
            Ok 'MiSans 字体已复制到 design\vendor（页面离线也能正确显示）'
        } elseif (Test-Path $dest) { Ok 'MiSans 本地字体已存在' }
    } catch { Warn $_; $failed += '桌面应用' }
}

if (-not $SkipDevice) {
    Step '键盘屏幕（只读检查）'
    try {
        $out = & $py -m neos75screen.cli hello 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw $out.Trim() }
        Write-Host ($out.Trim() -replace '(?m)^', '   ')
        if ($out -match 'stock firmware') {
            Warn '屏幕是原厂固件：实时页面需要补丁固件，见 README.md「固件」一节（刷写需要你确认）'
        } elseif ($out -match 'flags 0x([0-9A-F]{2})') {
            $flags = [Convert]::ToInt32($Matches[1], 16)
            if (($flags -band 0x10) -eq 0) { Warn '屏幕固件较旧：可用，但没有页缓存 / 5 ms 节拍（换页更慢）。见 README.md「固件」' }
            else { Ok '补丁固件功能齐全（页缓存、区域帧、主机分页、键盘状态）' }
        }
    } catch { Warn "没有连上键盘屏幕：$_（插上键盘后可以忽略，应用会自动连接）" }
}

Step '完成'
if ($failed.Count) { Warn ("这些步骤没有成功：" + ($failed -join '、') + "。处理后重跑本脚本即可。") ; exit 1 }
Ok '双击 Neos75.cmd 启动；命令行：sdk\.venv\Scripts\neos75 pages --list'
