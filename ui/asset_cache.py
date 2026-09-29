# asset_cache.py
"""
Cache único de imagem/som por caminho de arquivo (28/09/2026).

Ponto único de verdade: todo lazy-load de PNG/som no cliente passa por
`get_image`/`get_sound`, nunca chama `pygame.image.load`/
`pygame.mixer.Sound` direto. Sem isso, `ui/asset_preloader.py` (tela de
loading) decodificaria cada arquivo uma vez e o resultado seria jogado
fora — cada gerenciador (TileSpriteManager, IconManager, SoundManager,
character_animation, entity_disguise, ...) tinha seu PRÓPRIO dict de
cache, nunca compartilhado entre si, então a chamada "de verdade" (a
que acontece quando o objeto aparece em jogo) decodificava o arquivo
de novo do zero, sem nenhum ganho do pré-carregamento.

Guarda a Surface/Sound RAW (sem escala/corte/composição) — cada
gerenciador continua responsável pela própria transformação (escala,
sub-surface, atlas, etc.) e pelo próprio cache dessas derivadas, exatamente
como já fazia antes desta mudança. Isso aqui só evita decodificar o
MESMO arquivo do disco mais de uma vez.
"""
from __future__ import annotations
import pygame

_images: "dict[str, pygame.Surface | None]" = {}
_sounds: "dict[str, pygame.mixer.Sound | None]" = {}


def get_image(path: str) -> "pygame.Surface | None":
    """Surface RAW (`convert_alpha()`), cacheada por caminho. `None` se
    o arquivo não existir ou falhar ao carregar (mesmo contrato que
    cada chamador já tinha antes — nunca lança, só loga a critério de
    quem chama)."""
    if path in _images:
        return _images[path]
    try:
        surf = pygame.image.load(path).convert_alpha()
    except Exception:
        surf = None
    _images[path] = surf
    return surf


def get_sound(path: str) -> "pygame.mixer.Sound | None":
    """`pygame.mixer.Sound` cacheado por caminho. `None` se o arquivo
    não existir ou falhar ao carregar."""
    if path in _sounds:
        return _sounds[path]
    try:
        snd = pygame.mixer.Sound(path)
    except Exception:
        snd = None
    _sounds[path] = snd
    return snd


def cached_image_count() -> int:
    """Só pra diagnóstico/teste — nunca usado em lógica de jogo."""
    return len(_images)


def cached_sound_count() -> int:
    """Só pra diagnóstico/teste — nunca usado em lógica de jogo."""
    return len(_sounds)
