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
#
# Resultado final: UMA pasta so, dist\RPG_Online, com o jogo E o
# Launcher juntos -- e o que voce zipa pra subir na itch.io, igual
# sempre foi (08/10/2026, pedido do usuario -- a 1a versao deste script
# deixava Launcher/FullPackage em pastas separadas, confuso de
# distribuir). O pulo do gato: o zip de AUTO-atualizacao (que nunca
# pode conter Launcher.exe) e gerado ANTES do Launcher ser copiado pra
# dentro de dist\RPG_Online -- a ordem das etapas abaixo e o que
# garante isso, nao mexer nela.
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

Write-Host "== 3/5: zipando payload de auto-atualizacao (SEM o Launcher) =="
# Captura o conteudo de dist\RPG_Online AGORA, antes do Launcher ser
# copiado pra dentro dela no passo 5 -- e assim que o zip de auto-
# atualizacao nunca inclui Launcher.exe (ver launcher/update_client.py::
# apply_update -- e isso que evita o launcher precisar se
# autossubstituir no meio de uma atualizacao).
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

Write-Host "== 5/5: juntando o Launcher dentro de dist\RPG_Online =="
# SO agora o Launcher entra na pasta final -- o zip de atualizacao
# automatica do passo 3 ja foi gerado sem ele. Resultado: 1 pasta so
# (dist\RPG_Online), pronta pra zipar e subir na itch.io, igual sempre
# foi o fluxo. dist\Launcher (saida bruta do PyInstaller) e descartada
# depois de copiada -- nao fica pasta solta nenhuma em dist\.
robocopy "dist\Launcher" "dist\RPG_Online" /E /NFL /NDL /NJH /NJS | Out-Null
Remove-Item "dist\Launcher" -Recurse -Force

Write-Host ""
Write-Host "Pronto."
Write-Host "  - Auto-atualizacao publicada: /opt/rpg_ecs_online/releases/ (servidor $ServerHost)"
Write-Host "  - Pasta pronta pra distribuir (jogo + Launcher juntos): dist\RPG_Online"
