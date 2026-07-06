# build_client.ps1 — empacota o CLIENTE para distribuição (alpha).
# Uso:  powershell -ExecutionPolicy Bypass -File build_client.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== Build do cliente (PyInstaller onedir) =="
py -3.10 -m PyInstaller rpg_online_client.spec --noconfirm
if (-not $?) { throw "PyInstaller falhou" }

$dist = Join-Path $PSScriptRoot "dist\RPG_Online"

Write-Host "== Copiando assets/ e maps/ para $dist =="
robocopy "assets" (Join-Path $dist "assets") /E /NFL /NDL /NJH /NJS | Out-Null
# maps: só os mapas ATIVOS (map_1 + cavernas + transições). Excluídos:
#   map_main_*/map_worm_cave_* — conteúdo não referenciado por nenhum código (~42 MB)
#   *.png — fontes da ferramenta dev png_to_map, não carregados pelo jogo
robocopy "maps" (Join-Path $dist "maps") /E /NFL /NDL /NJH /NJS `
    /XF "map_main*" "map_worm_cave*" "main_*.png" "*.png" | Out-Null

Write-Host "== Gerando LEIA-ME.txt =="
@'
RPG ECS Online — ALPHA de testes
================================

COMO JOGAR
1. Abra o config.json (nesta pasta) e troque "server_host" pelo endereco
   que voce recebeu do organizador (ex: "server_host": "203.0.113.10").
   Se o arquivo nao existir, rode o jogo uma vez — ele sera criado.
2. Abra RPG_Online.exe.
3. Crie sua conta na tela de login (usuario + senha) e seu personagem.

CONTROLES BASICOS
- WASD / clique direito: mover      - Clique esquerdo: selecionar alvo
- 1..0: skills da hotbar            - Espaco: atacar inimigo mais proximo
- I inventario | T talentos | M mapa | J diario de quests | H habilidades
- L painel de skill level

ENCONTROU BUG?
- Descreva o que fazia quando aconteceu (skill usada, mapa, quantos players).
- Se o jogo fechar sozinho, anexe o arquivo crash.log desta pasta.
- Prints ajudam muito.

Este e um prototipo em desenvolvimento — progresso pode ser apagado
entre versoes do alpha.
'@ | Out-File -FilePath (Join-Path $dist "LEIA-ME.txt") -Encoding utf8

Write-Host "== Tamanho final =="
$size = (Get-ChildItem $dist -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("dist\RPG_Online: {0:N1} MB" -f $size)
Write-Host "Pronto. Distribua a pasta dist\RPG_Online (zip). config.json aparece ao lado do exe no primeiro run."
