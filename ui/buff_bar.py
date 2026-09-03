"""
buff_bar.py — Buff bar unificada (31/08/2026, pedido do usuário, estilo WoW).

Mostra os buffs TEMPORÁRIOS ativos no PRÓPRIO jogador (proc de item,
self-buff de skill, buff de matar chefe de jungle) — não importa a
origem, todos passam por engine.core_systems.grant_visible_buff, que
registra um ActiveEffect (StatusEffects, is_buff=True em EFFECT_DEFS) e
reaproveita a mesma sincronização de rede que debuff já usa (AOI_UPDATE/
deltas["effects"]). Debuff continua no nameplate (ui/systems.py::
_filter_nameplate_effects exclui buff de lá desde esta mesma sessão, pra
não duplicar ícone nos dois lugares).

Fase 2 (mesma data): alguns efeitos temporários do jogador NÃO usam
StatusEffects — têm mecanismo PRÓPRIO (componente dedicado com tick
duplicado cliente+servidor, ou uma flag simples de "proc pronto pra
usar"). Migrar todos pra StatusEffects seria um refactor grande e
arriscado (Camuflagem sozinha tem 10+ consumidores — visibilidade AOI,
alvo de mob, respawn/ghost); decisão do usuário: a buff bar só GANHA a
capacidade de LER esses componentes/campos já existentes
(`_gather_extra_buffs`), sem tocar em como cada um funciona por dentro.

Só o jogador LOCAL tem buff bar — remoto/mob continuam sem, mesma
decisão do usuário (WoW: buff é pessoal, debuff aparece em todo mundo).

Ordem da fila (01/09/2026): mais recente sempre na ponta direita (junto
do minimapa), mais antigo empurrado pra esquerda — igual WoW. StatusEffects
sozinho já dava essa ordem entre buffs DA MESMA fonte (ordem de inserção
do dict), mas os buffs de `_gather_extra_buffs` (fonte diferente) sempre
apareciam depois de QUALQUER buff de StatusEffects, mesmo tendo ligado
antes — corrigido com `engine/effect_order.py` (ponto único, um contador
de ativação por entidade, compartilhado entre TODAS as fontes) — ver
PROBLEMAS_ARQUITETURA.md.
"""
from __future__ import annotations
import pygame

from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI
from content.status_effects_data import EFFECT_DEFS


class _FakeEffect:
    """Efeito sintético — só os 2 atributos que _build_effects_row()
    precisa (mesmo padrão já usado em ui/systems.py pro shim de stun no
    nameplate). Usado pra representar um mecanismo que NÃO é StatusEffects
    (Escudo de Fogo, Bloco de Gelo, Camuflagem, procs binários) como um
    ícone na buff bar, sem inventar um tipo novo de dado."""
    __slots__ = ("effect_type", "duration")

    def __init__(self, effect_type: str, duration: float) -> None:
        self.effect_type = effect_type
        self.duration     = duration


def _gather_extra_buffs(world, player_entity: int) -> list:
    """Efeitos temporários do jogador com mecanismo PRÓPRIO (fora de
    StatusEffects) — 1 checador pequeno por fonte, cada um só LÊ o
    componente/campo real (nunca escreve, nunca interfere no tick/
    expiração/sincronização de cada mecanismo). Efeito novo desse tipo no
    futuro só precisa de mais 1 checador aqui + 1 entrada em
    content/status_effects_data.py::EFFECT_DEFS pro ícone/cor de
    fallback — nunca precisa virar StatusEffects de verdade só pra
    aparecer na barra."""
    from engine.components import FireShieldEffect, IceBlockEffect, CombatStats, CharacterStats

    out: list = []

    fire_shield = world.get_component(player_entity, FireShieldEffect)
    if fire_shield is not None:
        remaining = max(0.0, fire_shield.duration - fire_shield.elapsed)
        out.append(_FakeEffect("escudo_fogo", remaining))

    ice_block = world.get_component(player_entity, IceBlockEffect)
    if ice_block is not None:
        remaining = max(0.0, ice_block.duration - ice_block.elapsed)
        out.append(_FakeEffect("bloco_de_gelo", remaining))

    cs = world.get_component(player_entity, CombatStats)
    if cs is not None:
        if cs.camouflage_timer > 0:
            out.append(_FakeEffect("camuflagem", cs.camouflage_timer))
        if cs.na_mosca_bonus_active:
            out.append(_FakeEffect("na_mosca", 0.0))   # proc pronto — sem contador

    char = world.get_component(player_entity, CharacterStats)
    if char is not None and getattr(char, "fire_instant_ready", False):
        out.append(_FakeEffect("chama_interna", 0.0))   # proc pronto — sem contador

    return out


