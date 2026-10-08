# build_launcher.ps1 - empacota o LAUNCHER (08/10/2026, ver CLAUDE.md).
# Uso:  powershell -File build_launcher.ps1
#
# Produz dist\Launcher\ (Launcher.exe + dependencias tkinter/stdlib).
# NAO copia assets/maps/certs/config -- isso ja existe em dist\RPG_Online
# (build_client.ps1). release_client.ps1 e quem funde os dois numa pasta
# so pro pacote INICIAL da itch.io; updates automaticos via Launcher
# nunca tocam o proprio Launcher.exe (ver launcher/update_client.py).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "== Build do launcher (PyInstaller onedir) =="
py -3.10 -m PyInstaller release_tools\rpg_online_launcher.spec --noconfirm
if (-not $?) { throw "PyInstaller falhou" }

Write-Host "== Tamanho final =="
$size = (Get-ChildItem "dist\Launcher" -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("dist\Launcher: {0:N1} MB" -f $size)
Write-Host "Pronto."
