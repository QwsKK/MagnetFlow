#Requires -Version 5.1
<#
.SYNOPSIS
    把 115Translate 注册成每晚定时运行的计划任务。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\register_task.ps1 -Time 23:00
    powershell -ExecutionPolicy Bypass -File .\scripts\register_task.ps1 -Time 01:30 -Unregister
#>
[CmdletBinding()]
param(
    [string]$Time = "23:00",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$TaskName = "115Translate Nightly"
$Root = Split-Path -Parent $PSScriptRoot
$Bat = Join-Path $Root "scripts\run_nightly.bat"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已删除计划任务：$TaskName" -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $Bat)) {
    Write-Host "找不到 $Bat" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction -Execute $Bat -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

# 勾选「唤醒计算机运行此任务」，睡眠状态下也能起来
$settings.WakeToRun = $true

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "115 网盘视频自动中文字幕" -Force | Out-Null

Write-Host ""
Write-Host "  已注册计划任务：$TaskName" -ForegroundColor Green
Write-Host "  触发时间：每天 $Time" -ForegroundColor Gray
Write-Host "  执行脚本：$Bat" -ForegroundColor Gray
Write-Host ""
Write-Host "  提示：任务会以当前用户身份运行；若希望不登录也能跑，" -ForegroundColor Yellow
Write-Host "        请在「任务计划程序」里改成「不管用户是否登录都要运行」并保存凭据。" -ForegroundColor Yellow
Write-Host ""
