# click_rect_helpers.py
"""
Retângulo de área clicável compartilhado entre MouseTargetingSystem
(ui/player_input_system.py) e LootSystem (ui/loot_system.py) — extraído
de ui/systems.py (18/09/2026, ver PROBLEMAS_ARQUITETURA.md item #2 do
backlog de 14, "ui/systems.py monolítico"). Único helper de módulo que
tinha consumidores em 2 Systems que acabaram em arquivos diferentes
depois da divisão — os outros helpers de ui/systems.py foram só usados
por 1 System cada, e viajaram junto pro arquivo desse System.
"""
from __future__ import annotations

from engine.components import Renderable
from engine.tileset import TILE_SIZE
from ui.tile_sprite_manager import TILE_SPRITES


def _corpse_click_rect(world, entity_id: int, pos) -> tuple[float, float, float, float]:
    """Retorna (left, top, right, bottom) da área clicável de um corpse
    (Fase M1, revisão 3, 25/07/2026 — bug real relatado pelo usuário:
    "andei até a caixa, cliquei, nada aconteceu"). Antes, TANTO
    `MouseTargetingSystem._corpse_at_world_pos` QUANTO
    `LootSystem._try_open_corpse` usavam uma tolerância fixa pequena
    (±14×±10px), calibrada pra elipse achatada de 20×12px — harvestable
    com sprite real (ex: `pr_box1`, 32×64) tem a MAIORIA da área visível
    fora dessa faixa (a elipse cobre só a base do tile; o sprite sobe
    ~48px acima do centro), então clicar na parte de cima/meio do sprite
    (o que a maioria dos cliques faz, visualmente) não acertava nada.
    Fix: se o corpse tem `Renderable` com `sprite_id`, a área clicável
    vira o retângulo do sprite de verdade (mesmo ancoramento de
    `RenderSystem.render()` — base do sprite = base do tile); senão
    mantém a tolerância antiga (corpse de mob morto, sem Renderable)."""
    ren = world.get_component(entity_id, Renderable)
    if ren is not None and ren.sprite_id:
        sprite = TILE_SPRITES.get_raw_sprite(ren.sprite_id)
        if sprite is not None:
            sw, sh = sprite.get_size()
            left  = pos.x - TILE_SIZE / 2
            right = left + sw
            bottom = pos.y + TILE_SIZE / 2
            top    = bottom - sh
            return left, top, right, bottom
    return pos.x - 14, pos.y - 10, pos.x + 14, pos.y + 10
