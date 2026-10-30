# 啟動腫瘤衛教機器人（本機開發用）
# 用法：在檔案總管對這個檔案按右鍵 → 用 PowerShell 執行；或終端機執行 .\start.ps1
# 啟動後開瀏覽器 http://localhost:8000，用 P001 / patient123 登入

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 找 Python（優先用安裝好的 3.12，找不到再退回 PATH 的 python）
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# 本機遊玩用：植入 P001/R001/A001 示範帳號。正式環境不要設這個（預設關閉）。
$env:DEMO_SEED = "true"

Set-Location "$PSScriptRoot\backend"
Write-Host "啟動中… 開啟瀏覽器 http://localhost:8000（Ctrl+C 可停止）" -ForegroundColor Cyan
Write-Host "測試帳號：病患 P001 / patient123　研究者 R001 / research123" -ForegroundColor DarkGray
& $py -X utf8 -m uvicorn main:app --port 8000 --host 127.0.0.1
