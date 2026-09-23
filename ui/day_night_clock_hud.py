"""
day_night_clock_hud.py — Indicador de ciclo dia/noite no HUD (23/09/2026,
pedido do usuário: "não quero relógio comum", quer noção visual de quando
a noite está chegando, sem números). Reaproveita a MESMA técnica visual
da trilha em gradiente do slider de debug (client/debug_handlers.py::
_draw_debug_tab_dianoite — amostra day_night_color() e simula
MULTIPLICAÇÃO contra um cinza claro de referência), só que:
  - Só LEITURA (sem arrastar/clicar — o F12 pode "viajar no tempo" pra
    debug, o HUD normal nunca deve).
  - Sempre visível no topo-centro da tela (não um painel que abre/fecha).
  - Sem ícone de sol/lua (decisão do usuário 23/09/2026 — sem asset
    pronto no jogo, "escuro=noite, claro=dia" já é intuitivo só pela cor).
  - Com o rótulo da fase atual (ex: "Noite") embaixo da trilha (decisão
    do usuário — reforça sem exigir interpretar a cor).

100% client-only, leitura direta de ui.day_night_system (mesmo ponto
único de verdade que a trilha do F12 e o tingimento ambiente usam) — sem
estado próprio novo. Some durante BG/Arena, mesma regra de
DayNightSystem.render() (checa InstanceInventoryUIState.active) — dia/
noite é só cosmético de mundo aberto. Some também em mapa fechado
(caverna/dungeon, `self.map_affected`, setado por game.py::
_load_ambient_zones() — ver PROBLEMAS_ARQUITETURA.md §206) — sem luz do
sol, o relógio não faz sentido lá.
"""
from __future__ import annotations
import pygame

from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI
from ui.ui_components import InstanceInventoryUIState
from ui.day_night_system import (
    day_night_color, day_night_fraction, current_phase_label, DAY_NIGHT_CYCLE_S,
)


class DayNightClockHud(UIScaleMixin):
    # Bem menos amostras que o slider de debug (60) — a trilha aqui é 160px
    # de largura (vs 220+ do painel), mais amostras que isso é custo sem
    # ganho visual perceptível.
    _SAMPLES = 24
    _FONT_BASES = {"_font_sm": 16}

    def __init__(self, world, player_entity: int) -> None:
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        # Mapa fechado (caverna/dungeon) — setado por game.py::
        # _load_ambient_zones() sempre que o mapa troca, mesmo campo que
        # DayNightSystem.map_affected. True = padrão (mundo aberto).
        self.map_affected: bool = True

    def rect(self, screen_w: int) -> "pygame.Rect":
        """Geometria da trilha (coordenadas de TELA) — topo-centro,
        recalculada sempre do zero (mesmo padrão de _debug_dianoite_slider_rect,
        nunca cacheada de um frame anterior)."""
        w = self._u(UI.DAYNIGHT_CLOCK_W)
        h = self._u(UI.DAYNIGHT_CLOCK_H)
        x = screen_w // 2 - w // 2
        y = self._u(UI.DAYNIGHT_CLOCK_MARGIN_TOP)
        return pygame.Rect(x, y, w, h)

    def render(self, screen: pygame.Surface) -> None:
        iius = self.world.get_component(self.player_entity, InstanceInventoryUIState)
        if iius is not None and iius.active:
            return
        if not self.map_affected:
            return

        r = self.rect(screen.get_width())
        # Mesma simulação de MULTIPLICAÇÃO contra referência CLARA que o
        # slider de debug usa (ver docstring de _draw_debug_tab_dianoite) —
        # nunca um cinza escuro, ou a trilha inteira fica perto de preto.
        base = (200, 195, 180)
        seg_w = r.width / self._SAMPLES
        for i in range(self._SAMPLES):
            seg_frac = i / self._SAMPLES
            rr, gg, bb, _a = day_night_color(seg_frac * DAY_NIGHT_CYCLE_S)
            cr = round(base[0] * rr / 255)
            cg = round(base[1] * gg / 255)
            cb = round(base[2] * bb / 255)
            seg_rect = pygame.Rect(r.x + round(i * seg_w), r.y,
                                   round(seg_w) + 1, r.height)
            pygame.draw.rect(screen, (cr, cg, cb), seg_rect)
        pygame.draw.rect(screen, (140, 120, 90), r, 1, border_radius=2)

        frac = day_night_fraction()
        marker_x = r.x + round(frac * r.width)
        pygame.draw.line(
            screen, (255, 255, 255),
            (marker_x, r.y - self._u(2)), (marker_x, r.bottom + self._u(2)),
            self._u(2))

        phase = current_phase_label()
        lbl = self._font_sm.render(phase, False, (210, 200, 170))
        screen.blit(lbl, (r.centerx - lbl.get_width() // 2, r.bottom + self._u(3)))
