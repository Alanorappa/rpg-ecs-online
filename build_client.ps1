# build_client.ps1 - empacota o CLIENTE para distribuicao (alpha).
# Uso:  powershell -ExecutionPolicy Bypass -File build_client.ps1
#       powershell -File build_client.ps1 -ServerHost 203.0.113.10   (forca um IP)
#       powershell -File build_client.ps1 -ServerHost localhost      (build pra uso local)
# Sem -ServerHost: descobre o IP publico atual (api.ipify.org) e grava no
# config.json do pacote - o testador extrai e joga, sem editar nada.
# NOTA: manter este arquivo 100% ASCII - PowerShell 5.1 le .ps1 sem BOM como
# ANSI e acentos/travessoes UTF-8 viram bytes que quebram o parser.
param(
    [string]$ServerHost = "",
    [int]$ServerPort = 8765
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $ServerHost) {
    Write-Host "== Descobrindo IP publico (api.ipify.org) =="
    try {
        $ServerHost = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 10).Trim()
        Write-Host "IP publico: $ServerHost"
    } catch {
        Write-Warning "Nao consegui consultar o IP publico - usando 'localhost'. Use -ServerHost pra definir."
        $ServerHost = "localhost"
    }
}

Write-Host "== Build do cliente (PyInstaller onedir) =="
py -3.10 -m PyInstaller rpg_online_client.spec --noconfirm
if (-not $?) { throw "PyInstaller falhou" }

$dist = Join-Path $PSScriptRoot "dist\RPG_Online"

Write-Host "== Copiando assets/ e maps/ para $dist =="
robocopy "assets" (Join-Path $dist "assets") /E /NFL /NDL /NJH /NJS | Out-Null
# maps: so os mapas ATIVOS (map_1 + cavernas + transicoes). Excluidos:
#   map_main_*/map_worm_cave_* - conteudo nao referenciado por nenhum codigo (~42 MB)
#   *.png - fontes da ferramenta dev png_to_map, nao carregados pelo jogo
robocopy "maps" (Join-Path $dist "maps") /E /NFL /NDL /NJH /NJS `
    /XF "map_main*" "map_worm_cave*" "main_*.png" "*.png" | Out-Null

Write-Host "== Gravando config.json com server_host=$ServerHost =="
# Arquivo parcial de proposito: config.load() mescla com os DEFAULTS em
# runtime, entao so o que difere do default precisa estar aqui.
@{ server_host = $ServerHost; server_port = $ServerPort } |
    ConvertTo-Json | Out-File -FilePath (Join-Path $dist "config.json") -Encoding ascii

Write-Host "== Gerando LEIA-ME.txt =="
@"
RPG ECS Online - ALPHA de testes
================================

COMO JOGAR
1. Abra RPG_Online.exe - o servidor ja vem configurado ($ServerHost).
2. Crie sua conta na tela de login (usuario + senha) e seu personagem.
(Se o organizador avisar que o endereco mudou: edite "server_host" no
config.json desta pasta.)

CONTROLES BASICOS
- WASD / clique direito: mover      - Clique esquerdo: selecionar alvo
- 1..0: skills da hotbar            - Espaco: atacar inimigo mais proximo
- I inventario | T talentos | M mapa | J diario de quests | H habilidades
- L painel de skill level

ENCONTROU BUG?
- Descreva o que fazia quando aconteceu (skill usada, mapa, quantos players).
- Se o jogo fechar sozinho, anexe o arquivo crash.log desta pasta.
- Prints ajudam muito.

Este e um prototipo em desenvolvimento - progresso pode ser apagado
entre versoes do alpha.
"@ | Out-File -FilePath (Join-Path $dist "LEIA-ME.txt") -Encoding utf8

Write-Host "== Tamanho final =="
$size = (Get-ChildItem $dist -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("dist\RPG_Online: {0:N1} MB" -f $size)
Write-Host "Pronto. Distribua a pasta dist\RPG_Online (zip) - config.json ja aponta pra $ServerHost."
