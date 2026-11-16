$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot 'sydney_business_leads')
$env:PYTHONUTF8 = '1'
python web_app.py