class BuffBar(UIScaleMixin):
    _FONT_BASES = {"_font_dur": 18}   # mesmo tamanho de _effect_dur_font (ui/systems.py)

    def __init__(self, world, player_entity: int) -> None:
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        # Retângulos (coordenadas de TELA) do último frame desenhado —
        # 01/09/2026, pro tooltip (check_hover) saber qual ícone está sob
        # o mouse sem recalcular a geometria de novo (mesmo princípio já
        # usado pela hotbar: geometria calculada 1x, reusada pro hit-test).
        self._last_icon_rects: list = []

    def render(self, screen: pygame.Surface) -> None:
        from engine.components import StatusEffects
        from ui.systems import _build_effects_row

        self._last_icon_rects = []

        sfx = self.world.get_component(self.player_entity, StatusEffects)
        buffs = [e for e in (sfx.effects.values() if sfx else [])
                if (EFFECT_DEFS.get(e.effect_type) and EFFECT_DEFS[e.effect_type].is_buff)]
        buffs.extend(_gather_extra_buffs(self.world, self.player_entity))
        if not buffs:
            return

        # Ordena pela ordem de ATIVAÇÃO real (engine/effect_order.py) —
        # não pela ordem de inserção de cada lista separada. Sem isso, um
        # efeito com mecanismo próprio (Escudo de Fogo/Bloco de Gelo/
        # Camuflagem/procs) sempre apareceria depois de QUALQUER buff de
        # StatusEffects, mesmo tendo ligado antes — as 2 fontes não têm
        # relógio em comum além deste. _build_effects_row desenha o
        # primeiro da lista mais à esquerda, então ordem crescente = mais
        # antigo à esquerda, mais novo à direita (mesma convenção de
        # sempre).
        from engine.effect_order import get_seq
        buffs.sort(key=lambda e: get_seq(self.world, self.player_entity, e.effect_type))

        row, local_rects = _build_effects_row(buffs, self._font_dur, icon_size=self._u(UI.BUFF_BAR_ICON))
        if row is None:
            return

        sw, _ = screen.get_size()
        minimap_left_x = sw - self._u(UI.MINIMAP_SIZE) - self._u(UI.MINIMAP_MARGIN_RIGHT)
        bar_right_x = minimap_left_x - self._u(UI.BUFF_BAR_GAP_TO_MINIMAP)
        bar_top_y   = self._u(UI.BUFF_BAR_MARGIN_TOP)
        blit_x = bar_right_x - row.get_width()
        screen.blit(row, (blit_x, bar_top_y))

        # Translada os retângulos locais (relativos à Surface) pra
        # coordenadas de TELA, guarda a duração junto (pro tooltip poder
        # mostrar o tempo restante) — `local_rects` sai de
        # _build_effects_row() na MESMA ordem/tamanho de `buffs` (nenhum
        # efeito de buff bar é um "efeito de chão" filtrado lá dentro).
        self._last_icon_rects = [
            (eff.effect_type, local_rect.move(blit_x, bar_top_y), eff.duration)
            for eff, (_et, local_rect) in zip(buffs, local_rects)
        ]

    def check_hover(self, mx: int, my: int) -> "tuple[str, list] | None":
        """Se `(mx, my)` está sobre algum ícone desenhado no último
        `render()`, devolve `(titulo, lines)` no MESMO formato que
        `client/tooltip_handlers.py::_draw_tooltip()` já espera — sem
        nenhum código de desenho novo, só reaproveita a caixa que
        item/skill já usam. `lines` é uma lista de `(texto, cor)`.
        Devolve `None` se o mouse não está sobre nenhum ícone."""
        from math import ceil as _ceil
        for effect_type, rect, duration in self._last_icon_rects:
            if not rect.collidepoint(mx, my):
                continue
            defn = EFFECT_DEFS.get(effect_type)
            if defn is None:
                return None
            lines: list = []
            if defn.description:
                lines.append((defn.description, (220, 220, 220)))
            if duration > 0:
                lines.append((f"{max(1, _ceil(duration))}s restantes", (170, 170, 170)))
            return defn.label, lines
        return None
