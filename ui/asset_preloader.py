# asset_preloader.py
"""
Pré-carregamento de TODOS os assets do jogo (28/09/2026, pedido do
usuário) — varre `assets/` inteiro e decodifica cada imagem/som pelo
cache único (`ui/asset_cache.py`) UMA vez, antes do jogo abrir de
verdade (boot) ou antes de uma troca de mapa/instância ser mostrada.

Sem isso, cada sprite/som era carregado do disco na primeira vez que
aparecia em jogo (bug real medido nesta sessão: folha de sprite nunca
vista custando 96-320ms num frame só, ao entrar numa área nova do
mapa ou numa instância) — o jogador sentia isso como engasgo/
travamento no meio do jogo, sem aviso.

`preload_all()` é DELIBERADAMENTE bruta (carrega TUDO que existe em
`assets/`, não só o que a área atual precisa) — decisão do usuário:
o catálogo total do jogo é pequeno (~45MB, ~600ms de imagem + ~2.5s de
som, medidos nesta sessão) e cabe folgado dentro do tempo de loading
já existente, então não vale a complexidade de descobrir "o que esta
área específica usa" (streaming por-mapa, técnica de MMO grande — ver
PROBLEMAS_ARQUITETURA.md §215 pra a pesquisa que justificou essa
escolha)."""
from __future__ import annotations
import os

_IMAGE_EXTS = (".png",)
_SOUND_EXTS = (".ogg", ".wav", ".mp3")


def iter_asset_files(base_dir: str) -> "list[tuple[str, str]]":
    """Lista (caminho, tipo) de todo arquivo de imagem/som sob
    `base_dir`, recursivo. `tipo` é "image" ou "sound". Função pura
    (sem I/O de carregamento) — separada de `preload_all` pra poder
    contar o total ANTES de começar a carregar (barra de progresso)."""
    out: "list[tuple[str, str]]" = []
    for root, _dirs, files in os.walk(base_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            path = os.path.join(root, fname)
            if ext in _IMAGE_EXTS:
                out.append((path, "image"))
            elif ext in _SOUND_EXTS:
                out.append((path, "sound"))
    return out


def preload_all(progress_cb=None, step: int = 6) -> dict:
    """Carrega toda imagem/som de `assets/` pro cache compartilhado.

    `progress_cb(done, total)`, se passado, é chamado a cada `step`
    arquivos (nunca a cada 1 — o call site típico é redesenhar a tela
    de loading, custo real por chamada; `step` evita isso rodar
    centenas de vezes à toa) e sempre no último arquivo.

    Retorna um resumo `{"total", "images", "sounds", "failed"}` —
    "failed" é a lista de caminhos que existem mas falharam ao
    carregar (arquivo corrompido, formato inesperado) — o jogo
    continua funcionando pra esses (fallback de sempre: retângulo/
    silêncio), só reporta pra diagnóstico."""
    from ui.asset_cache import get_image, get_sound
    from paths import resource_path

    base_dir = resource_path("assets")
    files = iter_asset_files(base_dir) if os.path.isdir(base_dir) else []
    total = len(files)
    images = sounds = 0
    failed: "list[str]" = []

    for i, (path, kind) in enumerate(files):
        if kind == "image":
            ok = get_image(path) is not None
            images += ok
        else:
            ok = get_sound(path) is not None
            sounds += ok
        if not ok:
            failed.append(path)
        if progress_cb and (i % step == 0 or i == total - 1):
            progress_cb(i + 1, total)

    return {"total": total, "images": images, "sounds": sounds, "failed": failed}
