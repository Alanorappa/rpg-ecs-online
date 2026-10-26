# click_rect_helpers.py
"""
Hit-test de clique compartilhado entre MouseTargetingSystem/PlayerInputSystem
(ui/player_input_system.py) e LootSystem (ui/loot_system.py) — extraído
de ui/systems.py (18/09/2026, ver PROBLEMAS_ARQUITETURA.md §185, item #2
do backlog de 14, "ui/systems.py monolítico").

Os 3 helpers abaixo formam um grupo único (geometria de área clicável +
a checagem "tem alvo vivo aqui" que consome essa geometria) com
consumidores em Systems que acabaram em ARQUIVOS diferentes depois da
divisão — moram aqui, não no arquivo de nenhum dos dois Systems, pra
não criar dependência de um domínio (loot) sobre o outro (input). Os
outros helpers de módulo de ui/systems.py tinham consumidor único e
viajaram junto pro arquivo do respectivo System.
"""
from __future__ import annotations

from engine.components import (
    Position, Renderable, Enemy, Visible, TileMovement, CombatStats,
    RemoteControlled, FogOfWar,
)
from engine.tileset import TILE_SIZE
from engine.world import World
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


def _entity_click_rect(world: World, entity_id: int, pos,
                       renderable) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) da área clicável de uma entidade VIVA
    (mob/player) — ponto único de verdade, 15/09/2026 (item 5 da revisão
    de performance, arquitetura/CLIENT_PERFORMANCE_REVIEW.md). Usa o
    sprite REAL do catálogo (`TILE_SPRITES`) quando `renderable.sprite_id`
    existe (torre, mob com sprite fixo) — mesma âncora de base que
    `RenderSystem.render()` usa pra desenhar (12/08/2026, bug real:
    clique não acertava sprite grande de torre, só um retângulo pequeno
    de sempre). Cai pro retângulo simples (`renderable.width/height`)
    quando não há sprite no catálogo (players, que desenham via
    animação de personagem — `engine/character_animation.py` — nunca
    via `Renderable.sprite_id`).

    Antes desta função, `MouseTargetingSystem._enemy_at_world_pos` já
    fazia essa conta certa (sprite real), mas `_live_target_at_world_pos`
    (abaixo) e `_remote_player_at_world_pos`
    (`MouseTargetingSystem`) reimplementavam só o retângulo simples —
    mob/torre grande podia ficar clicável num caminho e não no outro.
    NUNCA usada pra corpse — `_corpse_click_rect` (acima) é caso à parte,
    com tolerância pequena deliberada pra quando não há sprite."""
    spr = TILE_SPRITES.get_raw_sprite(renderable.sprite_id) if renderable.sprite_id else None
    if spr:
        sw, sh = spr.get_size()
        left   = pos.x - TILE_SIZE / 2
        right  = left + sw
        bottom = pos.y + TILE_SIZE / 2
        top    = bottom - sh
        return left, top, right, bottom
    hw = renderable.width / 2
    hh = renderable.height / 2
    return pos.x - hw, pos.y - hh, pos.x + hw, pos.y + hh


def friendly_target_at_world_pos(world: World, player_entity_id: int,
                                 world_x: float, world_y: float) -> int:
    """PLAYER aliado vivo sob o ponto (player remoto que o jogador não pode
    atacar, ou o PRÓPRIO jogador), ou -1. Usado pelo "mouseover" das skills
    de suporte (SkillSystem): quem está sob o mouse recebe a skill sem
    precisar trocar o alvo de combate. Só players — o servidor só aceita
    player como alvo de suporte (`skill_processor._server_support_tid`).
    Mesma área clicável de `_entity_click_rect`; o servidor revalida."""
    from engine.faction_system import can_engage
    for entity_id, pos, renderable, _, _rc in world.get_entities_with(
            Position, Renderable, Visible, RemoteControlled):
        if entity_id == player_entity_id or _rc.hp <= 0:
            continue
        left, top, right, bottom = _entity_click_rect(world, entity_id, pos, renderable)
        if left <= world_x <= right and top <= world_y <= bottom \
                and not can_engage(world, player_entity_id, entity_id):
            return entity_id
    pos = world.get_component(player_entity_id, Position)
    ren = world.get_component(player_entity_id, Renderable)
    if pos is not None and ren is not None:
        left, top, right, bottom = _entity_click_rect(world, player_entity_id, pos, ren)
        if left <= world_x <= right and top <= world_y <= bottom:
            return player_entity_id
    return -1


def _live_target_at_world_pos(world: World, player_entity_id: int,
                              world_x: float, world_y: float) -> bool:
    """True se há um inimigo vivo OU player remoto vivo na posição mundo
    (mesma bounding box de `_entity_click_rect`/`MouseTargetingSystem.
    _enemy_at_world_pos`/`_remote_player_at_world_pos` — LootSystem e
    MouseTargetingSystem são Systems independentes, em arquivos
    diferentes, sem referência uma à outra — cada um chama a função
    compartilhada em vez de acoplar).

    Usado por LootSystem pra NUNCA competir pelo clique quando um alvo
    (NPC/Player/Mob) ocupa o mesmo tile de um cadáver — bug real relatado
    pelo usuário (03/08/2026): clique direito num inimigo em cima de um
    corpo com loot fazia o personagem CAMINHAR PRO LOOT em vez de atacar
    (LootSystem._try_open_corpse rodava sem checar se havia um alvo vivo
    ali, e até cancelava o CombatState.target_entity_id que
    MouseTargetingSystem já tinha setado no MESMO evento). Prioridade de
    clique pedida pelo usuário: alvo (NPC/Player/Mob) sempre antes de
    loot.

    Checa FogOfWar.visible pro Enemy (15/09/2026 — antes não checava,
    então um inimigo escondido no fog ainda "roubava" o clique do loot;
    RemoteControlled nunca é fog-gated, mesma regra de
    RenderSystem.render(), player real nunca some por fog)."""
    _fog_vis = None
    _fow_first = world.get_first(FogOfWar)
    if _fow_first is not None:
        _fog_vis = _fow_first[1].visible
    for entity_id, pos, renderable, _, _, etm in world.get_entities_with(
            Position, Renderable, Enemy, Visible, TileMovement):
        if _fog_vis is not None and \
                (etm.current_tile_x, etm.current_tile_y) not in _fog_vis:
            continue
        left, top, right, bottom = _entity_click_rect(world, entity_id, pos, renderable)
        if left <= world_x <= right and top <= world_y <= bottom:
            cs = world.get_component(entity_id, CombatStats)
            if not cs or cs.current_hp > 0:
                return True
    for entity_id, pos, renderable, _, rc in world.get_entities_with(
            Position, Renderable, Visible, RemoteControlled):
        if entity_id == player_entity_id or rc.hp <= 0:
            continue
        left, top, right, bottom = _entity_click_rect(world, entity_id, pos, renderable)
        if left <= world_x <= right and top <= world_y <= bottom:
            return True
    return False
