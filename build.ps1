$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if (Test-Path dist) { Remove-Item -Recurse -Force dist }
if (Test-Path build) { Remove-Item -Recurse -Force build }

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name ClaudeBeep `
  --icon assets/icon.ico `
  --add-data "static;static" `
  --add-data "assets;assets" `
  --hidden-import websockets `
  --hidden-import lark_oapi `
  --hidden-import lark_oapi.ws `
  --hidden-import dingtalk_stream `
  tray.py

Write-Host "Built dist\ClaudeBeep.exe"
