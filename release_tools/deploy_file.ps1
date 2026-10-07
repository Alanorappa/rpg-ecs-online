# Deploy de UM arquivo local pro servidor Hetzner + restart do rpg-server.
# Uso: .\release_tools\deploy_file.ps1 -Path maps\map_1_entities.json
# Util pra iterar rapido (ex: ajustar posicao de spawn num JSON de mapa) sem
# precisar commitar antes de cada teste -- o proximo deploy_server.ps1 (git
# archive HEAD) sobrescreve este arquivo de qualquer jeito, entao isto nunca
# diverge de verdade do estado commitado a longo prazo.

param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$ServerHost = "5.161.104.174",
    [string]$SshKey = "$HOME/.ssh/rpg_hetzner"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Path)) {
    Write-Host "Arquivo nao encontrado: $Path" -ForegroundColor Red
    exit 1
}

# Caminho relativo com barras forward (tanto pro display quanto pro destino remoto).
$RelPath = (Resolve-Path $Path).Path.Substring((Resolve-Path .).Path.Length + 1) -replace '\\', '/'
$RemotePath = "/opt/rpg_ecs_online/$RelPath"
$RemoteDir  = $RemotePath.Substring(0, $RemotePath.LastIndexOf('/'))

Write-Host "Enviando $RelPath para ${ServerHost}:$RemotePath ..."

ssh -i $SshKey "root@$ServerHost" "mkdir -p $RemoteDir"
if ($LASTEXITCODE -ne 0) { Write-Host "mkdir remoto falhou." -ForegroundColor Red; exit 1 }

scp -i $SshKey $Path "root@${ServerHost}:$RemotePath"
if ($LASTEXITCODE -ne 0) { Write-Host "scp falhou." -ForegroundColor Red; exit 1 }

ssh -i $SshKey "root@$ServerHost" "chown rpgapp:rpgapp $RemotePath && systemctl restart rpg-server && sleep 2 && systemctl is-active rpg-server"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Deploy falhou -- ver output acima." -ForegroundColor Red
    exit 1
}

Write-Host "Deploy concluido (1 arquivo), servidor reiniciado." -ForegroundColor Green
