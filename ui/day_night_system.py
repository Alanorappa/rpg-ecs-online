# day_night_system.py
"""
DayNightSystem — ciclo dia/noite cosmético (21/09/2026, pedido do
usuário). 100% client-only: relógio determinístico (função do tempo
real), sem mensagem de rede nova, sem o servidor saber de nada — todo
cliente calcula a MESMA hora sozinho. Só mundo aberto tem dia/noite —
BG/Arena ficam sempre num horário fixo (decisão do usuário: evita a luz
mudar no meio de uma partida competitiva).

Desenho (ver PROBLEMAS_ARQUITETURA.md §194):
  1. `day_night_color()` — ponto único de verdade da cor/alpha do
     tingimento atual, derivado de `day_night_fraction()` por
     interpolação entre keyframes.
  2. `DayNightSystem.render()` — desenha esse tingimento por cima de TODO
     o mundo já renderizado (zoom_surf), igual o overlay de Fog of War já
     faz (mesmo padrão de Surface SRCALPHA cacheada).
  3. Objeto com `TileType.light_source` (lampião etc.) "fura" um buraco
     no tingimento com um gradiente radial + `BLEND_RGBA_SUB` — o brilho
     nasce sozinho conforme escurece (de dia o tingimento já é alpha=0,
     então subtrair não muda nada visualmente — sem precisar de um
     "liga/desliga" separado).
"""
from __future__ import annotations
import time
import pygame

from engine.tileset import TILE_SIZE
from engine.world_systems import System
from ui.ui_components import InstanceInventoryUIState


# ── Relógio ────────────────────────────────────────────────────────────────
# 60 minutos reais = 1 dia completo (pedido do usuário, 21/09/2026).
DAY_NIGHT_CYCLE_S = 60.0 * 60.0

# Keyframes (fração do ciclo 0.0-1.0, RGB do tingimento, alpha 0-255) —
# interpolados linearmente entre os dois mais próximos. alpha=0 = sem
# tingimento nenhum (dia pleno). Ordem crescente de fração, sempre
# terminando em 1.00 com o MESMO valor de 0.00 (loop sem costura).
_KEYFRAMES: list[tuple[float, tuple[int, int, int], int]] = [
    (0.00, (20,  25,  60), 150),   # meia-noite
    (0.20, (20,  25,  60), 150),   # madrugada
    (0.27, (255, 140, 70),  70),   # amanhecer
    (0.33, (0,    0,  0),    0),   # manhã — sem tingimento
    (0.67, (0,    0,  0),    0),   # tarde — sem tingimento
    (0.73, (255, 120, 60),  70),   # entardecer
    (0.80, (20,  25,  60), 150),   # anoitecer
    (1.00, (20,  25,  60), 150),   # volta pra meia-noite
]


def day_night_fraction(now: float | None = None) -> float:
    """0.0-1.0 — posição no ciclo. Mesma fórmula em TODO cliente (função
    pura de `time.time()`, sem estado/servidor) — todo mundo vê a mesma
    hora automaticamente, sem sincronizar nada pela rede."""
    now = now if now is not None else time.time()
    return (now % DAY_NIGHT_CYCLE_S) / DAY_NIGHT_CYCLE_S


def day_night_color(now: float | None = None) -> tuple[int, int, int, int]:
    """RGBA do tingimento atual — ver docstring do módulo."""
    frac = day_night_fraction(now)
    for i in range(len(_KEYFRAMES) - 1):
        f0, rgb0, a0 = _KEYFRAMES[i]
        f1, rgb1, a1 = _KEYFRAMES[i + 1]
        if f0 <= frac <= f1:
            t = 0.0 if f1 == f0 else (frac - f0) / (f1 - f0)
            r = round(rgb0[0] + (rgb1[0] - rgb0[0]) * t)
            g = round(rgb0[1] + (rgb1[1] - rgb0[1]) * t)
            b = round(rgb0[2] + (rgb1[2] - rgb0[2]) * t)
            a = round(a0 + (a1 - a0) * t)
            return (r, g, b, a)
    last = _KEYFRAMES[-1]
    return (last[1][0], last[1][1], last[1][2], last[2])


