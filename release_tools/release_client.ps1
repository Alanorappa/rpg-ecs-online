# release_client.ps1 - builda cliente+launcher, publica o zip de auto-
# atualizacao no servidor (08/10/2026, ver CLAUDE.md). Resolve o pedido
# original do usuario de nao precisar de varios comandos separados pra
# publicar uma versao nova pros testers.
#
# Uso:  powershell -File release_tools\release_client.ps1
#       powershell -File release_tools\release_client.ps1 -ServerHost 203.0.113.10
#
# NAO reinicia o servidor do jogo (deploy_server.ps1 continua separado
# -- publicar uma versao nova de CLIENTE e independente de reiniciar o
# jogo pra quem ja esta conectado).
param(
    [string]$ServerHost = "5.161.104.174",
    [string]$SshKey = "$HOME/.ssh/rpg_hetzner",
    [int]$ServerPort = 8765
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "== 1/5: build do cliente =="
& (Join-Path $PSScriptRoot "build_client.ps1") -ServerHost $ServerHost -ServerPort $ServerPort
if (-not $?) { throw "build_client.ps1 falhou" }

Write-Host "== 2/5: build do launcher =="
& (Join-Path $PSScriptRoot "build_launcher.ps1")
if (-not $?) { throw "build_launcher.ps1 falhou" }

Write-Host "== 3/5: zipando dist\RPG_Online (payload de auto-atualizacao) =="
# O zip de auto-atualizacao e SO o jogo -- NUNCA Launcher.exe (ver
# launcher/update_client.py::apply_update -- e isso que evita o launcher
# precisar se autossubstituir no meio da atualizacao).
$zipPath = "dist\RPG_Online.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "dist\RPG_Online\*" -DestinationPath $zipPath -CompressionLevel Optimal

$sha256 = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLower()
$sha256 | Out-File -FilePath "dist\RPG_Online.sha256" -Encoding ascii -NoNewline
Write-Host "sha256: $sha256"

Write-Host "== 4/5: publicando no servidor (releases/, sem reiniciar o jogo) =="
ssh -i $SshKey "root@$ServerHost" "mkdir -p /opt/rpg_ecs_online/releases"
scp -i $SshKey "dist\RPG_Online.zip" "root@${ServerHost}:/opt/rpg_ecs_online/releases/RPG_Online.zip"
if (-not $?) { throw "scp do zip falhou" }
scp -i $SshKey "dist\RPG_Online.sha256" "root@${ServerHost}:/opt/rpg_ecs_online/releases/RPG_Online.sha256"
if (-not $?) { throw "scp do sha256 falhou" }

Write-Host "== 5/5: pacote INICIAL da itch.io (Launcher + jogo juntos) =="
# So precisa ser reenviado pra itch.io quando o PROPRIO Launcher.exe
# mudar (raro) -- testers ja instalados atualizam via auto-update sem
# precisar disso de novo.
$fullDir = "dist\RPG_Online_FullPackage"
if (Test-Path $fullDir) { Remove-Item $fullDir -Recurse -Force }
robocopy "dist\RPG_Online" $fullDir /E /NFL /NDL /NJH /NJS | Out-Null
robocopy "dist\Launcher" $fullDir /E /NFL /NDL /NJH /NJS | Out-Null

Write-Host ""
Write-Host "Pronto."
Write-Host "  - Auto-atualizacao publicada: /opt/rpg_ecs_online/releases/ (servidor $ServerHost)"
Write-Host "  - Pacote pra subir na itch.io (so se o Launcher mudou): $fullDir"
