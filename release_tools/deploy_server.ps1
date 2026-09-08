# Deploy da arvore commitada (HEAD) pro servidor Hetzner + restart do rpg-server.
# So envia o que ja esta commitado no git -- nunca arquivos locais nao commitados
# (config.json/data/game.db/maps/*.csv sao sempre WIP do usuario, nunca vao junto).

param(
    [string]$ServerHost = "5.161.104.174",
    [string]$SshKey = "$HOME/.ssh/rpg_hetzner"
)

$ErrorActionPreference = "Stop"

# Pipe binario direto pro ssh via PowerShell corrompe o tar (PowerShell trata
# o pipeline como texto) -- por isso passa por um arquivo temporario + scp.
$TmpTar = New-TemporaryFile
Remove-Item $TmpTar
$TmpTar = "$TmpTar.tar"

Write-Host "Enviando HEAD ($(git rev-parse --short HEAD)) para $ServerHost ..."
git archive HEAD --format=tar --output=$TmpTar
if ($LASTEXITCODE -ne 0) { Write-Host "git archive falhou." -ForegroundColor Red; exit 1 }

scp -i $SshKey $TmpTar "root@${ServerHost}:/tmp/deploy.tar"
if ($LASTEXITCODE -ne 0) { Remove-Item $TmpTar; Write-Host "scp falhou." -ForegroundColor Red; exit 1 }

Remove-Item $TmpTar

ssh -i $SshKey "root@$ServerHost" "tar -x -f /tmp/deploy.tar -C /opt/rpg_ecs_online && rm /tmp/deploy.tar && chown -R rpgapp:rpgapp /opt/rpg_ecs_online && systemctl restart rpg-server && sleep 2 && systemctl is-active rpg-server"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Deploy falhou -- ver output acima." -ForegroundColor Red
    exit 1
}

Write-Host "Deploy concluido, servidor reiniciado." -ForegroundColor Green
