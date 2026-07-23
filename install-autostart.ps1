<#
    开机自动在后台运行局域网服务（无控制台窗口、不弹浏览器）。

    需要用【管理员】PowerShell 运行：
        powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1

    默认：当前用户登录时启动（不需要密码，打印机权限最稳）。
    加 -AtStartup：开机即启动，以 SYSTEM 身份跑，不需要有人登录。

    卸载：.\uninstall-autostart.ps1
#>
[CmdletBinding()]
param(
    [switch]$AtStartup,          # 开机启动（SYSTEM），而不是登录时启动
    [int]$Port = 5000,
    [switch]$NoFirewall          # 不动防火墙
)

$ErrorActionPreference = 'Stop'
$TaskName = 'SKU Barcode Printer'
$RuleName = 'SKU Barcode Printer'
$Dir = $PSScriptRoot

function Fail($msg) { Write-Host "[错误] $msg" -ForegroundColor Red; exit 1 }
function Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "       $msg" -ForegroundColor DarkGray }

# --- 前置检查 -------------------------------------------------------------
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "请用【管理员】PowerShell 运行：`n       powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
}

$Pythonw = Join-Path $Dir '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Pythonw)) {
    Write-Host "[..]   还没建虚拟环境，先跑一次 uv sync..." -ForegroundColor Yellow
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        $uv = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter uv.exe -ErrorAction SilentlyContinue |
              Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $uv) { Fail "找不到 uv，请先运行: winget install --id astral-sh.uv -e" }
    Push-Location $Dir; & $uv sync; Pop-Location
    if (-not (Test-Path $Pythonw)) { Fail "uv sync 之后仍找不到 $Pythonw" }
}
Ok "解释器: $Pythonw"

# --- 计划任务 -------------------------------------------------------------
$action = New-ScheduledTaskAction -Execute $Pythonw `
    -Argument 'app.py --lan --no-browser' -WorkingDirectory $Dir

if ($AtStartup) {
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $mode = '开机启动（SYSTEM，不需要有人登录）'
} else {
    $me        = "$env:USERDOMAIN\$env:USERNAME"
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $me
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    $mode = "登录时启动（$me）"
}

# 网络起来得慢，延迟 30 秒再启；崩了自动重启；不限运行时长
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description 'SKU 条码打印，局域网后台服务' -Force | Out-Null
Ok "已注册计划任务「$TaskName」 —— $mode"

# --- 防火墙 ---------------------------------------------------------------
if ($NoFirewall) {
    Info "按要求跳过防火墙设置（同事可能连不上）"
} elseif (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    Ok "防火墙规则已存在，跳过"
} else {
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP `
        -LocalPort $Port -Action Allow -Profile Any | Out-Null
    Ok "已放行入站 TCP $Port"
}

# --- 立即启动并自检 -------------------------------------------------------
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-ScheduledTask -TaskName $TaskName

$live = $false
foreach ($i in 1..20) {
    Start-Sleep -Milliseconds 700
    try { Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2 | Out-Null; $live = $true; break } catch { }
}

Write-Host ''
if ($live) {
    Ok "服务已在后台运行"
    $ips = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notlike '198.18.*' }
    Write-Host ''
    Write-Host '  同事在浏览器里打开：' -ForegroundColor Cyan
    foreach ($ip in $ips) { Write-Host ("      http://{0}:{1}    ({2})" -f $ip.IPAddress, $Port, $ip.InterfaceAlias) -ForegroundColor Cyan }
    Write-Host ''
    Info '重启后会自动拉起，不会有窗口弹出。'
    Info "改配置后重启服务： Restart-ScheduledTask -TaskName '$TaskName'"
    Info "卸载：             .\uninstall-autostart.ps1"
} else {
    Fail "任务已注册但端口 $Port 起不来。看一下任务计划程序里「$TaskName」的上次运行结果。"
}