class DayNightSystem(System):
    def __init__(self, world, player_entity: int):
        self.world         = world
        self.player_entity = player_entity
        self._overlay_surf: "pygame.Surface | None" = None
        self._overlay_size: tuple[int, int] = (0, 0)
        # Cache de gradiente radial por raio em PIXELS (poucos valores
        # possíveis — radius_tiles do catálogo é um inteiro pequeno) —
        # mesmo padrão de recurso cacheado com teto natural de
        # `ui/floating_text.py::_outline_cache`.
        self._light_gradient_cache: dict[int, "pygame.Surface"] = {}

    def _get_light_gradient(self, radius_px: int) -> "pygame.Surface":
        """Gradiente radial cacheado por raio — alpha máximo no centro,
        decaindo linearmente até 0 na borda, cor SEMPRE (0,0,0) (só o
        canal alpha varia). Usado com BLEND_RGBA_SUB: como RGB do
        gradiente é 0, subtrair nunca altera a COR do overlay — só
        reduz seu alpha, "abrindo um buraco" suave nele. Se a cor do
        gradiente não fosse 0, BLEND_RGBA_SUB subtrairia RGB também
        (não ponderado pelo alpha do próprio pixel), estourando pra
        preto puro em vez de revelar a cor normal do mundo por baixo."""
        cached = self._light_gradient_cache.get(radius_px)
        if cached is not None:
            return cached
        d = radius_px * 2
        surf = pygame.Surface((d, d), pygame.SRCALPHA)
        for y in range(d):
            for x in range(d):
                dist = ((x - radius_px) ** 2 + (y - radius_px) ** 2) ** 0.5
                if dist <= radius_px:
                    a = round(255 * (1.0 - dist / radius_px))
                    surf.set_at((x, y), (0, 0, 0, a))
        self._light_gradient_cache[radius_px] = surf
        return surf

    def render(self, world_objects: list | None = None) -> None:
        if self.world_surf is None:
            return
        # Só mundo aberto — BG/Arena ficam sempre num horário fixo
        # (decisão do usuário: luz não deve mudar no meio de uma partida).
        iius = self.world.get_component(self.player_entity, InstanceInventoryUIState)
        if iius is not None and iius.active:
            return

        r, g, b, a = day_night_color()
        if a <= 0:
            return  # dia pleno — nada pra desenhar, economiza o frame inteiro

        surf_w, surf_h = self.world_surf.get_size()
        if self._overlay_surf is None or self._overlay_size != (surf_w, surf_h):
            self._overlay_surf = pygame.Surface((surf_w, surf_h), pygame.SRCALPHA)
            self._overlay_size = (surf_w, surf_h)
        self._overlay_surf.fill((r, g, b, a))

        if world_objects:
            for obj in world_objects:
                light = obj.get("light_source")
                if not light:
                    continue
                off_x, off_y, lw, lh, intensity_pct, radius_tiles = light
                cx = obj["screen_x"] + off_x + lw / 2
                cy = obj["screen_y"] + off_y + lh / 2
                radius_px = radius_tiles * TILE_SIZE
                if radius_px <= 0:
                    continue
                if (cx < -radius_px or cx > surf_w + radius_px
                        or cy < -radius_px or cy > surf_h + radius_px):
                    continue  # fora da tela — não desenha
                gradient = self._get_light_gradient(radius_px)
                strength = max(0.0, min(1.0, intensity_pct / 100.0))
                gradient.set_alpha(round(255 * strength))
                self._overlay_surf.blit(
                    gradient, (cx - radius_px, cy - radius_px),
                    special_flags=pygame.BLEND_RGBA_SUB)

        self.world_surf.blit(self._overlay_surf, (0, 0))
