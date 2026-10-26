# 采集器快捷入口。优先用合并后项目根目录的 .venv。
# 用法: .\run.ps1 --site mysite --pages 1 1 --dry-run
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "D:\DevHub\python\python.exe" }
if (-not (Test-Path $py)) {
    Write-Host "找不到 Python：先创建 venv（py -3.12 -m venv ..\.venv）" -ForegroundColor Red
    exit 1
}
& $py (Join-Path $PSScriptRoot "harvest.py") @args
exit $LASTEXITCODE
