<#
    卸载开机自启。需要【管理员】PowerShell：
        powershell -ExecutionPolicy Bypass -File .\uninstall-autostart.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 5000,
    [switch]$KeepFirewall
)

$ErrorActionPreference = 'Stop'
$TaskName = 'SKU Barcode Printer'
$RuleName = 'SKU Barcode Printer'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[错误] 请用【管理员】PowerShell 运行" -ForegroundColor Red
    exit 1
}

Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Write-Host "[OK]   已停止正在运行的服务" -ForegroundColor Green

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[OK]   已删除计划任务「$TaskName」" -ForegroundColor Green
} else {
    Write-Host "       计划任务不存在，跳过" -ForegroundColor DarkGray
}

if ($KeepFirewall) {
    Write-Host "       按要求保留防火墙规则" -ForegroundColor DarkGray
} elseif (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $RuleName
    Write-Host "[OK]   已删除防火墙规则（端口 $Port 重新关闭）" -ForegroundColor Green
} else {
    Write-Host "       防火墙规则不存在，跳过" -ForegroundColor DarkGray
}
