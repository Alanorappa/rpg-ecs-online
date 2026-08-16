"""
tests/test_client_ui.py — Testes headless de UI do CLIENTE (SDL dummy).

Primeiros testes permanentes de cliente do projeto (item D2 da auditoria,
PROBLEMAS_ARQUITETURA.md §11): os padrões de validação headless usados nas
correções de 11-15/07/2026 (marcadores de mapa, fonte tight, HUD composta)
viravam scripts descartáveis no scratchpad — promovidos aqui pra virarem
sinal de regressão.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
# Vários caminhos de UI exigem um display real (convert_alpha, mouse):
pygame.display.set_mode((320, 240))


# ── ui/systems.py::CameraSystem — snap quando perto do alvo ──────────────────
# Bug real relatado pelo usuário 19/07/2026: câmera "tremia" bem no instante
# em que o personagem parava. Causa: lerp de decaimento exponencial nunca
# chega EXATO no alvo — fica fazendo ajustes minúsculos decrescentes pra
# sempre, e isso "vaza" como jitter visual (mais perceptível parado, sem
# movimento maior mascarando). SNAP_EPSILON trava exato no alvo quando perto
# o bastante, eliminando o resíduo interminável. `SMOOTHING_ENABLED = False`
# por padrão desde o mesmo dia (diagnóstico: tremida persistiu mesmo com
# SNAP_EPSILON — desligar a suavização inteira ajuda a isolar se a causa é
# mesmo o lerp) — os 2 testes abaixo ligam explicitamente pra continuar
# cobrindo a lógica de suavização, caso volte a ser usada.

def test_camera_trava_exato_no_alvo_quando_perto_o_bastante():
    from engine.world import World
    from engine.components import Camera, Position
    from ui.systems import CameraSystem

    world = World()
    target = world.create_entity()
    world.add_component(target, Position(x=100.0, y=100.0))
    cam = world.create_entity()
    world.add_component(cam, Camera(target_entity_id=target))
    world.add_component(cam, Position(x=100.0 - CameraSystem.SNAP_EPSILON / 2, y=100.0))

    sys_cam = CameraSystem(world)
    sys_cam.SMOOTHING_ENABLED = True
    sys_cam.update(dt=1 / 60)

    cam_pos = world.get_component(cam, Position)
    assert cam_pos.x == 100.0
    assert cam_pos.y == 100.0


def test_camera_continua_suavizando_quando_longe_do_alvo():
    """SMOOTHING_ENABLED está False por padrão (diagnóstico 19/07/2026,
    ver ARQUITETURA_ONLINE.md) — este teste liga explicitamente pra
    continuar cobrindo o comportamento de lerp, caso volte a ser usado."""
    from engine.world import World
    from engine.components import Camera, Position
    from ui.systems import CameraSystem

    world = World()
    target = world.create_entity()
    world.add_component(target, Position(x=100.0, y=0.0))
    cam = world.create_entity()
    world.add_component(cam, Camera(target_entity_id=target))
    world.add_component(cam, Position(x=0.0, y=0.0))

    sys_cam = CameraSystem(world)
    sys_cam.SMOOTHING_ENABLED = True
    sys_cam.update(dt=1 / 60)

    cam_pos = world.get_component(cam, Position)
    assert 0.0 < cam_pos.x < 100.0   # se aproximou, mas não saltou direto


def test_camera_sem_suavizacao_gruda_direto_no_alvo():
    """Default atual (SMOOTHING_ENABLED=False) — sem lerp nenhum, a câmera
    fica exatamente em cima do alvo todo frame, mesmo longe."""
    from engine.world import World
    from engine.components import Camera, Position
    from ui.systems import CameraSystem

    world = World()
    target = world.create_entity()
    world.add_component(target, Position(x=500.0, y=-30.0))
    cam = world.create_entity()
    world.add_component(cam, Camera(target_entity_id=target))
    world.add_component(cam, Position(x=0.0, y=0.0))

    sys_cam = CameraSystem(world)
    assert sys_cam.SMOOTHING_ENABLED is False
    sys_cam.update(dt=1 / 60)

    cam_pos = world.get_component(cam, Position)
    assert cam_pos.x == 500.0
    assert cam_pos.y == -30.0


# ── ui/map_markers.py ────────────────────────────────────────────────────────

def _make_marker_world():
    from engine.world import World
    from engine.components import (TileMovement, Trainer, Merchant, Visible)
    world = World()
    trainer = world.create_entity()   # SEM Visible (fora de FoW) — deve aparecer
    world.add_component(trainer, TileMovement(current_tile_x=5, current_tile_y=5))
    world.add_component(trainer, Trainer(class_id="mago"))
    merchant = world.create_entity()  # COM Visible — também aparece
    world.add_component(merchant, TileMovement(current_tile_x=8, current_tile_y=8))
    world.add_component(merchant, Merchant())
    world.add_component(merchant, Visible())
    return world


class _FakeQuestDialog:
    def marker_for(self, eid):
        return None


def test_collect_markers_nao_exige_visible():
    """Decisão 25 (ARQUITETURA_ONLINE.md): ícone de mapa/minimapa GUIA o
    player — NPC fora da linha de visão continua no mapa."""
    from ui.map_markers import collect_markers
    world = _make_marker_world()
    player = world.create_entity()
    markers = collect_markers(world, player, _FakeQuestDialog())
    kinds = sorted(m.icon_name for m in markers)
    assert kinds == ["map_merchant", "map_trainer_mago"], kinds


def test_deconflict_positions_sem_sobreposicao():
    from ui.map_markers import deconflict_positions
    pts = [(100.0, 100.0)] * 4           # 4 marcadores no MESMO ponto
    placed = deconflict_positions(pts, min_dist=8.0)
    assert len(placed) == 4
    for i, (ax, ay) in enumerate(placed):
        for bx, by in placed[i + 1:]:
            assert (ax - bx) ** 2 + (ay - by) ** 2 >= 8.0 ** 2 - 1e-6, \
                "dois marcadores continuam sobrepostos"


# ── ui/fonts.py::render_tight ────────────────────────────────────────────────

def test_render_tight_remove_bearing_gigante():
    """Bug real 11/07: 'i' da MEGAMAN10 tem advance=6 mas tinta começa em x=3
    ('Zumbi' virava 'Zumb i'). render_tight reempacota pela tinta real."""
    from ui.fonts import make_pixel, render_tight
    font = make_pixel(16)
    naive = font.render("Zumbi", False, (255, 255, 255))
    tight = render_tight(font, "Zumbi", (255, 255, 255))
    assert tight.get_width() < naive.get_width(), \
        f"tight ({tight.get_width()}px) deveria ser mais estreito que naive ({naive.get_width()}px)"
    # cache: mesma chamada devolve o MESMO objeto
    assert render_tight(font, "Zumbi", (255, 255, 255)) is tight


# ── ui/hud_bars.py ───────────────────────────────────────────────────────────

def test_build_hud_tamanhos_e_cache_de_nivel():
    import ui.hud_bars as hb
    from ui.fonts import make_pixel
    font = make_pixel(hb.LEVEL_FONT_SIZE)

    p = hb.build_player_hud(0.5, 0.3, 0.8, hb.RESOURCE_COLORS["arqueiro"], 12, font)
    assert p.get_size() == (hb.P_SIZE[0] * hb.SCALE, hb.P_SIZE[1] * hb.SCALE)

    m = hb.build_mob_hud(0.9, 7, font)
    assert m.get_size() == (hb.M_SIZE[0] * hb.SCALE, hb.M_SIZE[1] * hb.SCALE)

    # Cache do número de nível (fix de perf 14/07): 2ª chamada = cache hit
    hb._level_surf_cache.clear()
    hb._level_surf(33, font)
    n_after_first = len(hb._level_surf_cache)
    hb._level_surf(33, font)
    assert len(hb._level_surf_cache) == n_after_first == 1


def test_effects_row_offset_meia_largura():
    """Bug real 11/07: usava largura INTEIRA pra ir do centro à borda direita
    — sobrava um vão do tamanho da HUD entre ela e os ícones de efeito."""
    import ui.hud_bars as hb
    from ui.fonts import make_pixel
    surf = hb.build_mob_hud(1.0, 1, make_pixel(hb.LEVEL_FONT_SIZE))
    xo, yo = hb.effects_row_offset(surf)
    assert xo == surf.get_width() / 2 + hb.EFFECTS_GAP_PX
    assert yo == -hb.HUD_GAP_PX - surf.get_height() / 2


# ── ui/systems.py::LootSystem — loot free-for-all de grupo, sem duplicar ──────
# Bug real relatado pelo usuário 17/07/2026: em grupo, cada membro processava
# sua PRÓPRIA cópia local do corpse (criada em LOOT_AVAILABLE) de forma
# independente — dois membros lootavam o MESMO ouro. Fix: em modo online
# (_online_loot_requester setado), clicar manda LOOT_REQUEST em vez de
# creditar da cópia local; crédito real só acontece em LOOT_RESULT
# (client/network_handlers.py::_handle_msg_loot_result).

def _make_loot_world():
    from engine.world import World
    from engine.entity_factory import create_corpse
    from engine.components import Wallet, Inventory, PlayerControlled
    from ui.ui_components import LootUIState
    world = World()
    player = world.create_entity()
    world.add_component(player, PlayerControlled())
    world.add_component(player, Wallet(gold=0))
    world.add_component(player, Inventory())
    world.add_component(player, LootUIState())
    corpse = create_corpse(world, 100, 100, [], coins=11)
    return world, player, corpse


def _make_harvestable_loot_world():
    """Mesmo esqueleto que _spawn_remote_harvestable monta no cliente
    (Position+Renderable(sprite_id)+Corpse) — usado pra testar a área de
    clique real do sprite, não a tolerância antiga calibrada pra elipse."""
    from engine.world import World
    from engine.components import (Wallet, Inventory, PlayerControlled,
                                   Position, Renderable, Corpse)
    from ui.ui_components import LootUIState
    world = World()
    player = world.create_entity()
    world.add_component(player, PlayerControlled())
    world.add_component(player, Wallet(gold=0))
    world.add_component(player, Inventory())
    world.add_component(player, LootUIState())
    hv = world.create_entity()
    world.add_component(hv, Position(x=100, y=100, prev_x=100, prev_y=100))
    world.add_component(hv, Renderable(color=(120, 90, 60), width=32, height=32,
                                       sprite_id="pr_box1"))
    world.add_component(hv, Corpse(loot=[], coins=10))
    return world, player, hv


def _click_gold_row(loot_system, corpse_eid) -> bool:
    loot_system._open_modal(corpse_eid, 50, 50)
    modal = loot_system._modal_rect()
    row = loot_system._row_rect(modal, 0)   # linha 0 = ouro
    return loot_system._try_take_item(row.centerx, row.centery)


def _click_row(loot_system, corpse_eid, row_index: int) -> bool:
    loot_system._open_modal(corpse_eid, 50, 50)
    modal = loot_system._modal_rect()
    row = loot_system._row_rect(modal, row_index)
    return loot_system._try_take_item(row.centerx, row.centery)


def test_online_loot_request_nao_credita_localmente():
    from ui.systems import LootSystem
    from engine.components import Wallet, Corpse
    world, player, corpse = _make_loot_world()
    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)

    sent = []
    loot.set_online_loot_requester(lambda local_eid, take, item_id: sent.append((local_eid, take, item_id)))
    result = _click_gold_row(loot, corpse)

    wallet = world.get_component(player, Wallet)
    assert result is True
    assert wallet.gold == 0, "modo online nao deveria creditar ouro localmente no clique"
    assert sent == [(corpse, "gold", "")], \
        "deveria mandar o request com o eid LOCAL do corpse e take='gold'"
    assert world.get_component(corpse, Corpse).coins == 11, \
        "corpse local nao deveria ser mutado antes do LOOT_RESULT confirmar"


def test_online_loot_request_nao_duplica_pedido_em_voo():
    from ui.systems import LootSystem
    world, player, corpse = _make_loot_world()
    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)

    sent = []
    loot.set_online_loot_requester(lambda local_eid, take, item_id: sent.append((local_eid, take, item_id)))
    _click_gold_row(loot, corpse)
    _click_gold_row(loot, corpse)   # 2º clique antes do LOOT_RESULT responder

    assert sent == [(corpse, "gold", "")], \
        "clique duplicado enquanto o request está em voo não deveria reenviar"


def test_online_loot_request_item_manda_take_item_com_item_id():
    """Clicar num item (não no ouro) manda take='item' + item_id (débito
    A4, 11/08/2026 — era item_name; nome de exibição não distingue itens
    diferentes com o mesmo nome) — não 'all' (senão o request levaria o
    ouro junto, bug real relatado pelo usuário 17/07/2026: sacar parcial
    não deveria afetar o resto)."""
    from ui.systems import LootSystem
    from content.item_table import ITEMS
    from engine.entity_factory import create_corpse
    from engine.components import Wallet, Inventory, PlayerControlled
    from ui.ui_components import LootUIState
    from engine.world import World

    world = World()
    player = world.create_entity()
    world.add_component(player, PlayerControlled())
    world.add_component(player, Wallet(gold=0))
    world.add_component(player, Inventory())
    world.add_component(player, LootUIState())
    item = next(iter(ITEMS.values()))()
    corpse = create_corpse(world, 100, 100, [item], coins=11)

    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)
    sent = []
    loot.set_online_loot_requester(lambda local_eid, take, item_id: sent.append((local_eid, take, item_id)))
    result = _click_row(loot, corpse, 1)   # linha 1 = 1º item (linha 0 é o ouro)

    assert result is True
    assert sent == [(corpse, "item", item.item_id)]


# ── Área de clique de harvestable com sprite alto (Fase M1, revisão 3) ───────
# Bug real relatado pelo usuário 25/07/2026: "andei até a caixa, cliquei com
# o direito, nada aconteceu". Causa: a tolerância de clique de corpse
# (±14×±10px, em MouseTargetingSystem._corpse_at_world_pos e
# LootSystem._try_open_corpse) era fixa, calibrada pra elipse achatada de
# 20×12px — sprite real (ex: "pr_box1", 32×64, ancorado com a BASE no tile)
# sobe ~48px acima do centro da entidade, e a maior parte dessa área visível
# ficava fora da tolerância antiga. Fix: ui/systems.py::_corpse_click_rect
# calcula a área clicável a partir do tamanho REAL do sprite quando a
# entidade tem Renderable+sprite_id (harvestable) — corpse de mob morto
# (sem Renderable) mantém a tolerância antiga, sem mudança de comportamento.

def test_try_open_corpse_acerta_topo_do_sprite_alto():
    """Clique no TOPO visual do sprite (fora da tolerância antiga de
    ±10px, dentro da altura real de 64px) precisa abrir o modal."""
    from ui.systems import LootSystem
    world, player, hv = _make_harvestable_loot_world()
    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)

    # pos.y=100, sprite 64px alto ancorado na base (bottom=116, top=52) —
    # y=60 está bem no topo visual, fora do ±10px antigo (90-110).
    loot._try_open_corpse(100, 60)
    assert loot.open_corpse_id == hv, \
        "clique no topo do sprite alto deveria abrir o modal de loot"


def test_corpse_at_world_pos_acerta_topo_do_sprite_alto():
    """Mesmo cenário, mas pelo lado de MouseTargetingSystem (decide se
    deixa o LootSystem tratar o clique em vez de andar até o tile)."""
    from ui.systems import MouseTargetingSystem
    world, player, hv = _make_harvestable_loot_world()
    screen = pygame.display.get_surface()
    targeting = MouseTargetingSystem(world, player, screen)
    assert targeting._corpse_at_world_pos(100, 60) is True


def test_try_open_corpse_mob_morto_mantem_tolerancia_antiga():
    """Corpse de mob morto (sem Renderable) não deveria ficar clicável
    numa área maior — comportamento antigo intacto."""
    from ui.systems import LootSystem
    world, player, corpse = _make_loot_world()
    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)

    loot._try_open_corpse(100, 60)   # mesma distância do teste acima
    assert loot.open_corpse_id == -1, \
        "corpse sem Renderable não deveria abrir a essa distância (tolerância antiga)"


def test_try_open_corpse_de_longe_anda_pro_tile_adjacente_nao_pro_proprio():
    """Bug real relatado pelo usuário 25/07/2026 (3ª rodada): "cliquei em
    vários pontos da caixa, não abriu". Causa: clicar de LONGE (>1 tile)
    fazia o auto-move mirar o PRÓPRIO tile do corpse — pra harvestable
    (que agora tem colisão real, TileMovement) esse tile é SÓLIDO, então
    o player nunca conseguia chegar lá e a fila de movimento ficava presa
    pra sempre, sem NUNCA abrir o modal. Fix: mirar o tile ADJACENTE mais
    próximo do player (mesmo padrão de _walk_to_merchant)."""
    from ui.systems import LootSystem
    from engine.components import (TileMovement, PlayerAutoMove, CombatState)
    world, player, hv = _make_harvestable_loot_world()
    # harvestable em pos.x=100,y=100 -> tile (3,3). Player longe (tile (0,0),
    # dist=3) — fora do alcance de abertura imediata (dist>1).
    world.add_component(player, TileMovement(current_tile_x=0, current_tile_y=0,
                                             target_tile_x=0, target_tile_y=0))
    world.add_component(player, PlayerAutoMove())
    world.add_component(player, CombatState())

    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)
    loot._try_open_corpse(100, 100)   # clique no centro do harvestable

    auto = world.get_component(player, PlayerAutoMove)
    assert auto.ground_target != (3, 3), \
        "não deveria mirar o próprio tile do harvestable (sólido — nunca alcançável)"
    # Tile adjacente mais próximo do player em (0,0): (2,3) ou (3,2).
    assert auto.ground_target in [(2, 3), (3, 2)]
    assert loot.open_corpse_id == -1   # não abriu ainda — só começou a andar


def test_try_open_corpse_de_longe_pula_adjacente_mais_proximo_se_ele_for_solido():
    """2º bug real do mesmo tipo (25/07/2026, caixa de teste M2 relatada pelo
    usuário em (111,383)): a escolha do tile adjacente "mais próximo" não
    filtrava por walkability — se o adjacente geometricamente mais perto do
    PLAYER for ele mesmo sólido (ex.: parede colada na caixa), o auto-move
    mirava um tile inalcançável e nunca chegava, travando o loot pra sempre
    (mesma classe de bug do teste anterior, só que no tile ADJACENTE em vez
    do próprio tile do corpse). Fix: filtra os 4 adjacentes por walkability
    antes de escolher o mais próximo."""
    import engine.world_systems as ws_mod
    from ui.systems import LootSystem
    from engine.components import (TileMovement, PlayerAutoMove, CombatState)
    world, player, hv = _make_harvestable_loot_world()
    # harvestable em pos.x=100,y=100 -> tile (3,3). Player em (0,0): sem
    # bloqueio nenhum, o mais próximo (empate) seria (2,3) (primeiro da
    # lista com distância mínima) — bloqueamos justamente esse.
    world.add_component(player, TileMovement(current_tile_x=0, current_tile_y=0,
                                             target_tile_x=0, target_tile_y=0))
    world.add_component(player, PlayerAutoMove())
    world.add_component(player, CombatState())

    class _FakeTileValidationBlockNearest:
        def is_tile_walkable(self, entity_id, tx, ty, from_tx=None,
                             from_ty=None, ignore_eid=-1):
            return (tx, ty) != (2, 3)   # só (2,3) é sólido

    ws_mod._svc_resolver = None
    ws_mod.register_services(tile_validation=_FakeTileValidationBlockNearest())
    try:
        screen = pygame.display.get_surface()
        loot = LootSystem(world, screen, player_entity=player)
        loot._try_open_corpse(100, 100)   # clique no centro do harvestable

        auto = world.get_component(player, PlayerAutoMove)
        assert auto.ground_target != (2, 3), \
            "não deveria mirar o adjacente bloqueado, mesmo sendo o geometricamente mais próximo"
        assert auto.ground_target == (3, 2), \
            "deveria escolher o próximo adjacente walkable mais próximo"
    finally:
        ws_mod._svc.pop("tile_validation", None)


def test_try_open_corpse_nao_compete_com_alvo_vivo_no_mesmo_tile():
    """Bug real relatado pelo usuário (03/08/2026): clique direito num
    inimigo em cima de um corpo com loot fazia o personagem CAMINHAR PRO
    LOOT em vez de atacar — LootSystem._try_open_corpse rodava sem checar
    se havia um alvo vivo ali, e até cancelava o CombatState.
    target_entity_id que MouseTargetingSystem já tinha setado no MESMO
    clique. Prioridade pedida pelo usuário: alvo (NPC/Player/Mob) sempre
    antes de loot."""
    from ui.systems import LootSystem
    from engine.components import (TileMovement, PlayerAutoMove, CombatState,
                                   Enemy, Visible, CombatStats, Renderable, Position)
    world, player, corpse = _make_loot_world()
    world.add_component(player, TileMovement(current_tile_x=2, current_tile_y=3,
                                             target_tile_x=2, target_tile_y=3))
    world.add_component(player, PlayerAutoMove())
    cs_state = CombatState()
    cs_state.target_entity_id = 999  # já setado por MouseTargetingSystem neste MESMO clique
    cs_state.is_pursuing = True
    world.add_component(player, cs_state)

    enemy = world.create_entity()
    world.add_component(enemy, Position(x=100, y=100))
    world.add_component(enemy, Renderable(color=(200, 0, 0), width=32, height=32))
    world.add_component(enemy, Enemy())
    world.add_component(enemy, Visible())
    world.add_component(enemy, TileMovement(current_tile_x=3, current_tile_y=3,
                                            target_tile_x=3, target_tile_y=3))
    world.add_component(enemy, CombatStats())

    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)
    loot._try_open_corpse(100, 100)   # clique no centro do corpo/inimigo (mesmo tile)

    assert loot.open_corpse_id == -1, "não deveria abrir o modal de loot"
    assert loot.pending_loot_corpse_id == -1, "não deveria começar a andar pro corpo"
    auto = world.get_component(player, PlayerAutoMove)
    assert auto.ground_target is None, "não deveria mirar movimento pro corpo"
    cs_after = world.get_component(player, CombatState)
    assert cs_after.target_entity_id == 999, "não deveria cancelar o alvo já selecionado"
    assert cs_after.is_pursuing is True


# ── LootSystem.update — modal fecha sozinho ao esvaziar ──────────────────────
# Bug real relatado pelo usuário 25/07/2026: depois do fix do despawn genérico
# assimétrico (§34.54, harvestable não é mais removido ao esvaziar), o modal
# de loot parou de fechar sozinho ao pegar tudo — "irritante". Causa: o
# fechamento automático SÓ checava "componente Corpse sumiu" (entidade
# removida), que era como a versão ANTIGA (bugada) esvaziava harvestable —
# o fechamento nunca teve checagem própria de "esvaziou", só funcionava por
# acidente via aquele bug agora corrigido.

def test_loot_update_fecha_modal_sozinho_quando_corpse_esvazia():
    from ui.systems import LootSystem
    from engine.components import Corpse
    world, player, hv = _make_harvestable_loot_world()
    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)
    loot.open_corpse_id = hv

    corpse = world.get_component(hv, Corpse)
    corpse.loot = []
    corpse.coins = 0
    loot.update([])

    assert loot.open_corpse_id == -1, \
        "modal deveria fechar sozinho quando o corpse aberto esvazia por completo"


def test_loot_update_nao_fecha_modal_com_corpse_ainda_com_loot():
    from ui.systems import LootSystem
    world, player, hv = _make_harvestable_loot_world()
    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)
    loot.open_corpse_id = hv   # Corpse(loot=[], coins=10) — ainda tem ouro

    loot.update([])

    assert loot.open_corpse_id == hv, \
        "modal não deveria fechar enquanto o corpse ainda tem loot/ouro"


# ── client/network_handlers.py::_handle_msg_loot_result — sem INV_SYNC ───────
# Histórico: bug real relatado pelo usuário 18/07/2026 (progresso de quest
# "colete N itens" parou de atualizar) foi corrigido na época mandando
# INV_SYNC depois de creditar item de loot, pra servidor saber do Inventory
# novo e rodar sync_collect_progress. Débito A4 (11/08/2026, ver
# PROBLEMAS_ARQUITETURA.md): saque virou autoritativo no servidor —
# request_loot() já bota o item direto no Inventory AO VIVO do servidor
# ANTES de responder LOOT_RESULT, então o INV_SYNC não faz mais falta —
# removido, e sync_collect_progress se moveu pra dentro de
# server/session.py::_handle_loot_request (roda no MESMO fluxo que muta o
# Inventory, não mais como efeito colateral de um pacote separado).

from client.network_handlers import NetworkHandlers as _NH_loot


class _LootResultFixture(_NH_loot):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._available_loot = {}
        self._remote_corpses = {}
        self._loot_system = type("_FakeLootSys", (), {"_online_loot_pending": set()})()
        self.loot_actions = []
        self.save_state_calls = 0

    def _on_loot_action(self, change_type: str = "item") -> None:
        self.loot_actions.append(change_type)

    def _send_save_state(self) -> None:
        self.save_state_calls += 1


def _make_loot_result_fixture():
    from engine.world import World
    from engine.components import Wallet, Inventory

    world = World()
    player = world.create_entity()
    world.add_component(player, Wallet(gold=0))
    world.add_component(player, Inventory())
    return _LootResultFixture(world, player)


def test_loot_result_com_item_nunca_manda_inv_sync():
    """`items` em LOOT_RESULT já é exatamente o que o servidor concedeu no
    Inventory ao vivo dele (débito A4) — creditar a cópia local nunca
    deveria disparar INV_SYNC de volta."""
    fx = _make_loot_result_fixture()
    fx._handle_msg_loot_result({
        "corpse_id": 1, "coins": 0,
        "items": [{"item_id": "hp_potion", "name": "Poção de Vida", "stack": 1}],
    })
    assert fx.loot_actions == [], \
        "loot server-autoritativo não deveria mandar INV_SYNC nunca mais"
    assert fx.save_state_calls == 1


def test_loot_result_so_ouro_nao_manda_inv_sync():
    """Ouro já é server-authoritative (request_loot credita o Wallet do
    servidor direto) — nunca precisou de INV_SYNC."""
    fx = _make_loot_result_fixture()
    fx._handle_msg_loot_result({"corpse_id": 1, "coins": 11, "items": []})
    assert fx.loot_actions == []
    assert fx.save_state_calls == 1


def test_loot_result_vazio_nao_manda_inv_sync():
    fx = _make_loot_result_fixture()
    fx._handle_msg_loot_result({"corpse_id": 1, "coins": 0, "items": []})
    assert fx.loot_actions == []


# ── Harvestable nunca some ao esvaziar (Fase M1, revisão 4, 25/07/2026) ──────
# Bug real relatado pelo usuário: looteou a caixa (personagem A) e ela sumiu
# da TELA DELE — mas continuou visível pro personagem B, que nunca chegou a
# esvaziar o pote comum pela própria conta. Causa: _sync_local_corpse_after_take
# (chamado por _handle_msg_loot_result) remove a entidade LOCAL sempre que o
# Corpse esvazia — comportamento certo pra corpse de mob morto (deveria
# mesmo desaparecer), errado pra harvestable (permanente, no_decay=True no
# servidor) — só o personagem que esvaziou o pote passa por esse código, por
# isso o sumiço era assimétrico entre os dois clientes.

def test_loot_result_esvaziando_harvestable_nao_remove_a_entidade():
    from engine.components import Position, Renderable, Corpse
    fx = _make_loot_result_fixture()
    hv = fx.world.create_entity()
    fx.world.add_component(hv, Position(x=100, y=100, prev_x=100, prev_y=100))
    fx.world.add_component(hv, Renderable(color=(120, 90, 60), width=32, height=32,
                                          sprite_id="pr_box1"))
    fx.world.add_component(hv, Corpse(loot=[], coins=10))
    fx._available_loot[9] = {"local_eid": hv, "tx": 3, "ty": 3}

    fx._handle_msg_loot_result({"corpse_id": 9, "coins": 10, "items": []})

    assert fx.world.get_component(hv, Corpse) is not None, \
        "harvestable não deveria ser removido do ECS local ao esvaziar"
    assert 9 in fx._available_loot, \
        "harvestable esvaziado continua rastreado (pode ser reaberto, só sem loot)"


def test_loot_result_esvaziando_corpse_de_mob_morto_ainda_remove():
    """Comportamento antigo intacto: corpse de mob morto (sem Renderable)
    continua sumindo ao esvaziar — só harvestable é a exceção."""
    from engine.components import Position, Corpse
    fx = _make_loot_result_fixture()
    corpse = fx.world.create_entity()
    fx.world.add_component(corpse, Position(x=100, y=100, prev_x=100, prev_y=100))
    fx.world.add_component(corpse, Corpse(loot=[], coins=11))
    fx._available_loot[5] = {"local_eid": corpse, "tx": 3, "ty": 3}

    fx._handle_msg_loot_result({"corpse_id": 5, "coins": 11, "items": []})

    assert fx.world.get_component(corpse, Corpse) is None, \
        "corpse de mob morto deveria ser removido do ECS local ao esvaziar"
    assert 5 not in fx._available_loot


# ── ENTITY_DESPAWN (eid negativo) nunca deveria remover harvestable ──────────
# Bug real relatado pelo usuário 25/07/2026: mesmo com _sync_local_corpse_
# after_take guardado (acima), a caixa continuava sumindo pra TODO MUNDO no
# AOI ao esvaziar. Causa raiz de verdade: server/session.py::
# _handle_loot_request manda um ENTITY_DESPAWN genérico (eid negativo) pra
# QUALQUER corpse que fique realmente vazio — sem checar `no_decay` — e o
# handler client-side desse caminho (_handle_msg_entity_despawn, eid<0)
# nunca teve o guard de Renderable que os outros caminhos (_sync_local_
# corpse_after_take, LootSystem.render_world, _draw_remote_corpses) já
# tinham. Corrigido nos dois lados: servidor não manda mais esse despawn
# pra harvestable, e o cliente ganha o MESMO guard aqui como segunda camada.

def test_entity_despawn_eid_negativo_nao_remove_harvestable():
    from engine.components import Position, Renderable, Corpse
    fx = _make_loot_result_fixture()
    hv = fx.world.create_entity()
    fx.world.add_component(hv, Position(x=100, y=100, prev_x=100, prev_y=100))
    fx.world.add_component(hv, Renderable(color=(120, 90, 60), width=32, height=32,
                                          sprite_id="pr_box1"))
    fx.world.add_component(hv, Corpse(loot=[], coins=0))
    fx._available_loot[9] = {"local_eid": hv, "tx": 3, "ty": 3}

    fx._handle_msg_entity_despawn({"eid": -9})

    assert fx.world.get_component(hv, Corpse) is not None, \
        "harvestable nunca deveria ser removido por um ENTITY_DESPAWN de eid negativo"
    assert 9 in fx._available_loot


def test_entity_despawn_eid_negativo_ainda_remove_corpse_de_mob_morto():
    """Regressão: corpse de mob morto (sem Renderable) continua sumindo
    normalmente ao receber ENTITY_DESPAWN de eid negativo."""
    from engine.components import Position, Corpse
    fx = _make_loot_result_fixture()
    corpse = fx.world.create_entity()
    fx.world.add_component(corpse, Position(x=100, y=100, prev_x=100, prev_y=100))
    fx.world.add_component(corpse, Corpse(loot=[], coins=0))
    fx._available_loot[7] = {"local_eid": corpse, "tx": 3, "ty": 3}

    fx._handle_msg_entity_despawn({"eid": -7})

    assert fx.world.get_component(corpse, Corpse) is None
    assert 7 not in fx._available_loot


# ── INVENTORY_UPDATE — recompensa de item de quest (23/07/2026) ─────────────
# Reusa _grant_items_to_inventory, o mesmo helper que LOOT_RESULT usa acima
# (extraído dele nesta leva) — _handle_msg_inventory_update é só o ponto de
# entrada novo (server/session.py::_handle_quest_turn_in manda essa msg).

def test_inventory_update_credita_item_no_inventory_local():
    fx = _make_loot_result_fixture()
    from engine.components import Inventory
    fx._handle_msg_inventory_update({
        "items": [{"name": "Espada de treinamento", "stack": 1}],
    })
    inv = fx.world.get_component(fx.player_entity, Inventory)
    assert any(it is not None and it.name == "Espada de treinamento" for it in inv.items)


def test_inventory_update_com_stack_maior_que_1():
    fx = _make_loot_result_fixture()
    from engine.components import Inventory
    fx._handle_msg_inventory_update({
        "items": [{"name": "Poção de Mana", "stack": 3}],
    })
    inv = fx.world.get_component(fx.player_entity, Inventory)
    item = next(it for it in inv.items if it is not None and it.name == "Poção de Mana")
    assert item.stack == 3


# ── SKILL_GRANTED — recompensa de skill de quest (25/07/2026) ───────────────
# Servidor já gravou em learned_skill_ids do lado dele antes de mandar esta
# msg (session.py::_handle_quest_turn_in) — o cliente só materializa o
# mesmo localmente (mesma lógica de ui/trainer_system.py::_do_learn).

def _make_skill_granted_fixture():
    from engine.world import World
    from engine.components import Wallet, Inventory, PlayerSkills

    world = World()
    player = world.create_entity()
    world.add_component(player, Wallet(gold=0))
    world.add_component(player, Inventory())
    world.add_component(player, PlayerSkills())
    return _LootResultFixture(world, player)


def test_skill_granted_adiciona_a_learned_skill_ids_e_a_hotbar():
    fx = _make_skill_granted_fixture()
    from engine.components import PlayerSkills
    fx._handle_msg_skill_granted({"skill_id": "golpe_poderoso", "name": "Golpe Poderoso"})
    ps = fx.world.get_component(fx.player_entity, PlayerSkills)
    assert "golpe_poderoso" in ps.learned_skill_ids
    assert any(sk is not None and sk.skill_id == "golpe_poderoso" for sk in ps.skills)


def test_skill_granted_ja_aprendida_nao_duplica_slot():
    fx = _make_skill_granted_fixture()
    from engine.components import PlayerSkills
    ps = fx.world.get_component(fx.player_entity, PlayerSkills)
    from content.skill_config import SKILL_CATALOG
    idx = ps.skills.index(None)
    ps.skills[idx] = PlayerSkills._make_skill("golpe_poderoso", SKILL_CATALOG)
    ps.learned_skill_ids.add("golpe_poderoso")

    fx._handle_msg_skill_granted({"skill_id": "golpe_poderoso", "name": "Golpe Poderoso"})

    assert sum(1 for sk in ps.skills if sk is not None and sk.skill_id == "golpe_poderoso") == 1


def test_inventory_update_vazio_nao_quebra():
    fx = _make_loot_result_fixture()
    fx._handle_msg_inventory_update({"items": []})  # não deve levantar exceção


# ── INVENTORY_UPDATE — campo "removed" (28/07/2026, bug real relatado pelo
# usuário: item de quest entregue continuava "fantasma" na bag local, já
# que engine/quest_logic.py::complete_quest só removia do Inventory do
# SERVIDOR, nunca avisava o cliente). server/session.py::
# _handle_quest_turn_in manda "removed" na MESMA mensagem de "items".

def test_inventory_update_removed_tira_item_inteiro_da_bag_local():
    from engine.components import Inventory, Item
    fx = _make_loot_result_fixture()
    inv = fx.world.get_component(fx.player_entity, Inventory)
    item = Item("Artefato Extremamente Misterioso", "material", slot=None, max_stack=1)
    item.stack = 1
    inv.items.append(item)

    fx._handle_msg_inventory_update({
        "items": [], "removed": [{"name": "Artefato Extremamente Misterioso", "stack": 1}],
    })

    assert all(it is None or it.name != "Artefato Extremamente Misterioso" for it in inv.items)


def test_inventory_update_removed_reduz_stack_parcial():
    from engine.components import Inventory, Item
    fx = _make_loot_result_fixture()
    inv = fx.world.get_component(fx.player_entity, Inventory)
    item = Item("Presa de Lobo", "material", slot=None, max_stack=99)
    item.stack = 5
    inv.items.append(item)

    fx._handle_msg_inventory_update({
        "items": [], "removed": [{"name": "Presa de Lobo", "stack": 2}],
    })

    remaining = next(it for it in inv.items if it is not None and it.name == "Presa de Lobo")
    assert remaining.stack == 3


def test_inventory_update_removed_item_nao_presente_nao_quebra():
    fx = _make_loot_result_fixture()
    fx._handle_msg_inventory_update({
        "items": [], "removed": [{"name": "Item Que Nao Esta Na Bag", "stack": 1}],
    })  # não deve levantar exceção nem afetar nada


# ── client/network_handlers.py — spawn de player remoto propaga "level" ──────
# Bug real relatado pelo usuário 18/07/2026: nameplate de player remoto só
# atualizava o level quando o servidor reenviava HP (regen/dano), nunca no
# momento em que o player entrava na AOI — igual a posição já fazia. Causa
# raiz: WORLD_STATE/ENTITY_SPAWN/AOI_UPDATE já mandavam "level" corretamente
# (fix anterior do servidor), mas os 3 pontos que chamam
# _spawn_remote_player_entity no cliente reconstruíam o dict manualmente e
# esqueciam de repassar o campo "level" — a entidade sempre nascia com o
# default (1), só corrigido depois por um STATS_UPDATE de HP.

from client.network_handlers import NetworkHandlers as _NH
from client.remote_entity_handlers import RemoteEntityHandlers as _REH


class _NetHandlerFixture(_NH, _REH):

    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = -1
        self._remote_players = {}
        self._remote_player_move_queues = {}
        self._remote_step_timers = {}
        self._pvp_respawn_target = -1
        # Estado de mobs remotos — não usado por estes testes (só players),
        # mas _handle_msg_aoi_update/_sync_mob_effects leem incondicionalmente.
        self._remote_mobs = {}
        self._remote_mob_projectiles = {}
        self._mob_move_queues = {}
        self._pending_mob_despawn = {}
        self._mob_ghost_pos = {}
        self._pending_loot_redirect = {}
        self._remote_harvestables = {}
        self._available_loot = {}
        self._remote_corpses = {}

    def _player_world_pos(self):
        # Stub do mixin real (client/save_sync_handlers.py) — usado pelo som
        # de disparo de projétil (_spawn_mob_projectile).
        return (0.0, 0.0)


def _make_net_fixture():
    from engine.world import World
    world = World()
    player = world.create_entity()
    return _NetHandlerFixture(world, player)


def test_entity_spawn_propaga_level_do_player_remoto():
    from engine.components import RemoteControlled
    fx = _make_net_fixture()
    fx._handle_msg_entity_spawn({
        "eid": 42, "kind": "player", "tx": 5, "ty": 5,
        "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
        "level": 7,
    })
    local_eid = fx._remote_players[42]
    rc = fx.world.get_component(local_eid, RemoteControlled)
    assert rc.level == 7, "ENTITY_SPAWN deveria propagar o level pro RemoteControlled"


def test_world_state_propaga_level_do_player_remoto():
    from engine.components import RemoteControlled
    fx = _make_net_fixture()
    fx._handle_msg_world_state({"entities": [{
        "eid": 42, "kind": "player", "tx": 5, "ty": 5,
        "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
        "level": 9,
    }]})
    local_eid = fx._remote_players[42]
    rc = fx.world.get_component(local_eid, RemoteControlled)
    assert rc.level == 9, "WORLD_STATE deveria propagar o level pro RemoteControlled"


# ── STATS_UPDATE — XP/gold de instância (13/08/2026, ver
# PROBLEMAS_ARQUITETURA.md) — bug real relatado pelo usuário: barra de XP
# dentro da BG mostrava a XP de fora da instância (current_xp/
# xp_to_next_level nunca eram mandados). Junto: texto flutuante de XP
# ganho, som de level-up e texto flutuante de gold ganho, todos exclusivos
# da instância (nomes "instance_*" no payload, nunca reaproveitando o
# canal "xp_gained" do XP real).

def _make_stats_update_fixture():
    from engine.world import World
    from engine.components import CharacterStats, CombatStats, Position
    world = World()
    player = world.create_entity()
    char = CharacterStats(class_id="guerreiro")
    char.level = 1
    char.current_xp = 999
    char.xp_to_next_level = 1000
    world.add_component(player, char)
    world.add_component(player, CombatStats())
    world.add_component(player, Position(x=10.0, y=10.0))
    fx = _NetHandlerFixture(world, player)
    fx._my_eid = 1
    return fx


def test_stats_update_sobrescreve_current_xp_sem_somar():
    from engine.components import CharacterStats
    fx = _make_stats_update_fixture()
    fx._handle_msg_stats_update({
        "eid": 1, "level": 1, "current_xp": 0, "xp_to_next_level": 500,
    })
    char = fx.world.get_component(fx.player_entity, CharacterStats)
    assert char.current_xp == 0, \
        "current_xp deveria ser SOBRESCRITO (valor final do servidor), nunca somado"
    assert char.xp_to_next_level == 500


def test_stats_update_instance_xp_gained_mostra_flt_sem_alterar_current_xp():
    from engine.components import CharacterStats
    from ui.floating_text import FLT
    fx = _make_stats_update_fixture()
    flt_calls = []
    FLT.add = lambda *a, **k: flt_calls.append(a)

    fx._handle_msg_stats_update({
        "eid": 1, "level": 3, "current_xp": 30, "xp_to_next_level": 400,
        "instance_xp_gained": 30,
    })
    char = fx.world.get_component(fx.player_entity, CharacterStats)
    assert char.current_xp == 30, \
        "current_xp deveria vir do valor final (current_xp), não somar instance_xp_gained"
    assert any("30" in str(c[0]) and "XP" in str(c[0]) for c in flt_calls), \
        "deveria mostrar texto flutuante '+30 XP'"


def test_stats_update_instance_leveled_up_toca_som_de_levelup():
    import client.network_handlers as nh_mod
    fx = _make_stats_update_fixture()
    sound_calls = []
    nh_mod.SOUNDS.play_ui = lambda *a, **k: sound_calls.append(a)

    fx._handle_msg_stats_update({
        "eid": 1, "level": 2, "current_xp": 10, "xp_to_next_level": 300,
        "instance_leveled_up": True,
    })
    assert ("levelup",) in sound_calls, \
        "instance_leveled_up deveria tocar o mesmo som de level-up do mundo real"


def test_stats_update_sem_instance_leveled_up_nao_toca_som():
    import client.network_handlers as nh_mod
    fx = _make_stats_update_fixture()
    sound_calls = []
    nh_mod.SOUNDS.play_ui = lambda *a, **k: sound_calls.append(a)

    fx._handle_msg_stats_update({
        "eid": 1, "level": 1, "current_xp": 5, "xp_to_next_level": 500,
        "instance_xp_gained": 5,
    })
    assert not sound_calls, "sem level-up, não deveria tocar som nenhum"


def test_stats_update_instance_gold_gained_mostra_flt_dourado():
    from ui.floating_text import FLT
    fx = _make_stats_update_fixture()
    flt_calls = []
    FLT.add = lambda *a, **k: flt_calls.append((a, k))

    fx._handle_msg_stats_update({
        "eid": 1, "level": 1, "current_xp": 0, "xp_to_next_level": 500,
        "gold": 170, "instance_gold_gained": 20,
    })
    matches = [c for c in flt_calls if "20" in str(c[0][0]) and "g" in str(c[0][0])]
    assert matches, "deveria mostrar texto flutuante '+20g'"
    assert matches[0][0][3] == (255, 215, 0), \
        "cor deveria ser a mesma dourada padrão já usada pra ouro no resto do jogo"


def test_aoi_update_spawned_propaga_level_do_player_remoto():
    from engine.components import RemoteControlled
    fx = _make_net_fixture()
    fx._handle_msg_aoi_update({"spawned": [{
        "eid": 42, "kind": "player", "tx": 5, "ty": 5,
        "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
        "level": 4,
    }]})
    local_eid = fx._remote_players[42]
    rc = fx.world.get_component(local_eid, RemoteControlled)
    assert rc.level == 4, "AOI_UPDATE (spawned) deveria propagar o level pro RemoteControlled"


def test_aoi_update_spawned_harvestable_chama_spawn_remote_harvestable():
    """Bug real relatado pelo usuário 25/07/2026: clicou numa caixa de
    harvestable no jogo e nenhum loot apareceu. Causa: _handle_msg_aoi_update
    tem seu PRÓPRIO dispatch de "spawned", separado de
    _handle_msg_entity_spawn/_handle_msg_world_state (que já tratavam
    "harvestable" certo) — sem um branch aqui, a entidade caía no loop de
    fallback "player" e virava um jogador remoto fantasma; a entidade de
    verdade (com o Corpse que o clique direito precisa) nunca era criada.
    Este é o caminho de descoberta MAIS COMUM (o player anda até perto)."""
    from engine.components import Harvestable, Corpse
    fx = _make_net_fixture()
    fx._handle_msg_aoi_update({"spawned": [{
        "eid": 99, "kind": "harvestable", "corpse_id": 3,
        "tx": 8, "ty": 9, "name": "Caixa", "sprite_id": "pr_box1",
    }]})
    assert 99 not in fx._remote_players, \
        "harvestable não deveria virar jogador remoto fantasma"
    local_eid = fx._remote_harvestables[99]
    hv = fx.world.get_component(local_eid, Harvestable)
    corpse = fx.world.get_component(local_eid, Corpse)
    assert hv.corpse_id == 3
    assert corpse is not None
    assert fx._available_loot[3]["local_eid"] == local_eid


def test_aoi_update_despawned_harvestable_remove_entidade_local_e_available_loot():
    """Bug real relatado pelo usuário 25/07/2026: harvestable com trava de
    quest (Fase M3) não sumia de novo ao completar a quest. Causa (lado
    cliente): o campo "despawned" da PRÓPRIA AOI_UPDATE (diferente da
    mensagem ENTITY_DESPAWN avulsa) nunca tratou harvestable — só
    _remote_players/_remote_mobs. _remote_harvestables/_available_loot
    ficavam órfãos pra sempre."""
    from engine.components import Harvestable
    fx = _make_net_fixture()
    fx._handle_msg_aoi_update({"spawned": [{
        "eid": 99, "kind": "harvestable", "corpse_id": 3,
        "tx": 8, "ty": 9, "name": "Caixa", "sprite_id": "pr_box1",
    }]})
    local_eid = fx._remote_harvestables[99]

    fx._handle_msg_aoi_update({"despawned": [99]})

    assert 99 not in fx._remote_harvestables
    assert 3 not in fx._available_loot
    assert fx.world.get_component(local_eid, Harvestable) is None, \
        "entidade local do harvestable deveria ser removida do ECS"


# ── Harvestable como entidade real (Fase M1, revisão 2, 25/07/2026) ──────────
# Antes: harvestable era um dict solto em _corpses, sem colisão/Y-sort real,
# e a marca visual (elipse OU sprite) duplicava com _draw_remote_corpses.
# Agora: entidade ECS de verdade (Position+TileMovement+Renderable+
# Harvestable+Corpse vazio), sincronizada como QUALQUER entidade estacionária
# (mesma fábrica compartilhada do servidor, create_harvestable_entity).

def test_spawn_remote_harvestable_cria_entidade_com_sprite_e_corpse_vazio():
    from engine.components import Harvestable, Renderable, TileMovement, Corpse
    fx = _make_net_fixture()
    fx._spawn_remote_harvestable(77, {
        "tx": 10, "ty": 20, "name": "Caixa", "sprite_id": "pr_box1", "corpse_id": 5,
    })
    local_eid = fx._remote_harvestables[77]
    hv     = fx.world.get_component(local_eid, Harvestable)
    ren    = fx.world.get_component(local_eid, Renderable)
    tm     = fx.world.get_component(local_eid, TileMovement)
    corpse = fx.world.get_component(local_eid, Corpse)
    assert hv.corpse_id == 5
    assert ren.sprite_id == "pr_box1"
    assert tm.current_tile_x == 10 and tm.current_tile_y == 20
    assert corpse is not None and corpse.loot == [] and corpse.coins == 0
    assert fx._available_loot[5]["local_eid"] == local_eid


def test_spawn_remote_harvestable_e_idempotente():
    """Chegar duas vezes (ex: WORLD_STATE + ENTITY_SPAWN) não deveria
    criar uma segunda entidade pro mesmo server_eid."""
    fx = _make_net_fixture()
    fx._spawn_remote_harvestable(77, {
        "tx": 10, "ty": 20, "name": "Caixa", "sprite_id": "pr_box1", "corpse_id": 5,
    })
    first_local_eid = fx._remote_harvestables[77]
    fx._spawn_remote_harvestable(77, {
        "tx": 10, "ty": 20, "name": "Caixa", "sprite_id": "pr_box1", "corpse_id": 5,
    })
    assert fx._remote_harvestables[77] == first_local_eid


def test_loot_available_de_harvestable_ja_conhecido_atualiza_no_lugar():
    """Bug corrigido nesta revisão: harvestable já é uma entidade real
    (criada em _spawn_remote_harvestable, via ENTITY_SPAWN/WORLD_STATE) —
    LOOT_AVAILABLE não deve criar uma SEGUNDA entidade nem uma segunda
    marca visual (o bug relatado pelo usuário: elipse aparecendo por cima
    da sprite). Deve só atualizar o Corpse já anexado à MESMA entidade."""
    from engine.components import Corpse
    fx = _make_net_fixture()
    fx._spawn_remote_harvestable(77, {
        "tx": 10, "ty": 20, "name": "Caixa", "sprite_id": "pr_box1", "corpse_id": 5,
    })
    local_eid = fx._remote_harvestables[77]

    fx._handle_msg_loot_available({
        "corpse_id": 5, "tx": 10, "ty": 20, "coins": 10,
        "items": [{"name": "Espada de treinamento", "stack": 1}],
    })

    all_corpses = list(fx.world.get_entities_with(Corpse))
    assert len(all_corpses) == 1, "LOOT_AVAILABLE não deveria criar uma segunda entidade Corpse"
    corpse = fx.world.get_component(local_eid, Corpse)
    assert corpse.coins == 10
    assert len(corpse.loot) == 1
    assert corpse.loot[0].name == "Espada de treinamento"
    assert fx._available_loot[5]["local_eid"] == local_eid


def test_loot_available_resolve_item_por_item_id_quando_nome_nao_bate_no_catalogo():
    """Fase 4.7 (12/08/2026, ver PROBLEMAS_ARQUITETURA.md §39) — bug real
    relatado pelo usuário: flecha da Reciclagem (item construído no
    servidor sem item_id, nome vindo do subtype da aljava equipada) às
    vezes "não aparecia no loot" — reconstrução ANTIGA combinava só por
    NOME, então um nome que não batesse com nenhuma factory do catálogo
    fazia o item sumir silenciosamente da lista. Simula exatamente esse
    cenário: item_id real ("arrow") + nome que NÃO existe em nenhum
    catálogo — precisa reconstruir mesmo assim, via item_id."""
    from engine.components import Corpse
    fx = _make_net_fixture()

    fx._handle_msg_loot_available({
        "corpse_id": 42, "tx": 10, "ty": 20, "coins": 0,
        "items": [{"item_id": "arrow", "name": "Nome Que Não Existe No Catálogo",
                  "stack": 5, "item_type": "ammo"}],
    })

    local_eid = fx._available_loot[42]["local_eid"]
    corpse = fx.world.get_component(local_eid, Corpse)
    assert len(corpse.loot) == 1, \
        "item com item_id real deveria reconstruir mesmo com nome não-catalogado"
    assert corpse.loot[0].item_id == "arrow"
    assert corpse.loot[0].stack == 5


def test_entity_despawn_de_harvestable_remove_entidade_local_e_available_loot():
    """Zona de itens (25/07/2026) — diferente do harvestable de posição
    fixa (nunca despawna, só esvazia), um nó de ZONA some de verdade ao
    esgotar, reaproveitando o mesmo ENTITY_DESPAWN genérico já usado pra
    mob morto (eid >= 0). Sem tratar esse branch, a entidade local (com
    Corpse/Renderable) ficaria pra sempre na tela, órfã do servidor."""
    from engine.components import Harvestable
    fx = _make_net_fixture()
    fx._spawn_remote_harvestable(88, {
        "tx": 10, "ty": 20, "name": "Cogumelo", "sprite_id": "pl_bush3", "corpse_id": 6,
    })
    local_eid = fx._remote_harvestables[88]

    fx._handle_msg_entity_despawn({"eid": 88})

    assert 88 not in fx._remote_harvestables
    assert 6 not in fx._available_loot
    assert fx.world.get_component(local_eid, Harvestable) is None, \
        "entidade local do nó de zona deveria ser removida do ECS"


# ── client/pvp_zone_handlers.py — indicador de Zona PvP (Fase F) ─────────────
# Cosmético apenas (decisão de dano é do servidor) — cobre entrar/sair do
# retângulo disparando o log certo e ligando/desligando a flag do banner.

def test_pvp_zone_indicador_entra_e_sai():
    from client.pvp_zone_handlers import PvpZoneHandlers

    fx = PvpZoneHandlers()
    fx._load_pvp_zones({"pvp_zones": [{"name": "Arena Selvagem", "rect": (10, 10, 20, 20)}]})
    assert fx._in_pvp_zone_flag is False

    fx._update_pvp_zone_indicator(15, 15)   # dentro do rect
    assert fx._in_pvp_zone_flag is True

    fx._update_pvp_zone_indicator(15, 16)   # ainda dentro — não deveria "piscar"
    assert fx._in_pvp_zone_flag is True

    fx._update_pvp_zone_indicator(0, 0)     # fora do rect
    assert fx._in_pvp_zone_flag is False


def test_pvp_zone_sem_zonas_no_mapa_nunca_liga_flag():
    from client.pvp_zone_handlers import PvpZoneHandlers

    fx = PvpZoneHandlers()
    fx._load_pvp_zones({})   # mapa sem pvp_zones (ex: cavernas)
    fx._update_pvp_zone_indicator(15, 15)
    assert fx._in_pvp_zone_flag is False


# ── game.py::_client_pvp_context — resolver PvP client-side (Fase F) ────────
# Bug real relatado pelo usuário 18/07/2026: dentro da Zona PvP, clique
# direito no outro player abria o modal de trade/duelo em vez de atacar, e
# castar skill retornava "Alvo amigável" — o servidor já liberava
# corretamente (_pvp_allowed_between), mas o resolver PvP client-side
# (registrado em engine.faction_system) só conhecia duelo, nunca zona;
# como can_engage() do lado cliente decide ANTES de mandar qualquer coisa
# pro servidor, a ação nunca saía do cliente.

from client.duel_handlers import DuelHandlers
from client.pvp_zone_handlers import PvpZoneHandlers
from client.party_handlers import PartyHandlers
from client.arena_handlers import ArenaHandlers
from client.bg_queue_handlers import BgQueueHandlers


class _PvpCtxFixture(DuelHandlers, PvpZoneHandlers, PartyHandlers, ArenaHandlers, BgQueueHandlers):
    def __init__(self, world, player_entity, my_eid=1):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = my_eid
        self._pvp_zones = []
        self._duel_opponent_local_val = -1
        self._party_id_val = -1
        self._party_members_val = []
        self._arena_in_match_val = False
        self._arena_opponents_server_val = set()


from game import GameEngine as _GE_pvp
_PvpCtxFixture._client_pvp_context      = _GE_pvp._client_pvp_context
_PvpCtxFixture._local_eid_to_server_eid = _GE_pvp._local_eid_to_server_eid


def _make_pvp_ctx_world(rect=(10, 10, 20, 20)):
    from engine.world import World
    from engine.components import TileMovement, RemoteControlled

    world = World()
    me = world.create_entity()
    world.add_component(me, TileMovement(current_tile_x=15, current_tile_y=15))
    other = world.create_entity()
    world.add_component(other, TileMovement(current_tile_x=15, current_tile_y=15))
    world.add_component(other, RemoteControlled(server_eid=99, name="Alvo"))
    fx = _PvpCtxFixture(world, me)
    fx._pvp_zones = [{"name": "Teste", "rect": rect}]
    return fx, me, other


def test_client_pvp_context_libera_ambos_dentro_da_zona():
    fx, me, other = _make_pvp_ctx_world()
    assert fx._client_pvp_context(fx.world, me, other) is True


def test_client_pvp_context_bloqueia_fora_da_zona():
    from engine.components import TileMovement
    fx, me, other = _make_pvp_ctx_world()
    fx.world.get_component(other, TileMovement).current_tile_x = 0
    fx.world.get_component(other, TileMovement).current_tile_y = 0
    assert fx._client_pvp_context(fx.world, me, other) is False


def test_client_pvp_context_mesmo_grupo_dentro_da_zona_continua_amigavel():
    fx, me, other = _make_pvp_ctx_world()
    fx._party_id_val = 7
    fx._party_members_val = [{"eid": fx._my_eid}, {"eid": 99}]
    assert fx._client_pvp_context(fx.world, me, other) is False


def test_client_pvp_context_duelo_libera_independente_de_zona():
    fx, me, other = _make_pvp_ctx_world()
    from engine.components import TileMovement
    fx.world.get_component(other, TileMovement).current_tile_x = 0
    fx.world.get_component(other, TileMovement).current_tile_y = 0
    fx._duel_opponent_local_val = other
    assert fx._client_pvp_context(fx.world, me, other) is True


# Bug real relatado pelo usuário 20/07/2026: "não consegui atacar" dentro da
# Arena 2x2 — o servidor libera dano via Faction (arena_time_a/b), mas o
# CLIENTE nunca recebe Faction de outro player via ENTITY_SPAWN (só mob manda
# "faction"), então o resolver PvP client-side nunca sabia que o oponente da
# arena virou hostil — clique direito/SPACE/skill ficavam bloqueados mesmo
# com o servidor pronto pra liberar o dano.

def test_client_pvp_context_arena_libera_contra_oponente_da_partida():
    fx, me, other = _make_pvp_ctx_world()
    from engine.components import TileMovement
    fx.world.get_component(other, TileMovement).current_tile_x = 0
    fx.world.get_component(other, TileMovement).current_tile_y = 0
    fx._arena_in_match_val = True
    fx._arena_opponents_server_val = {99}   # server_eid do "other" (ver _make_pvp_ctx_world)
    assert fx._client_pvp_context(fx.world, me, other) is True


def test_client_pvp_context_arena_nao_libera_contra_quem_nao_e_oponente():
    fx, me, other = _make_pvp_ctx_world()
    from engine.components import TileMovement
    # Fora da zona também, senão o check de zona por si só já liberaria
    # (queremos isolar só o branch de arena aqui).
    fx.world.get_component(other, TileMovement).current_tile_x = 0
    fx.world.get_component(other, TileMovement).current_tile_y = 0
    fx._arena_in_match_val = True
    fx._arena_opponents_server_val = {12345}   # eid de outro player, não o "other"
    assert fx._client_pvp_context(fx.world, me, other) is False


def test_client_pvp_context_fora_de_partida_arena_nao_libera():
    fx, me, other = _make_pvp_ctx_world()
    from engine.components import TileMovement
    fx.world.get_component(other, TileMovement).current_tile_x = 0
    fx.world.get_component(other, TileMovement).current_tile_y = 0
    fx._arena_in_match_val = False
    fx._arena_opponents_server_val = {99}
    assert fx._client_pvp_context(fx.world, me, other) is False


# ── TAB/ESPAÇO precisam listar oponente de PvP como candidato (22/07/2026) ──
# Bug real relatado pelo usuário: TAB nunca selecionava o oponente de duelo,
# e ESPAÇO nunca conseguia iniciar auto-attack contra ele — ambos só
# olhavam o componente Enemy (mobs), nunca RemoteControlled (outro
# player). `_sync_combat_target` (client/remote_entity_handlers.py) já
# suportava mandar AUTO_ATTACK pra um alvo RemoteControlled corretamente;
# só nunca recebia um alvo remoto pra sincronizar.

def test_tab_target_inclui_oponente_de_pvp_engajavel():
    from engine.world import World
    from engine.components import Position, Visible, RemoteControlled, CombatState, PlayerControlled
    from engine.faction_system import register_pvp_context
    from ui.systems import MouseTargetingSystem

    world = World()
    me = world.create_entity()
    world.add_component(me, PlayerControlled())
    world.add_component(me, Position(x=50, y=50))
    world.add_component(me, CombatState())
    other = world.create_entity()
    world.add_component(other, Position(x=60, y=60))
    world.add_component(other, Visible())
    world.add_component(other, RemoteControlled(server_eid=99, hp=100))

    register_pvp_context(lambda w, a, b: True)   # simula duelo/arena ativo
    try:
        screen = pygame.display.get_surface()
        mts = MouseTargetingSystem(world, me, screen)
        enemies = mts._visible_enemies_sorted(0, 0)
        assert other in enemies
    finally:
        register_pvp_context(None)


def test_tab_target_nao_inclui_player_remoto_fora_de_contexto_pvp():
    """Sem duelo/arena/zona ativos, o resolver de contexto PvP não é
    registrado (ou devolve False) — outro player continua amigável e não
    deve aparecer no ciclo do TAB."""
    from engine.world import World
    from engine.components import Position, Visible, RemoteControlled, CombatState, PlayerControlled
    from engine.faction_system import register_pvp_context
    from ui.systems import MouseTargetingSystem

    world = World()
    me = world.create_entity()
    world.add_component(me, PlayerControlled())
    world.add_component(me, Position(x=50, y=50))
    world.add_component(me, CombatState())
    other = world.create_entity()
    world.add_component(other, Position(x=60, y=60))
    world.add_component(other, Visible())
    world.add_component(other, RemoteControlled(server_eid=99, hp=100))

    register_pvp_context(None)
    screen = pygame.display.get_surface()
    mts = MouseTargetingSystem(world, me, screen)
    enemies = mts._visible_enemies_sorted(0, 0)
    assert other not in enemies


def test_clique_seleciona_torre_pelo_2o_tile_do_sprite_nao_so_pelo_retangulo_antigo():
    """Bug real relatado pelo usuário (12/08/2026): clique de seleção só
    acertava o retângulo pequeno antigo (`Renderable.width/height`,
    ~40px), não a área visual real do sprite da torre (64×128px, 2 tiles
    de largura). `_enemy_at_world_pos` agora usa a caixa do sprite real
    quando `sprite_id` está setado — mesma âncora de base do
    `RenderSystem` (`ui/systems.py::RenderSystem.render`, branch
    `_sprite_rnd`)."""
    from engine.world import World
    from engine.components import Position, Renderable, Enemy, Visible, TileMovement, CombatStats
    from engine.tileset import TILE_SIZE as _TS_click_test
    from ui.systems import MouseTargetingSystem

    world = World()
    me = world.create_entity()
    world.add_component(me, Position(x=0, y=0))

    tower = world.create_entity()
    tpx, tpy = 5 * _TS_click_test + _TS_click_test // 2, 5 * _TS_click_test + _TS_click_test // 2
    world.add_component(tower, Position(x=tpx, y=tpy))
    world.add_component(tower, Renderable(color=(120, 120, 130), width=40, height=40, sprite_id="gcn_19"))
    world.add_component(tower, Enemy())
    world.add_component(tower, Visible())
    world.add_component(tower, TileMovement(current_tile_x=5, current_tile_y=5))
    world.add_component(tower, CombatStats(base_stamina=100))

    screen = pygame.display.get_surface()
    mts = MouseTargetingSystem(world, me, screen)

    # Ponto dentro do RETÂNGULO antigo (sempre deveria funcionar).
    assert mts._enemy_at_world_pos(tpx, tpy) == tower
    # Ponto no 2º tile do sprite (leste, ~48px à direita) — fora do
    # retângulo antigo de 40px, mas dentro da área visual real do sprite.
    assert mts._enemy_at_world_pos(tpx + 48, tpy) == tower, \
        "clique no 2º tile do sprite deveria selecionar a torre"


def test_space_engage_mira_oponente_de_pvp_engajavel():
    from engine.world import World
    from engine.components import (Position, TileMovement, Visible,
                                   RemoteControlled, CombatState, CombatStats,
                                   PlayerControlled)
    from engine.faction_system import register_pvp_context
    from ui.systems import PlayerInputSystem

    world = World()
    me = world.create_entity()
    world.add_component(me, PlayerControlled())
    world.add_component(me, TileMovement(current_tile_x=5, current_tile_y=5))
    my_cs = CombatStats()
    world.add_component(me, my_cs)
    my_state = CombatState()
    world.add_component(me, my_state)

    other = world.create_entity()
    # Longe o bastante (fora de PLAYER_ATTACK_RANGE) pra testar só a
    # seleção/perseguição do alvo, sem cair no branch de ataque imediato
    # (que dependeria de serviços globais registrados via register_services,
    # fora de escopo deste teste).
    world.add_component(other, Position(x=5 * 32, y=10 * 32))
    world.add_component(other, TileMovement(current_tile_x=5, current_tile_y=10))
    world.add_component(other, Visible())
    world.add_component(other, RemoteControlled(server_eid=99, hp=100))

    register_pvp_context(lambda w, a, b: True)
    try:
        screen = pygame.display.get_surface()
        pis = PlayerInputSystem(world, screen)
        pis._space_engage(me, world.get_component(me, TileMovement), my_cs, my_state, None)
        assert my_state.target_entity_id == other
        assert my_state.is_pursuing is True
    finally:
        register_pvp_context(None)


# ── ui/systems.py::PlayerInputSystem — movimento manual (WASD/clique de
# chão) desliga a perseguição de forma STICKY, nunca is_pursuing/alvo
# (28/07/2026, pedido do usuário — REVISADO 2x no mesmo dia).
#
# 1ª versão: desligava is_pursuing ao mover — quebrava o combate inteiro
# (server/combat_processor.py exige is_pursuing=True pra RANGED disparar,
# e client/remote_entity_handlers.py::_sync_combat_target manda
# AUTO_ATTACK{tid:-1} sempre que is_pursuing vira False, limpando o alvo
# NO SERVIDOR de vez — WorldServer.set_player_target).
#
# 2ª versão: parâmetro `suppress_chase` TRANSIENTE (só True enquanto a
# tecla estava fisicamente pressionada naquele frame) — usuário testou e
# reportou que a perseguição voltava assim que soltava as teclas entre
# um passo e outro (padrão comum de jogo em grade: toque curto por
# tile). Esclareceu a intenção real: mover deve desligar a perseguição
# de vez, e ela só deve VOLTAR num reengajamento de propósito (clique
# direito no alvo, Espaço ou skill) — nunca sozinha ao parar de andar.
#
# Fix de verdade: `CombatState.chase_suppressed` (engine/components.py)
# — bool STICKY, client-side puro (nunca lido no servidor/rede).
# Movimento manual (WASD ou clique de chão) seta True. TODO ponto que já
# seta is_pursuing=True (clique direito no alvo, Espaço, início/fim de
# skill ofensiva — 8 lugares ao todo, ui/systems.py + skill_handlers.py +
# spell_system.py + save_sync_handlers.py) agora TAMBÉM zera
# chase_suppressed=False junto. `_process_target`/`_process_archer_combat`
# leem `combat_state.chase_suppressed` direto (não é mais parâmetro) pra
# bloquear só as chamadas de `_auto_move_step` — nunca o ataque em si,
# que continua disparando sempre que alcance+cooldown forem satisfeitos
# (igual arqueiro sempre fez, nunca dependeu de tile_movement.is_moving).

class _FakeKeys:
    """Substitui pygame.key.get_pressed() nos testes — indexável por
    constante de tecla (pygame.K_*), todas False exceto as informadas."""
    def __init__(self, pressed: set):
        self._pressed = pressed

    def __getitem__(self, key):
        return key in self._pressed


def _make_wasd_pursuit_fixture():
    from engine.world import World
    from engine.components import (Position, TileMovement, PlayerControlled,
                                   CombatStats, CombatState, PlayerAutoMove, Visible)
    import engine.world_systems as ws_mod

    class _FakeTileValidation:
        def is_tile_walkable(self, entity_id, tx, ty, from_tx=None,
                             from_ty=None, ignore_eid=-1):
            return True

    class _FakePathfinding:
        def find_path(self, start, end, dynamic_obstacles=None,
                       max_nodes=300, manhattan_limit=60):
            sx, sy = start
            ex, ey = end
            nx = sx + (1 if ex > sx else (-1 if ex < sx else 0))
            ny = sy + (1 if ey > sy else (-1 if ey < sy else 0))
            return [(nx, ny)]

        def _get_tilemap_component(self):
            return None

    ws_mod._svc_resolver = None
    ws_mod.register_services(tile_validation=_FakeTileValidation(),
                             pathfinding=_FakePathfinding())

    world = World()
    eid = world.create_entity()
    world.add_component(eid, Position(x=5 * 32, y=5 * 32))
    tm = TileMovement(current_tile_x=5, current_tile_y=5)
    world.add_component(eid, tm)
    world.add_component(eid, PlayerControlled())
    world.add_component(eid, CombatStats())
    cs = CombatState()
    world.add_component(eid, cs)
    world.add_component(eid, PlayerAutoMove())

    # Alvo vivo e visível, LONGE (fora de PLAYER_ATTACK_RANGE) — passa nas
    # validações de _process_target (alvo morto/fora de visão) sem cair no
    # branch de ataque nem exigir pathfinding real (dist > range só faria
    # a perseguição chamar _auto_move_step, que este teste quer provar que
    # NÃO roda mais quando is_pursuing=False).
    target = world.create_entity()
    world.add_component(target, Position(x=5 * 32, y=20 * 32))
    world.add_component(target, TileMovement(current_tile_x=5, current_tile_y=20))
    world.add_component(target, CombatStats())
    world.add_component(target, Visible())
    cs.target_entity_id = target
    cs.is_pursuing = True

    return world, eid, tm, cs, target


def test_wasd_liga_chase_suppressed_sem_mexer_em_is_pursuing_nem_alvo():
    """WASD nunca deve tocar is_pursuing/target_entity_id — só liga
    chase_suppressed (persistente)."""
    from ui.systems import PlayerInputSystem

    world, eid, tm, cs, target = _make_wasd_pursuit_fixture()
    sys_input = PlayerInputSystem(world, screen=None)

    orig_get_pressed = pygame.key.get_pressed
    pygame.key.get_pressed = lambda: _FakeKeys({pygame.K_d})
    try:
        sys_input.update([], dt=0.1)
    finally:
        pygame.key.get_pressed = orig_get_pressed

    assert cs.is_pursuing is True, "movimento manual nunca deve desligar is_pursuing"
    assert cs.target_entity_id == target, "alvo deve continuar selecionado (combate mantido)"
    assert cs.chase_suppressed is True, "movimento manual deve ligar chase_suppressed"


def test_chase_suppressed_continua_true_depois_de_soltar_as_teclas():
    """Núcleo do fix (2ª revisão): chase_suppressed é STICKY — soltar as
    teclas de movimento NÃO reativa a perseguição sozinha (pedido
    explícito do usuário: só reativa com reengajamento de propósito)."""
    from ui.systems import PlayerInputSystem

    world, eid, tm, cs, target = _make_wasd_pursuit_fixture()
    sys_input = PlayerInputSystem(world, screen=None)

    orig_get_pressed = pygame.key.get_pressed
    try:
        pygame.key.get_pressed = lambda: _FakeKeys({pygame.K_d})
        sys_input.update([], dt=0.1)   # liga chase_suppressed
        tm.is_moving = False           # simula o passo tendo terminado

        pygame.key.get_pressed = lambda: _FakeKeys(set())   # solta a tecla
        sys_input.update([], dt=0.1)
    finally:
        pygame.key.get_pressed = orig_get_pressed

    assert cs.chase_suppressed is True, \
        "soltar as teclas não deveria reativar a perseguição sozinha"
    assert not tm.is_moving, \
        "sem chase_suppressed=False, nenhum auto-move de perseguição deveria iniciar"


def test_process_target_suprime_auto_move_step_com_chase_suppressed():
    """chase_suppressed=True bloqueia só o auto-move de perseguição
    (_auto_move_step, via find_path) — is_pursuing e o alvo nunca são
    tocados, e nenhum movimento de perseguição é iniciado."""
    from ui.systems import PlayerInputSystem
    from engine.components import Position, PlayerAutoMove, CombatStats

    world, eid, tm, cs, target = _make_wasd_pursuit_fixture()
    cs.chase_suppressed = True
    sys_input = PlayerInputSystem(world, screen=None)
    position = world.get_component(eid, Position)
    combat_stats = world.get_component(eid, CombatStats)
    auto_move = world.get_component(eid, PlayerAutoMove)

    sys_input._process_target(eid, position, tm, combat_stats, cs, auto_move,
                              can_act=True, can_move=True, dt=0.1)

    assert cs.is_pursuing is True
    assert cs.target_entity_id == target
    assert not tm.is_moving, "perseguição suprimida não deveria iniciar movimento"


def test_process_target_persegue_normalmente_sem_chase_suppressed():
    """Regressão: com chase_suppressed=False (comportamento de sempre),
    alvo fora de alcance dispara o auto-move de perseguição normalmente."""
    from ui.systems import PlayerInputSystem
    from engine.components import Position, PlayerAutoMove, CombatStats

    world, eid, tm, cs, target = _make_wasd_pursuit_fixture()
    sys_input = PlayerInputSystem(world, screen=None)
    position = world.get_component(eid, Position)
    combat_stats = world.get_component(eid, CombatStats)
    auto_move = world.get_component(eid, PlayerAutoMove)

    sys_input._process_target(eid, position, tm, combat_stats, cs, auto_move,
                              can_act=True, can_move=True, dt=0.1)

    assert tm.is_moving, "sem chase_suppressed, perseguição deveria iniciar movimento (alvo fora de alcance)"


def test_reengajar_com_espaco_reativa_chase_suppressed_false():
    """Reengajamento de propósito (Espaço) zera chase_suppressed, mesmo
    que o movimento manual tenha ligado antes."""
    from ui.systems import PlayerInputSystem
    from engine.components import (Position, PlayerAutoMove, CombatStats,
                                   Enemy, AIControlled, Visible)

    world, eid, tm, cs, target = _make_wasd_pursuit_fixture()
    cs.chase_suppressed = True   # simula ter andado manualmente antes
    sys_input = PlayerInputSystem(world, screen=None)
    combat_stats = world.get_component(eid, CombatStats)
    auto_move = world.get_component(eid, PlayerAutoMove)

    # _space_engage seleciona o inimigo mais próximo entre entidades
    # (Position, Enemy, AIControlled, TileMovement, CombatStats, Visible)
    # — `target` (criado por _make_wasd_pursuit_fixture) não tem Enemy/
    # AIControlled, então um mob novo, mais simples, é criado aqui.
    # Fora de PLAYER_ATTACK_RANGE (1 tile) — evita o branch de ataque
    # imediato (exigiria o serviço 'combat' registrado, fora de escopo).
    from engine.components import TileMovement as _TM_sp
    mob = world.create_entity()
    world.add_component(mob, Position(x=5 * 32, y=10 * 32))
    world.add_component(mob, Enemy())
    world.add_component(mob, AIControlled())
    world.add_component(mob, _TM_sp(current_tile_x=5, current_tile_y=10))
    world.add_component(mob, CombatStats())
    world.add_component(mob, Visible())

    sys_input._space_engage(eid, tm, combat_stats, cs, auto_move)

    assert cs.target_entity_id == mob
    assert cs.chase_suppressed is False


def test_offline_sem_requester_continua_creditando_local():
    """Regressão: sem set_online_loot_requester (modo legado/offline), o
    fluxo antigo — creditar na hora do clique — continua intacto."""
    from ui.systems import LootSystem
    from engine.components import Wallet, Corpse
    world, player, corpse = _make_loot_world()
    screen = pygame.display.get_surface()
    loot = LootSystem(world, screen, player_entity=player)

    result = _click_gold_row(loot, corpse)

    wallet = world.get_component(player, Wallet)
    assert result is True
    assert wallet.gold == 11


# ── ui/spell_system.py — Recarregar não consome bag no cliente (online) ──────
# Bug real relatado pelo usuário 20/07/2026: comprou 200 flechas, recarregou
# aljava de limite 75, mochila perdeu 150 (o dobro). _complete_cast despachava
# _apply_recarregar (mutação REAL bag→aljava) no ramo visual_only achando
# seguro pelo guard de target_cs — guard que nunca protege Recarregar (é
# auto-alvo). O resultado real deve vir só do servidor; aqui o cliente só
# preenche a barra de cast, nunca mexe em bag/aljava.

def test_recarregar_visual_only_nao_mexe_em_bag_nem_aljava_online():
    from engine.world import World
    from engine.components import (
        Equipment, Inventory, Item, SpellCast, CombatState, CharacterStats,
    )
    from ui.spell_system import SpellCastSystem

    world = World()
    player = world.create_entity()

    quiver = Item("Aljava", "quiver", "offhand", arrow_count=0, max_arrows=75)
    equip = Equipment()
    equip.slots["offhand"] = quiver
    world.add_component(player, equip)

    ammo = Item("Flecha", "ammo", "", max_stack=999)
    ammo.stack = 200
    inv = Inventory(items=[ammo])
    world.add_component(player, inv)

    world.add_component(player, CharacterStats())
    combat_state = CombatState()
    world.add_component(player, combat_state)

    screen = pygame.display.get_surface()
    sys_spell = SpellCastSystem(world, screen)

    spell_cast = SpellCast(spell_id="recarregar", cast_time=1.0,
                           target_id=player, visual_only=True)
    sys_spell._complete_cast(player, spell_cast, combat_state)

    assert quiver.arrow_count == 0, "cliente não deve encher a aljava — só o servidor"


# ── ui/systems.py::PlayerInputSystem — CC bloqueia TODA movimentação ─────────
# Bug real relatado pelo usuário 21/07/2026: alvo sob polimorfia (e, pelo
# mesmo mecanismo, desorientado/sono) não conseguia andar com WASD, mas
# continuava andando via clique do mouse (clique de chão) — is_action_locked
# só era consultado no branch de teclado (can_move). Correção: can_move passa
# a ser encaminhado também pra _process_ground_move/_process_follow/chase,
# isolando QUALQUER movimento durante CC, igual já acontecia com o teclado.
def _make_cc_move_fixture():
    from engine.world import World
    from engine.components import (
        Position, TileMovement, PlayerControlled, CombatStats,
        PlayerAutoMove, StatusEffects,
    )
    import engine.world_systems as ws_mod

    class _FakePathfinding:
        def find_path(self, start, end, dynamic_obstacles=None,
                       max_nodes=300, manhattan_limit=60):
            sx, sy = start
            ex, ey = end
            nx = sx + (1 if ex > sx else (-1 if ex < sx else 0))
            ny = sy + (1 if ey > sy else (-1 if ey < sy else 0))
            return [(nx, ny)]

        def _get_tilemap_component(self):
            return None

    class _FakeTileValidation:
        def is_tile_walkable(self, entity_id, tx, ty, from_tx=None,
                             from_ty=None, ignore_eid=-1):
            return True

    # _svc_resolver é global de módulo — se um teste de servidor (WorldServer)
    # rodou antes na mesma sessão do pytest, ele fica setado e is_tile_walkable
    # tenta resolver o bundle daquele OUTRO mundo primeiro, ignorando o fake
    # abaixo. Reseta pra None (equivalente a "modo offline/sem resolver") pra
    # isolar este teste de estado global vazado por ordem de execução.
    ws_mod._svc_resolver = None
    ws_mod.register_services(pathfinding=_FakePathfinding(),
                             tile_validation=_FakeTileValidation())

    world = World()
    eid = world.create_entity()
    world.add_component(eid, Position(x=5 * 32, y=5 * 32))
    tm = TileMovement(current_tile_x=5, current_tile_y=5)
    world.add_component(eid, tm)
    world.add_component(eid, PlayerControlled())
    world.add_component(eid, CombatStats())
    auto_move = PlayerAutoMove()
    auto_move.active = True
    auto_move.ground_target = (10, 5)
    world.add_component(eid, auto_move)
    sfx = StatusEffects()
    world.add_component(eid, sfx)

    return world, eid, tm, auto_move, sfx


def test_clique_de_chao_move_normalmente_sem_cc():
    from ui.systems import PlayerInputSystem

    world, eid, tm, auto_move, sfx = _make_cc_move_fixture()
    sys_input = PlayerInputSystem(world, screen=None)
    sys_input.update([], dt=0.1)

    assert auto_move.path or tm.is_moving, "sem CC, clique de chão deve mover"


def test_polimorfia_bloqueia_clique_de_chao_igual_teclado():
    from ui.systems import PlayerInputSystem

    world, eid, tm, auto_move, sfx = _make_cc_move_fixture()
    sfx.effects["polymorph"] = object()
    sys_input = PlayerInputSystem(world, screen=None)
    sys_input.update([], dt=0.1)

    assert tm.current_tile_x == 5 and tm.current_tile_y == 5
    assert not tm.is_moving
    assert not auto_move.path, "path não deve nem ser calculado sob CC"


def test_desorientado_bloqueia_clique_de_chao():
    from ui.systems import PlayerInputSystem

    world, eid, tm, auto_move, sfx = _make_cc_move_fixture()
    sfx.effects["disoriented"] = object()
    sys_input = PlayerInputSystem(world, screen=None)
    sys_input.update([], dt=0.1)

    assert tm.current_tile_x == 5 and tm.current_tile_y == 5
    assert not tm.is_moving


def test_medo_bloqueia_clique_de_chao():
    """Medo (talento "Horrorizante" do Executar) não estava em
    is_action_locked — um player amedrontado tinha zero restrição de
    movimento/ação. Adicionado ao mesmo choke-point (engine/utils.py)
    usado por polimorfia/desorientado, pedido explícito do usuário."""
    from ui.systems import PlayerInputSystem

    world, eid, tm, auto_move, sfx = _make_cc_move_fixture()
    sfx.effects["fear"] = object()
    sys_input = PlayerInputSystem(world, screen=None)
    sys_input.update([], dt=0.1)

    assert tm.current_tile_x == 5 and tm.current_tile_y == 5
    assert not tm.is_moving


def test_stun_bloqueia_clique_de_chao():
    """Bug real: "stun" já existia como StatusEffects de verdade (aplicado
    por Interceptar/Punho no Queixo/knockback), mas nenhum gate de
    movimento olhava pra ele — nem is_action_locked (antigo) nem, por
    consequência, o roteamento de can_move pro clique de chão. Generalizado
    via EFFECT_DEFS[*].blocks_move/blocks_act (content/status_effects_data.py)."""
    from ui.systems import PlayerInputSystem

    world, eid, tm, auto_move, sfx = _make_cc_move_fixture()
    sfx.effects["stun"] = object()
    sys_input = PlayerInputSystem(world, screen=None)
    sys_input.update([], dt=0.1)

    assert tm.current_tile_x == 5 and tm.current_tile_y == 5
    assert not tm.is_moving


# ── client/remote_entity_handlers.py::_draw_remote_players — hostilidade e
# nameplate (revisado 22/07/2026): hostilidade agora é decidida por
# game.py::_client_pvp_context (duelo OU zona PvP OU arena, já exclui mesmo
# grupo) em vez de checar duelo/arena manualmente — mesma fonte única usada
# por clique/skill. Nameplate de morto (QUALQUER contexto, não só arena)
# nunca mais some por completo: só a barra de HP/badge de nível somem, o
# nome continua sempre visível (bug real relatado pelo usuário 22/07/2026).
def _make_remote_player_fixture(hp: int = 80):
    from engine.world import World
    from engine.components import Position, RemoteControlled, TileMovement
    import client.remote_entity_handlers as reh_mod
    from client.duel_handlers import DuelHandlers
    from client.pvp_zone_handlers import PvpZoneHandlers
    from client.party_handlers import PartyHandlers
    from client.arena_handlers import ArenaHandlers
    from client.bg_queue_handlers import BgQueueHandlers
    from game import GameEngine as _GE_rp

    class _Fixture(reh_mod.RemoteEntityHandlers, DuelHandlers, PvpZoneHandlers,
                   PartyHandlers, ArenaHandlers, BgQueueHandlers):
        def __init__(self, world, player_entity):
            self.world = world
            self.player_entity = player_entity
            self._my_eid = 1
            self._pvp_zones = []
            self._duel_opponent_local_val = -1
            self._party_id_val = -1
            self._party_members_val = []
            self._arena_in_match_val = False
            self._arena_opponents_server_val = set()
            self._bg_in_match_val = False
            self._bg_opponents_server_val = set()
            self._nameplate_mode = 0
            from ui.fonts import make as _make_font
            self.font_sm = _make_font(12)

    _Fixture._client_pvp_context      = _GE_rp._client_pvp_context
    _Fixture._local_eid_to_server_eid = _GE_rp._local_eid_to_server_eid

    world = World()
    me = world.create_entity()
    world.add_component(me, TileMovement(current_tile_x=0, current_tile_y=0))
    local_eid = world.create_entity()
    world.add_component(local_eid, Position(x=100.0, y=100.0))
    world.add_component(local_eid, TileMovement(current_tile_x=0, current_tile_y=0))
    world.add_component(local_eid, RemoteControlled(
        server_eid=42, name="Fulano", hp=hp, hp_max=100, level=5))
    fx = _Fixture(world, me)
    fx._remote_players = {42: local_eid}
    return fx


def _pending_text_colors():
    from ui.world_labels import WORLD_LABELS
    colors = set()
    for _, _, surf, _ in WORLD_LABELS._pending:
        for x in range(surf.get_width()):
            for y in range(surf.get_height()):
                a = surf.get_at((x, y))
                if a.a > 0:
                    colors.add((a.r, a.g, a.b))
    return colors


def test_arena_oponente_fica_hostil_igual_duelo():
    from ui.world_labels import WORLD_LABELS
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    fx = _make_remote_player_fixture()
    fx._arena_in_match_val = True
    fx._arena_opponents_server_val = {42}
    fx._draw_remote_players(0.0, 0.0)
    assert (255, 90, 90) in _pending_text_colors(), "oponente de arena deveria ficar vermelho"


def test_arena_proprio_time_nao_fica_hostil():
    from ui.world_labels import WORLD_LABELS
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    fx = _make_remote_player_fixture()
    fx._arena_in_match_val = True
    fx._arena_opponents_server_val = set()   # 42 NÃO é oponente — é do próprio time
    fx._draw_remote_players(0.0, 0.0)
    colors = _pending_text_colors()
    assert (255, 90, 90) not in colors, "companheiro de time não deve ficar hostil"
    assert (255, 255, 200) in colors


def test_nameplate_de_morto_mostra_so_o_nome():
    from ui.world_labels import WORLD_LABELS
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    fx = _make_remote_player_fixture(hp=0)
    fx._draw_remote_players(0.0, 0.0)
    assert len(WORLD_LABELS._pending) == 1, "morto: só o nome, sem barra de HP/badge de nível"


def test_nameplate_de_vivo_mostra_barra_e_nome():
    from ui.world_labels import WORLD_LABELS
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    fx = _make_remote_player_fixture(hp=80)
    fx._draw_remote_players(0.0, 0.0)
    assert len(WORLD_LABELS._pending) == 2, "vivo: barra de HP + nome"


def test_nameplate_mode_1_player_remoto_mostra_so_o_nome():
    """Shift+V modo 1 (29/07/2026): "a única coisa que permanecerá será
    o nome" — vale pra player remoto igual mob/NPC."""
    from ui.world_labels import WORLD_LABELS
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    fx = _make_remote_player_fixture(hp=80)
    fx._nameplate_mode = 1
    fx._draw_remote_players(0.0, 0.0)
    assert len(WORLD_LABELS._pending) == 1, "modo 1: só o nome, sem badge/barra"


def test_nameplate_mode_2_player_remoto_mostra_barra_simples_sem_png():
    """Shift+V modo 2: nome + barra de HP "gerada pelo jogo" — ainda 2
    elementos (bar+nome) igual modo 0, mas a Surface da barra tem que vir
    de build_simple_hp_bar (sem PNG/badge), não de build_mob_hud."""
    from ui.world_labels import WORLD_LABELS
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    fx = _make_remote_player_fixture(hp=80)
    fx._nameplate_mode = 2
    fx._draw_remote_players(0.0, 0.0)
    assert len(WORLD_LABELS._pending) == 2, "modo 2: barra simples + nome"
    from ui.hud_bars import M_SIZE, P_SIZE, SCALE
    _icon_size = WORLD_LABELS._pending[0][2].get_size()
    _png_sizes = {(M_SIZE[0] * SCALE, M_SIZE[1] * SCALE), (P_SIZE[0] * SCALE, P_SIZE[1] * SCALE)}
    assert _icon_size not in _png_sizes, \
        "modo 2 não deveria usar o tamanho nativo de nenhum asset PNG (build_mob_hud/build_player_hud)"


def test_zona_pvp_deixa_hostil_sem_ser_duelo_arena():
    """A7 (22/07/2026): dentro de zona PvP, sem ser duelo/arena/mesmo
    grupo, o outro player também deve ficar vermelho — cobertura nova,
    já que antes desta fase a função nem olhava zona PvP."""
    from ui.world_labels import WORLD_LABELS
    from engine.components import TileMovement
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    fx = _make_remote_player_fixture()
    fx._pvp_zones = [{"name": "Teste", "rect": (0, 0, 5, 5)}]
    fx.world.get_component(fx.player_entity, TileMovement).current_tile_x = 2
    fx.world.get_component(fx.player_entity, TileMovement).current_tile_y = 2
    fx.world.get_component(fx._remote_players[42], TileMovement).current_tile_x = 2
    fx.world.get_component(fx._remote_players[42], TileMovement).current_tile_y = 2
    fx._draw_remote_players(0.0, 0.0)
    assert (255, 90, 90) in _pending_text_colors(), "dentro da zona PvP deveria ficar vermelho"


# ── client/remote_entity_handlers.py::_spawn_remote_mob — NPC de serviço
# ganha componente de capacidade (Fase 1 de combate genérico, 21/07/2026)
def test_spawn_remote_mob_de_mercador_anexa_npc_e_merchant():
    from engine.components import NPC, Merchant
    fx = _make_net_fixture()
    fx._handle_msg_entity_spawn({
        "eid": 99, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Guerreiro (NPC)", "entity_class": "Warrior",
        "hp": 60, "hp_max": 60, "level": 1, "faction": "civis",
        "name": "Zeca", "profession": "Comerciante", "shop_id": "general",
    })
    local_eid = fx._remote_mobs[99]
    npc = fx.world.get_component(local_eid, NPC)
    assert npc is not None and npc.profession == "Comerciante"
    merch = fx.world.get_component(local_eid, Merchant)
    assert merch is not None and merch.shop_id == "general"


def test_spawn_remote_mob_de_trainer_anexa_trainer_com_class_id():
    from engine.components import Trainer
    fx = _make_net_fixture()
    fx._handle_msg_entity_spawn({
        "eid": 98, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Mago (NPC)", "entity_class": "Mage",
        "hp": 40, "hp_max": 40, "level": 1, "faction": "civis",
        "name": "Arcanista", "profession": "Treinador", "class_id": "mago",
    })
    local_eid = fx._remote_mobs[98]
    trainer = fx.world.get_component(local_eid, Trainer)
    assert trainer is not None and trainer.class_id == "mago"


def _find_cosmetic_projectile(fx):
    """Único PlayerProjectile cosmético (target_server_id=-3) no world."""
    from engine.components import PlayerProjectile
    projs = [pp for _, pp in fx.world.get_entities_with(PlayerProjectile)
             if pp.target_server_id == -3]
    return projs[0] if projs else None


def test_spawn_mob_projectile_com_alvo_sendo_mob_remoto():
    """Bug real relatado pelo usuário 21/07/2026: tiro de mob/NPC mirando
    um NÃO-player era descartado (target_seid só resolvia player). Além do
    fix de resolução, o projétil agora nasce como PlayerProjectile — o
    MESMO visual do arqueiro jogador (rastro + rotação por movimento),
    silencioso (target_server_id=-3; sons vêm de NpcSounds em outros
    pontos), pedido do usuário 21/07/2026."""
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 50, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Zumbi", "entity_class": "Warrior",
        "hp": 30, "hp_max": 30, "level": 1, "faction": "monstros_hostis",
    })
    mob_local_eid = fx._remote_mobs[50]

    fx._spawn_mob_projectile(777, {
        "x": 100.0, "y": 100.0, "target_seid": 50,
        "color": [200, 160, 60], "is_arrow": True,
        "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
    })

    proj = _find_cosmetic_projectile(fx)
    assert proj is not None, "projétil deveria ser criado quando o alvo é um mob remoto"
    assert proj.target_id == mob_local_eid
    assert proj.spell_id == "arrow", "flecha de mob/NPC deve usar o visual da flecha do player"
    assert proj.speed == 700.0
    assert proj.color == (101, 67, 33)


def test_projetil_de_mago_npc_usa_visual_de_bola_de_fogo():
    """Caster Mage/Mago: projétil idêntico à Bola de Fogo do mago jogador
    (spritesheet animado, keyed por spell_id="bola_de_fogo")."""
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 70, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Mago (NPC)", "entity_class": "Mage", "is_ranged": True,
        "hp": 40, "hp_max": 40, "level": 1, "faction": "civis",
        "name": "Arcanista", "profession": "Treinador",
    })
    fx._handle_msg_entity_spawn({
        "eid": 71, "kind": "enemy", "tx": 7, "ty": 5,
        "race": "Zumbi", "entity_class": "Warrior",
        "hp": 30, "hp_max": 30, "level": 1, "faction": "monstros_hostis",
    })
    fx._spawn_mob_projectile(801, {
        "x": 160.0, "y": 160.0, "attacker_seid": 70, "target_seid": 71,
        "color": [255, 80, 0], "is_arrow": False,
        "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
    })
    proj = _find_cosmetic_projectile(fx)
    assert proj is not None
    assert proj.spell_id == "bola_de_fogo"
    assert proj.speed == 300.0


def test_projetil_de_caster_nao_mago_mantem_cor_do_servidor():
    """Warlock/atacante fora do AOI: círculo mágico genérico do pipeline
    do player, mantendo cor/velocidade do servidor (identidade visual do
    Vampiro roxo preservada)."""
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 72, "kind": "enemy", "tx": 7, "ty": 5,
        "race": "Zumbi", "entity_class": "Warrior",
        "hp": 30, "hp_max": 30, "level": 1, "faction": "monstros_hostis",
    })
    fx._spawn_mob_projectile(802, {
        "x": 160.0, "y": 160.0, "attacker_seid": -1, "target_seid": 72,
        "color": [160, 0, 220], "is_arrow": False,
        "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
    })
    proj = _find_cosmetic_projectile(fx)
    assert proj is not None
    assert proj.spell_id == "npc_bolt"
    assert proj.color == (160, 0, 220)
    assert proj.speed == 380.0


def _record_npc_sound_calls():
    """Monkeypatch de SOUNDS.play_mob_sounds_at que grava (evento, base_key)
    de cada chamada — restaurar com o retorno (função original)."""
    from ui.sound_manager import SOUNDS
    calls = []
    original = SOUNDS.play_mob_sounds_at

    def recorder(comp, event, *a, **kw):
        calls.append((event, getattr(comp, event, "") if comp else ""))

    SOUNDS.play_mob_sounds_at = recorder
    return calls, original


def test_som_de_impacto_do_arqueiro_npc_e_arrow_impact():
    """Decisão do usuário 21/07/2026 (escopo genérico): golpe de atacante
    NÃO-player contra alvo mob toca o som do PRÓPRIO atacante — ranged usa
    o campo novo attack_impact ("arrow_impact" no Arqueiro (NPC), mesmo som
    do arqueiro jogador) em vez do hit_normal fixo de antes."""
    from ui.sound_manager import SOUNDS
    fx = _make_net_fixture()
    fx._handle_msg_entity_spawn({
        "eid": 60, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Arqueiro (NPC)", "entity_class": "Hunter", "is_ranged": True,
        "hp": 45, "hp_max": 45, "level": 1, "faction": "civis",
        "name": "Andre", "profession": "Treinador", "class_id": "arqueiro",
    })
    calls, original = _record_npc_sound_calls()
    try:
        played = fx._play_nonplayer_attack_impact(60, 100.0, 100.0, 0.0, 0.0)
    finally:
        SOUNDS.play_mob_sounds_at = original
    assert played, "atacante NPC ranged com attack_impact configurado deveria tocar som próprio"
    assert ("attack_impact", "arrow_impact") in calls


def test_som_de_disparo_toca_quando_projetil_de_npc_nasce():
    """Som de disparo ("arrow_release") toca no momento em que o projétil
    nasce — igual ao arqueiro jogador — quando o alvo NÃO é o player local
    (mob→player mantém o comportamento antigo, som na chegada do
    COMBAT_RESULT)."""
    from ui.sound_manager import SOUNDS
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 61, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Arqueiro (NPC)", "entity_class": "Hunter", "is_ranged": True,
        "hp": 45, "hp_max": 45, "level": 1, "faction": "civis",
        "name": "Andre", "profession": "Treinador",
    })
    fx._handle_msg_entity_spawn({
        "eid": 62, "kind": "enemy", "tx": 7, "ty": 5,
        "race": "Zumbi", "entity_class": "Warrior",
        "hp": 30, "hp_max": 30, "level": 1, "faction": "monstros_hostis",
    })
    calls, original = _record_npc_sound_calls()
    try:
        fx._spawn_mob_projectile(800, {
            "x": 160.0, "y": 160.0, "attacker_seid": 61, "target_seid": 62,
            "color": [200, 160, 60], "is_arrow": True,
            "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
        })
    finally:
        SOUNDS.play_mob_sounds_at = original
    assert ("attack_ranged", "arrow_release") in calls, \
        "disparo do NPC ranged deveria tocar o som de attack_ranged (arrow_release) ao nascer o projétil"


def test_som_de_lancamento_e_impacto_do_mago_npc_espelham_bola_de_fogo():
    """Feedback do usuário 21/07/2026: impacto do mago tocava hit_normal
    (som de melee). Agora: lançamento (attack_magic =
    skill_bola_de_fogo_launch) quando o projétil nasce + impacto
    (attack_impact = skill_bola_de_fogo_impact) na chegada do golpe —
    mesmos arquivos da Bola de Fogo do mago jogador."""
    from ui.sound_manager import SOUNDS
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 80, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Mago (NPC)", "entity_class": "Mage", "is_ranged": True,
        "hp": 40, "hp_max": 40, "level": 1, "faction": "civis",
        "name": "Selene", "profession": "Treinador",
    })
    fx._handle_msg_entity_spawn({
        "eid": 81, "kind": "enemy", "tx": 7, "ty": 5,
        "race": "Zumbi", "entity_class": "Warrior",
        "hp": 30, "hp_max": 30, "level": 1, "faction": "monstros_hostis",
    })
    calls, original = _record_npc_sound_calls()
    try:
        fx._spawn_mob_projectile(803, {
            "x": 160.0, "y": 160.0, "attacker_seid": 80, "target_seid": 81,
            "color": [255, 80, 0], "is_arrow": False,
            "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
        })
        played_impact = fx._play_nonplayer_attack_impact(80, 100.0, 100.0, 0.0, 0.0)
    finally:
        SOUNDS.play_mob_sounds_at = original
    assert ("attack_magic", "skill_bola_de_fogo_launch") in calls, \
        "lançamento do mago NPC deveria tocar o som de launch da Bola de Fogo"
    assert played_impact
    assert ("attack_impact", "skill_bola_de_fogo_impact") in calls, \
        "impacto do mago NPC deveria tocar o som de impact da Bola de Fogo (não hit_normal)"


# ── Torre (29/07/2026) — mesmo mecanismo de "Mago (NPC)"/"Arqueiro (NPC)"
# acima, mas exercitando o bug real encontrado: `race` de uma torre
# ("torre_de_fogo"/"torre_de_flechas") só existe em content/tower_
# definitions.py::TOWER_TABLE, NUNCA em MOB_TABLE — sem o merge de
# fallback em engine/entity_factory.py::_build_combat_entity
# (`MOB_TABLE.get(race) or TOWER_TABLE.get(race)`), o cliente reconstruía
# a torre com `entity_class` genérico ERRADO (sempre "Arqueiro", nunca
# "Mago" de verdade) e `NpcSounds` TOTALMENTE vazio (mob_def=None) —
# visual sempre caía no círculo genérico "npc_bolt" e nenhum som tocava
# pra torre de fogo (arrow ainda "funcionava" por coincidência, já que o
# fallback genérico ranged também é "Arqueiro").

def test_projetil_de_torre_de_fogo_usa_visual_de_bola_de_fogo():
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 90, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "torre_de_fogo", "entity_class": "Mago", "is_ranged": True,
        "hp": 4000, "hp_max": 4000, "level": 5, "faction": "monstros_hostis",
        "name": "Torre de Fogo",
    })
    fx._handle_msg_entity_spawn({
        "eid": 91, "kind": "player", "tx": 6, "ty": 5,
        "name": "Fulano", "class_id": "guerreiro", "hp": 100, "hp_max": 100, "level": 1,
    })
    fx._spawn_mob_projectile(900, {
        "x": 160.0, "y": 160.0, "attacker_seid": 90, "target_seid": fx._my_eid,
        "color": [255, 80, 0], "is_arrow": False,
        "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
    })
    proj = _find_cosmetic_projectile(fx)
    assert proj is not None
    assert proj.spell_id == "bola_de_fogo", \
        "projétil da torre de fogo deveria usar o sprite animado, não o círculo genérico"


def test_torre_de_fogo_tem_entity_class_mago_reconstruida_no_cliente():
    """Núcleo do bug: sem o fallback TOWER_TABLE, entity_class virava
    sempre 'Arqueiro' (template genérico) — nunca 'Mago' de verdade."""
    from engine.components import EntityIdentity
    fx = _make_net_fixture()
    fx._handle_msg_entity_spawn({
        "eid": 92, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "torre_de_fogo", "entity_class": "Mago", "is_ranged": True,
        "hp": 4000, "hp_max": 4000, "level": 5, "faction": "monstros_hostis",
        "name": "Torre de Fogo",
    })
    local_eid = fx._remote_mobs[92]
    ident = fx.world.get_component(local_eid, EntityIdentity)
    assert ident.entity_class == "Mago"


def test_som_de_lancamento_e_impacto_da_torre_de_fogo_espelham_bola_de_fogo():
    """Bug real relatado pelo usuário: torre de fogo não emitia NENHUM
    som — NpcSounds ficava vazio porque 'torre_de_fogo' nunca era
    encontrada em MOB_TABLE."""
    from ui.sound_manager import SOUNDS
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 93, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "torre_de_fogo", "entity_class": "Mago", "is_ranged": True,
        "hp": 4000, "hp_max": 4000, "level": 5, "faction": "monstros_hostis",
        "name": "Torre de Fogo",
    })
    fx._handle_msg_entity_spawn({
        "eid": 94, "kind": "enemy", "tx": 7, "ty": 5,
        "race": "Zumbi", "entity_class": "Warrior",
        "hp": 30, "hp_max": 30, "level": 1, "faction": "monstros_hostis",
    })
    calls, original = _record_npc_sound_calls()
    try:
        fx._spawn_mob_projectile(901, {
            "x": 160.0, "y": 160.0, "attacker_seid": 93, "target_seid": 94,
            "color": [255, 80, 0], "is_arrow": False,
            "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
        })
        played_impact = fx._play_nonplayer_attack_impact(93, 100.0, 100.0, 0.0, 0.0)
    finally:
        SOUNDS.play_mob_sounds_at = original
    assert ("attack_magic", "skill_bola_de_fogo_launch") in calls, \
        "lançamento da torre de fogo deveria tocar o som de launch da Bola de Fogo"
    assert played_impact
    assert ("attack_impact", "skill_bola_de_fogo_impact") in calls, \
        "impacto da torre de fogo deveria tocar o som de impact da Bola de Fogo (não silêncio)"


def test_som_de_disparo_da_torre_de_flechas_e_arrow_release():
    from ui.sound_manager import SOUNDS
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 95, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "torre_de_flechas", "entity_class": "Arqueiro", "is_ranged": True,
        "hp": 3500, "hp_max": 3500, "level": 5, "faction": "monstros_hostis",
        "name": "Torre de Flechas",
    })
    fx._handle_msg_entity_spawn({
        "eid": 96, "kind": "enemy", "tx": 7, "ty": 5,
        "race": "Zumbi", "entity_class": "Warrior",
        "hp": 30, "hp_max": 30, "level": 1, "faction": "monstros_hostis",
    })
    calls, original = _record_npc_sound_calls()
    try:
        fx._spawn_mob_projectile(902, {
            "x": 160.0, "y": 160.0, "attacker_seid": 95, "target_seid": 96,
            "color": [200, 160, 60], "is_arrow": True,
            "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
        })
    finally:
        SOUNDS.play_mob_sounds_at = original
    assert ("attack_ranged", "arrow_release") in calls, \
        "disparo da torre de flechas deveria tocar arrow_release, nao ficar em silencio/melee"


def test_espelho_remoto_de_torre_bloqueia_o_segundo_tile_pro_player_local():
    """Bug real relatado pelo usuário (12/08/2026): sprite novo da torre
    (gcn_19, 2 tiles de largura) tinha colisão real batendo só com 1
    tile no SERVIDOR — mas o espelho remoto que o CLIENTE reconstrói
    (`_spawn_remote_mob`, torre vira `Enemy` genérico, NUNCA ganha o
    componente `Tower`) também precisa saber disso, senão o player
    prediz localmente andar por cima do 2º tile e leva um snap de
    correção do servidor (bug visual "elástico"). `entity_footprint_
    tiles()` detecta torre via `EntityIdentity.mob_key` (não via
    `Tower`, que o espelho do cliente nunca tem) — este teste prova que
    o payload real de spawn (com `sprite_id`, que `_build_mob_spawn_
    payload` já manda pro cliente) resulta no 2º tile bloqueado também
    do lado do cliente, não só do servidor (ver testes irmãos em
    tests/test_towers.py::TestTowerFootprint)."""
    from ui.systems import PlayerInputSystem
    fx = _make_net_fixture()
    fx._handle_msg_entity_spawn({
        "eid": 98, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "torre_de_fogo", "entity_class": "Mago", "is_ranged": True,
        "hp": 4000, "hp_max": 4000, "level": 5, "faction": "monstros_hostis",
        "name": "Torre de Fogo", "sprite_id": "gcn_19",
    })
    pis = PlayerInputSystem(fx.world)
    occupied = pis._get_enemy_tiles()
    assert (5, 5) in occupied, "tile âncora da torre já bloqueava antes"
    assert (6, 5) in occupied, \
        "2º tile do sprite (leste) deveria bloquear pro player local também"


def test_disparo_da_torre_de_flechas_toca_mesmo_mirando_o_player_local():
    """Bug real relatado pelo usuário 29/07/2026: quando o alvo do
    projétil É o player local, o som de LANÇAMENTO nunca tocava (pulado
    de propósito, assumindo que _play_attacker_mob_sound tocaria o mesmo
    som na chegada — errado, ver teste abaixo). Sem esse disparo, o
    jogador só ouvia o som ERRADO na hora do impacto e nada no
    lançamento."""
    from ui.sound_manager import SOUNDS
    fx = _make_net_fixture()
    fx._remote_mob_projectiles = {}
    fx._handle_msg_entity_spawn({
        "eid": 97, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "torre_de_flechas", "entity_class": "Arqueiro", "is_ranged": True,
        "hp": 3500, "hp_max": 3500, "level": 5, "faction": "monstros_hostis",
        "name": "Torre de Flechas",
    })
    calls, original = _record_npc_sound_calls()
    try:
        fx._spawn_mob_projectile(903, {
            "x": 160.0, "y": 160.0, "attacker_seid": 97, "target_seid": fx._my_eid,
            "color": [200, 160, 60], "is_arrow": True,
            "dir_x": 1.0, "dir_y": 0.0, "speed": 380.0,
        })
    finally:
        SOUNDS.play_mob_sounds_at = original
    assert ("attack_ranged", "arrow_release") in calls, \
        "lançamento deveria tocar mesmo quando o alvo é o próprio player"


def test_impacto_no_player_toca_attack_impact_nao_o_som_de_lancamento():
    """Núcleo do bug relatado pelo usuário 29/07/2026: a torre de flecha
    tocava o som de LANÇAMENTO (arrow_release) no momento do IMPACTO —
    _play_attacker_mob_sound (chamado na chegada do COMBAT_RESULT) usava
    'attack_ranged'/'attack_magic' (chaves de lançamento) em vez de
    'attack_impact', diferente da função irmã _play_nonplayer_attack_
    impact (mob-vs-mob), que sempre fez isso certo."""
    fx, server_attacker = _make_attacker_sound_fixture("Arqueiro", attack_impact="arrow_impact")
    import client.remote_entity_handlers as reh_mod
    calls = _spy_play_mob_sounds_at(reh_mod)
    handled = fx._play_attacker_mob_sound(server_attacker, 0.0, 0.0)
    assert handled is True
    assert calls == ["attack_impact"], \
        "impacto no player deveria tocar attack_impact, nunca o som de lançamento (attack_ranged)"


def test_atacante_melee_sem_config_cai_no_fallback_hit_normal():
    """Regressão: atacante não-player SEM som configurado (ex: Guarda Real,
    raça fora de MOB_TABLE) retorna False — o caller mantém o hit_normal
    genérico de antes, nada muda pra quem já funcionava."""
    fx = _make_net_fixture()
    fx._handle_msg_entity_spawn({
        "eid": 63, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Humanoide", "entity_class": "Warrior",
        "hp": 100, "hp_max": 100, "level": 10, "faction": "guardas_vila",
        "name": "Guarda Real",
    })
    played = fx._play_nonplayer_attack_impact(63, 100.0, 100.0, 0.0, 0.0)
    assert not played, "sem som configurado deve cair no fallback antigo (hit_normal do caller)"


def test_spawn_remote_mob_normal_nao_ganha_componente_de_npc_servico():
    """Regressão — mob comum (sem profession/shop_id/class_id no payload)
    continua sem nenhum componente de capacidade, igual antes desta leva."""
    from engine.components import NPC, Merchant, Trainer, QuestGiver
    fx = _make_net_fixture()
    fx._handle_msg_entity_spawn({
        "eid": 97, "kind": "enemy", "tx": 5, "ty": 5,
        "race": "Zumbi", "entity_class": "Warrior",
        "hp": 30, "hp_max": 30, "level": 1, "faction": "monstros_hostis",
    })
    local_eid = fx._remote_mobs[97]
    assert fx.world.get_component(local_eid, NPC) is None
    assert fx.world.get_component(local_eid, Merchant) is None
    assert fx.world.get_component(local_eid, Trainer) is None
    assert fx.world.get_component(local_eid, QuestGiver) is None


# ── B2 (22/07/2026): Interceptar não prediz mais o dash localmente — a
# animação só toca quando a correção confirmada do servidor chega via
# ENTITY_MOVE (is_dash=True, skill_rejected=False). Bug real relatado pelo
# usuário: alvo em movimento causava loop de "dash e volta" a cada rejeição
# — a predição local nunca mais deveria existir, só a reconciliação.
def test_interceptar_dash_visual_nao_existe_mais():
    """Regressão contra reintrodução: SkillSystem não deve mais ter o método
    de predição local de dash — só a animação disparada pela correção do
    servidor (ver _handle_msg_entity_move)."""
    from ui.systems import SkillSystem
    assert not hasattr(SkillSystem, "_interceptar_dash_visual")


def _make_entity_move_fixture():
    from engine.world import World
    from engine.components import Position, TileMovement
    import client.network_handlers as nh_mod

    class _Fixture(nh_mod.NetworkHandlers):
        def __init__(self, world, player_entity, my_eid):
            self.world = world
            self.player_entity = player_entity
            self._my_eid = my_eid
            self._self_move_queue = []
            self._net_last_tx = 0
            self._net_last_ty = 0

    world = World()
    player = world.create_entity()
    world.add_component(player, Position(x=5 * 32 + 16, y=5 * 32 + 16))
    world.add_component(player, TileMovement(current_tile_x=5, current_tile_y=5))
    fx = _Fixture(world, player, my_eid=1)
    return fx, player


def test_dash_confirmado_pelo_servidor_anima_do_zero_sem_predicao_local():
    """Sem predição local (player_tm parado), a correção is_dash=True do
    servidor deve INICIAR a animação — mesmo mecanismo já usado por
    deslocamento forçado (knockback), sem precisar de nenhum caminho novo."""
    from engine.components import TileMovement
    fx, player = _make_entity_move_fixture()
    fx._handle_msg_entity_move({
        "eid": fx._my_eid, "tx": 7, "ty": 5, "from_tx": 5, "from_ty": 5,
        "is_dash": True, "skill_rejected": False,
    })
    tm = fx.world.get_component(player, TileMovement)
    assert tm.is_dash is True
    assert tm.is_moving is True
    assert (tm.target_tile_x, tm.target_tile_y) == (7, 5)


def test_dash_rejeitado_pelo_servidor_nao_anima_nada():
    """skill_rejected=True (path bloqueado/alvo fora de range no instante do
    servidor) não deve iniciar nenhuma animação — sem predição local, não há
    nada pra desfazer/snap-back."""
    from engine.components import TileMovement
    fx, player = _make_entity_move_fixture()
    fx._handle_msg_entity_move({
        "eid": fx._my_eid, "tx": 5, "ty": 5, "from_tx": 5, "from_ty": 5,
        "is_dash": True, "skill_rejected": True,
    })
    tm = fx.world.get_component(player, TileMovement)
    assert tm.is_moving is False


# ── GameEngine._rebuild_screen_refs — hud_surf, não só .screen ──────────────
# Bug real (24/07/2026, reportado pelo usuário): modal de loot sumiu da tela
# depois de trocar o modo de janela (§34.48/49, que passou a recriar
# self.screen com um objeto Surface NOVO). Sistemas que herdam de
# engine.world_systems.System (LootSystem, ShopSystem, QuestDialogSystem,
# QuestJournalSystem, TrainerSystem, BlacksmithSystem/CraftingSystem) só têm
# world_surf/hud_surf — NUNCA um atributo .screen. _rebuild_screen_refs só
# setava sys.screen, um no-op silencioso pra todos esses — hud_surf ficava
# apontando pra a Surface antiga (órfã) pelo resto da sessão.

class _StubHudSurfSystem:
    """Imita LootSystem/ShopSystem/QuestDialogSystem/etc: só hud_surf/
    world_surf, nunca .screen (mesmo contrato de engine.world_systems.System)."""
    def __init__(self, surf):
        self.hud_surf   = surf
        self.world_surf = surf


class _StubScreenOnlySystem:
    """Imita TalentSystem/CharStatsUI/SkillLevelUI/MapOverlay/Minimap: só
    .screen (não são System subclasses)."""
    def __init__(self, surf):
        self.screen = surf


class _RebuildScreenRefsFixture:
    def __init__(self, surf):
        self.systems = []
        self._loot_system       = _StubHudSurfSystem(surf)
        self._shop_system       = _StubHudSurfSystem(surf)
        self._quest_dialog      = _StubHudSurfSystem(surf)
        self._quest_journal     = _StubHudSurfSystem(surf)
        self._trainer_system    = _StubHudSurfSystem(surf)
        self._crafting_system   = _StubHudSurfSystem(surf)
        self._blacksmith_system = None
        self._skill_system      = None
        self._projectile_system = None
        self._render_system     = None
        self._tile_render_system = None
        self._talent_system   = _StubScreenOnlySystem(surf)
        self._skill_level_ui  = _StubScreenOnlySystem(surf)
        self._char_stats_ui   = _StubScreenOnlySystem(surf)
        self._map_overlay     = _StubScreenOnlySystem(surf)
        self._minimap         = _StubScreenOnlySystem(surf)


from game import GameEngine as _GE_rsr
_RebuildScreenRefsFixture._rebuild_screen_refs = _GE_rsr._rebuild_screen_refs


def test_rebuild_screen_refs_atualiza_hud_surf_de_sistemas_sem_screen():
    old_surf = pygame.Surface((10, 10))
    new_surf = pygame.Surface((20, 20))
    fx = _RebuildScreenRefsFixture(old_surf)
    fx._rebuild_screen_refs(new_surf)
    assert fx._loot_system.hud_surf is new_surf
    assert fx._shop_system.hud_surf is new_surf
    assert fx._quest_dialog.hud_surf is new_surf
    assert fx._quest_journal.hud_surf is new_surf
    assert fx._trainer_system.hud_surf is new_surf
    assert fx._crafting_system.hud_surf is new_surf


def test_rebuild_screen_refs_continua_atualizando_quem_usa_screen():
    old_surf = pygame.Surface((10, 10))
    new_surf = pygame.Surface((20, 20))
    fx = _RebuildScreenRefsFixture(old_surf)
    fx._rebuild_screen_refs(new_surf)
    assert fx._talent_system.screen is new_surf
    assert fx._char_stats_ui.screen is new_surf
    assert fx._map_overlay.screen is new_surf
    assert fx._minimap.screen is new_surf


# ── ui/char_creation_screen.py::_drain_char_select_batch ─────────────────────
# Bug real relatado pelo usuário 25/07/2026: harvestable perto do spawn
# (dentro do raio de AOI já no login) nunca tinha loot disponível — o Corpse
# local ficava sempre vazio, mesmo a entidade aparecendo certinho na tela
# (renderizava normal, mas clicar nunca abria o modal). Investigação
# (com prints de diagnóstico temporários no cliente E no servidor, depois
# removidos) confirmou: o servidor mandava WORLD_STATE seguido imediatamente
# de LOOT_AVAILABLE (server/session.py::_spawn_and_start) pra cada
# harvestable dentro do AOI de login — mas a tela de seleção de personagem
# (run_online), ao achar WORLD_STATE na MESMA leva de net.poll(), dava
# `return True` NO MEIO do for, sem terminar de examinar o resto da lista.
# As LOOT_AVAILABLE que vinham logo depois na MESMA leva ficavam presas na
# lista local e eram perdidas pra sempre. Só acontecia quando o harvestable
# estava perto o bastante do spawn pra sair já no snapshot de login — a
# caixa de teste original (longe do spawn) nunca pegava esse caminho,
# só era descoberta depois via sweep de tick, quando esta tela já tinha
# fechado.

def test_drain_char_select_batch_preserva_mensagens_apos_world_state_na_mesma_leva():
    from ui.char_creation_screen import _drain_char_select_batch
    from shared.messages import MsgType
    msgs = [
        (MsgType.WORLD_STATE, {"entities": []}, 1, 0),
        (MsgType.LOOT_AVAILABLE, {"corpse_id": 1, "items": ["x"]}, 2, 0),
        (MsgType.LOOT_AVAILABLE, {"corpse_id": 2, "items": ["y"]}, 3, 0),
    ]
    pending_action, status, game_buffer, chars, reset_del, got_ws = \
        _drain_char_select_batch(msgs, "selecting", [], [], -1)

    assert got_ws is True
    assert status is None
    assert len(game_buffer) == 3, \
        "as 2 LOOT_AVAILABLE que vieram depois do WORLD_STATE na mesma leva não podem ser descartadas"
    assert game_buffer[0][0] == MsgType.WORLD_STATE
    assert game_buffer[1][0] == MsgType.LOOT_AVAILABLE
    assert game_buffer[1][1]["corpse_id"] == 1
    assert game_buffer[2][0] == MsgType.LOOT_AVAILABLE
    assert game_buffer[2][1]["corpse_id"] == 2


def test_drain_char_select_batch_sem_world_state_nao_reporta_pronto():
    from ui.char_creation_screen import _drain_char_select_batch
    from shared.messages import MsgType
    msgs = [(MsgType.LOOT_AVAILABLE, {"corpse_id": 1}, 1, 0)]
    pending_action, status, game_buffer, chars, reset_del, got_ws = \
        _drain_char_select_batch(msgs, "selecting", [], [], -1)
    assert got_ws is False
    assert game_buffer == [(MsgType.LOOT_AVAILABLE, {"corpse_id": 1}, 1, 0)]


def test_drain_char_select_batch_character_error_limpa_buffer_e_reseta_pending():
    from ui.char_creation_screen import _drain_char_select_batch
    from shared.messages import MsgType
    msgs = [
        (MsgType.LOOT_AVAILABLE, {"corpse_id": 1}, 1, 0),   # já acumulado antes do erro
        (MsgType.CHARACTER_ERROR, {"reason": "banido"}, 2, 0),
    ]
    pending_action, status, game_buffer, chars, reset_del, got_ws = \
        _drain_char_select_batch(msgs, "selecting", [], [], -1)
    assert pending_action == ""
    assert status == "Erro: banido"
    assert game_buffer == []
    assert got_ws is False


def test_drain_char_select_batch_delete_character_ok_remove_do_chars_e_sinaliza_reset():
    from ui.char_creation_screen import _drain_char_select_batch
    from shared.messages import MsgType
    chars_in = [{"id": 5, "name": "A"}, {"id": 7, "name": "B"}]
    msgs = [(MsgType.DELETE_CHARACTER_OK, {}, 1, 0)]
    pending_action, status, game_buffer, chars, reset_del, got_ws = \
        _drain_char_select_batch(msgs, "", [], chars_in, confirm_del_id=5)
    assert reset_del is True
    assert chars == [{"id": 7, "name": "B"}]


# ── ui/quest_system.py::QuestDialogSystem.open_for_item + client/
# inventory_handlers.py::_try_open_item_quest_dialog — item concede quest
# reaproveita o MESMO modal de diálogo de quest do NPC (25/07/2026, pedido
# do usuário; REVISADO no mesmo dia — usuário rejeitou um popup custom
# construído antes, pediu explicitamente a estrutura de sempre: mesmo
# modal do NPC, só que aberto pelo item, e o inventário deve fechar
# quando isso acontece). Clique direito num item registrado em
# ITEM_GRANTS_QUEST abre o QuestDialogSystem direto no estado "detail"
# (sem NPC real por trás) e fecha o inventário. Recusar/fechar não
# descarta o item (reabre no próximo clique); aceitar manda o MESMO
# QUEST_ACCEPT que o diálogo de NPC já usa, com npc_name="" (não há NPC
# real pra disparar talk_to_npc).

from ui.quest_system import QuestDialogSystem, QuestSystem


class _FakeNetItemQuestDialog:
    def __init__(self):
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


def _make_item_quest_dialog():
    from engine.world import World
    from engine.components import QuestLog, CharacterStats, PlayerControlled
    world = World()
    player = world.create_entity()
    world.add_component(player, CharacterStats(name="Testchar", class_id="guerreiro"))
    world.add_component(player, QuestLog())
    world.add_component(player, PlayerControlled())
    screen = pygame.display.get_surface()
    qs = QuestSystem(world, player)
    qs.set_ui_scale(1.0)
    dlg = QuestDialogSystem(world, player, screen, qs)
    dlg.set_ui_scale(1.0)
    dlg._qs._net = _FakeNetItemQuestDialog()
    return dlg, world, player


def _make_item_quest(qid="qz_item_quest_teste", item_name="Item de Quest de Teste"):
    """Registra uma quest de teste em QUESTS (limpa pelo chamador via
    try/finally) — objetivo collect_item pra casar com o padrão real de
    item-concede-quest."""
    from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
    QUESTS[qid] = QuestDef(
        title="Quest de Teste", description="d",
        objectives=(ObjectiveDef(type="collect_item", target="*",
                                 loot_item=item_name, count=1),),
        reward=QuestReward(xp=1),
    )
    return qid, item_name


def test_open_for_item_abre_estado_detail_sem_npc_real():
    from content.quests_data import QUESTS
    qid, item_name = _make_item_quest()
    try:
        dlg, world, player = _make_item_quest_dialog()
        opened = dlg.open_for_item(qid, item_name)
        assert opened is True
        assert dlg.is_open is True
        assert dlg._dialog_state == "detail"
        assert dlg._dialog_selected_qid == qid
        assert dlg._item_source is True
    finally:
        QUESTS.pop(qid, None)


def test_open_for_item_nao_abre_se_quest_ja_ativa():
    from content.quests_data import QUESTS
    from engine.components import QuestLog
    qid, item_name = _make_item_quest(qid="qz_item_quest_teste2")
    try:
        dlg, world, player = _make_item_quest_dialog()
        ql = world.get_component(player, QuestLog)
        ql.active[qid] = [0]
        assert dlg.open_for_item(qid, item_name) is False
        assert dlg.is_open is False
    finally:
        QUESTS.pop(qid, None)


def test_open_for_item_nao_abre_se_quest_ja_completa():
    from content.quests_data import QUESTS
    from engine.components import QuestLog
    qid, item_name = _make_item_quest(qid="qz_item_quest_teste3")
    try:
        dlg, world, player = _make_item_quest_dialog()
        ql = world.get_component(player, QuestLog)
        ql.completed.add(qid)
        assert dlg.open_for_item(qid, item_name) is False
        assert dlg.is_open is False
    finally:
        QUESTS.pop(qid, None)


def test_open_for_item_nao_abre_pra_qid_inexistente():
    dlg, world, player = _make_item_quest_dialog()
    assert dlg.open_for_item("qid_que_nao_existe", "Item Qualquer") is False
    assert dlg.is_open is False


def test_render_do_modal_aberto_por_item_nao_se_autofecha_sem_npc_real():
    """render() do fluxo NPC fecha o modal sozinho se o QuestGiver sumiu
    (giver is None) — item-sourced nunca teve giver nenhum, então
    precisa do branch dedicado pra NÃO cair nesse auto-close."""
    from content.quests_data import QUESTS
    qid, item_name = _make_item_quest(qid="qz_item_quest_teste4")
    try:
        dlg, world, player = _make_item_quest_dialog()
        dlg.open_for_item(qid, item_name)
        dlg.render()
        assert dlg.is_open is True
        assert dlg._dialog_state == "detail"
    finally:
        QUESTS.pop(qid, None)


def test_aceitar_no_modal_aberto_por_item_manda_quest_accept_com_npc_name_vazio():
    from content.quests_data import QUESTS
    from shared.messages import MsgType
    qid, item_name = _make_item_quest(qid="qz_item_quest_teste5")
    try:
        dlg, world, player = _make_item_quest_dialog()
        dlg.open_for_item(qid, item_name)
        dlg.render()   # popula _accept_rect/_decline_rect
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=dlg._accept_rect.center)
        dlg.handle_events([ev])
        assert dlg._qs._net.sent == [(MsgType.QUEST_ACCEPT, {"quest_id": qid, "npc_name": ""})]
        assert dlg.is_open is False
    finally:
        QUESTS.pop(qid, None)


def test_recusar_no_modal_aberto_por_item_so_fecha_sem_mandar_nada():
    from content.quests_data import QUESTS
    qid, item_name = _make_item_quest(qid="qz_item_quest_teste6")
    try:
        dlg, world, player = _make_item_quest_dialog()
        dlg.open_for_item(qid, item_name)
        dlg.render()
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=dlg._decline_rect.center)
        dlg.handle_events([ev])
        assert dlg._qs._net.sent == []
        assert dlg.is_open is False
    finally:
        QUESTS.pop(qid, None)


# ── client/inventory_handlers.py::_try_open_item_quest_dialog — cola o
# item com o modal acima e fecha o inventário no gatilho. ───────────────

from client.inventory_handlers import InventoryHandlers as _InvH


class _ItemQuestDialogFixture(_InvH):
    def __init__(self, world, player_entity, quest_dialog):
        self.world = world
        self.player_entity = player_entity
        self._quest_dialog = quest_dialog
        self._show_inventory = True
        self._selected_inv_idx = 3

    def _u(self, px):
        return px


def test_try_open_item_quest_dialog_abre_modal_e_fecha_inventario():
    from content.quests_data import QUESTS, ITEM_GRANTS_QUEST
    from engine.components import Item
    qid, item_name = _make_item_quest(qid="qz_item_quest_teste7",
                                       item_name="Item de Quest de Teste 7")
    ITEM_GRANTS_QUEST[item_name] = qid
    try:
        dlg, world, player = _make_item_quest_dialog()
        fx = _ItemQuestDialogFixture(world, player, dlg)
        item = Item(item_name, "material", slot=None, max_stack=1)
        opened = fx._try_open_item_quest_dialog(item)
        assert opened is True
        assert dlg.is_open is True
        assert dlg._dialog_selected_qid == qid
        assert fx._show_inventory is False
        assert fx._selected_inv_idx == -1
    finally:
        QUESTS.pop(qid, None)
        ITEM_GRANTS_QUEST.pop(item_name, None)


def test_try_open_item_quest_dialog_nao_abre_pra_item_nao_registrado():
    from engine.components import Item
    dlg, world, player = _make_item_quest_dialog()
    fx = _ItemQuestDialogFixture(world, player, dlg)
    item = Item("Item Qualquer", "material", slot=None, max_stack=1)
    assert fx._try_open_item_quest_dialog(item) is False
    assert dlg.is_open is False
    assert fx._show_inventory is True   # inventário não mexido


def test_try_open_item_quest_dialog_nao_abre_se_quest_ja_ativa_mantem_inventario():
    from content.quests_data import QUESTS, ITEM_GRANTS_QUEST
    from engine.components import Item, QuestLog
    qid, item_name = _make_item_quest(qid="qz_item_quest_teste8",
                                       item_name="Item de Quest de Teste 8")
    ITEM_GRANTS_QUEST[item_name] = qid
    try:
        dlg, world, player = _make_item_quest_dialog()
        ql = world.get_component(player, QuestLog)
        ql.active[qid] = [0]
        fx = _ItemQuestDialogFixture(world, player, dlg)
        item = Item(item_name, "material", slot=None, max_stack=1)
        assert fx._try_open_item_quest_dialog(item) is False
        assert dlg.is_open is False
        assert fx._show_inventory is True
    finally:
        QUESTS.pop(qid, None)
        ITEM_GRANTS_QUEST.pop(item_name, None)


# ── client/modal_stack_handlers.py::_any_modal_open — clique no minimap
# vazava pro click-to-move (28/07/2026, bug real relatado pelo usuário:
# clicar com o direito num item de quest na bag fazia o personagem andar
# até o tile clicado). Causa raiz: o bloco de clique no minimap em
# game.py::run() só checava `_map_overlay.is_open`, nunca nenhum OUTRO
# modal — um clique que caísse dentro do retângulo do minimap na tela
# vazava pro click-to-move mesmo com um painel [inventário, quest dialog,
# etc.] desenhado por cima cobrindo aquele canto. Fix: o bloco passou a
# usar `_any_modal_open()` (mesmo ponto único de verdade já usado pelo
# gating de systems_events logo abaixo dele). Como esse bloco é código
# inline dentro do run() monolítico (não extraído em método próprio), não
# dá pra testar o bloco em si isoladamente — os testes abaixo cobrem o
# INGREDIENTE do fix: `_any_modal_open()` reconhece quest_dialog aberto
# via `open_for_item()` (sem NPC real) mesmo com o inventário já fechado,
# que é exatamente o estado em que o bug se manifestava.

class _StubClosable:
    """Stub genérico pra sub-objetos referenciados por _modal_registry()
    (self._loot_system, self._quest_dialog, ...) — qualquer atributo/
    método de fechamento não definido explicitamente vira no-op, já que
    _modal_registry() constrói a lista inteira eagerly (referencia TODOS
    os close_fn na hora, mesmo os de modais fechados)."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        return lambda *a, **k: None


def _make_modal_stack_fixture(quest_dialog_open=False, show_inventory=False,
                              loot_open=False):
    from client.modal_stack_handlers import ModalStackHandlers

    class _Fixture(ModalStackHandlers):
        def __init__(self):
            self._mkb_rebind       = None
            self._chat_active      = False
            self._show_habilidades = False
            self._map_overlay      = _StubClosable(is_open=False)
            self._loot_system      = _StubClosable(open_corpse_id=(1 if loot_open else -1))
            self._crafting_system  = _StubClosable(is_open=False)
            self._trainer_system   = _StubClosable(is_open=False)
            self._quest_dialog     = _StubClosable(is_open=quest_dialog_open)
            self._quest_journal    = _StubClosable(is_open=False)
            self._shop_system      = _StubClosable(qty_modal_open=False, is_open=False)
            self._trade_is_open    = False
            self._trade_qty_modal  = None   # modal de fatiar stack no trade (11/08/2026)
            # Modais de PvP (10/08/2026 — antes ausentes do registro, ESC
            # não fechava nenhum deles, achado real do usuário).
            self._arena_pending_match = None
            self._bg_pending_match    = None
            self._arena_result        = None
            self._bg_result           = None
            self._arena_modal_open    = False
            self._show_hotbar_editor = False
            self._show_debug       = False
            self._show_talents     = False
            self._show_skills      = False
            self._show_inventory   = show_inventory
            self._show_pause       = False

        def __getattr__(self, name):
            if name.startswith("_close_"):
                return lambda *a, **k: None
            raise AttributeError(name)

    return _Fixture()


def test_any_modal_open_false_com_tudo_fechado():
    fx = _make_modal_stack_fixture()
    assert fx._any_modal_open() is False


def test_any_modal_open_true_com_quest_dialog_aberto_via_item_e_inventario_fechado():
    """Mesmo estado do bug real: quest_dialog abre via open_for_item()
    (sem NPC real) e o inventário já fechou junto — _any_modal_open()
    precisa continuar True, senão o bloco de clique no minimap em
    game.py volta a vazar pro click-to-move."""
    fx = _make_modal_stack_fixture(quest_dialog_open=True, show_inventory=False)
    assert fx._any_modal_open() is True
    assert fx._topmost_open_modal() == "quest_dialog"


# ── game.py — LootSystem recebe eventos BRUTOS incondicionalmente,
# vazando clique-pra-andar (29/07/2026, bug real relatado pelo usuário:
# clicar num harvestable por baixo de OUTRO modal aberto — ex.
# inventário — fazia o personagem andar até lá, "como se o harvestable
# estivesse acima do modal na ordem de camadas"). Mesma classe de bug do
# vazamento do minimap (§34.59) — LootSystem._try_open_corpse() só deve
# rodar com raw events quando o modal TOPO é "loot" (o dela mesma) ou
# nenhum modal está aberto; com qualquer OUTRO modal no topo, deve cair
# pros systems_events já filtrados (como qualquer outra system). Como o
# bloco em si é código inline em game.py::run() (não extraído em
# método), os testes abaixo cobrem só o INGREDIENTE
# (_topmost_open_modal() distingue "loot" de outro modal corretamente).

def test_topmost_open_modal_retorna_loot_quando_so_o_loot_esta_aberto():
    fx = _make_modal_stack_fixture(loot_open=True)
    assert fx._topmost_open_modal() == "loot"


def test_topmost_open_modal_nao_retorna_loot_com_inventario_aberto_e_loot_fechado():
    """Ingrediente do fix: com o loot FECHADO e outro modal (inventário)
    aberto, _topmost_open_modal() precisa retornar "inventory" (não
    "loot", não None) — é essa distinção que faz o bloco de game.py
    parar de dar raw events pro LootSystem nesse estado."""
    fx = _make_modal_stack_fixture(loot_open=False, show_inventory=True)
    assert fx._topmost_open_modal() == "inventory"


# ── Modais de PvP no registro (10/08/2026, bug real relatado pelo
# usuário: ESC não fechava o modal de fila de Arena/BG — nenhum dos 5
# modais de PvP estava em _modal_registry(), então nunca eram
# encontrados por _close_top_modal()). Prova diferencial: cada modal
# reconhecido por _topmost_open_modal() E fechado por _close_top_modal()
# sem levantar exceção (mesmo padrão de closer com efeito colateral real
# já usado por "trade", ver _close_arena_result/_close_bg_result).

def test_arena_queue_modal_e_reconhecido_e_fechado_pelo_esc():
    fx = _make_modal_stack_fixture()
    fx._arena_modal_open = True
    assert fx._topmost_open_modal() == "arena_queue"
    assert fx._close_top_modal() is True


def test_arena_accept_modal_e_reconhecido_e_fechado_pelo_esc():
    fx = _make_modal_stack_fixture()
    fx._arena_pending_match = {"mode": "2v2"}
    assert fx._topmost_open_modal() == "arena_accept"
    assert fx._close_top_modal() is True


def test_bg_result_modal_e_reconhecido_pelo_esc():
    fx = _make_modal_stack_fixture()
    fx._bg_result = {"winner_faction": "arena_time_a", "players": []}
    assert fx._topmost_open_modal() == "bg_result"


# ── ui/systems.py::RenderSystem — Y-sort do harvestable com sprite
# (28/07/2026, bug real relatado pelo usuário: "quando eu estou no mesmo
# tile do item, o item fica sobre o personagem, e o personagem deveria
# ficar sobre o item quando ele não tem colisão... não só personagem, mas
# também para mobs e npcs"). Harvestable (create_harvestable_entity)
# ocupa a altura CHEIA do tile (Renderable.height=32) com Position.y já
# no CENTRO do tile — o foot_y genérico de qualquer entidade (position.y
# + height/2) cai então na BASE do tile, sempre "na frente" (Y maior =
# desenhado por último = por cima) de um personagem/mob/NPC no MESMO
# tile (sprite menor que o tile, ex. PLAYER_SIZE=24). Objeto ESTÁTICO de
# mapa (árvore/arbusto) usa sort_y = centro do tile (TileRenderSystem),
# não a base — por isso nunca teve esse problema. Fix: entidade com
# Renderable.sprite_id setado (só harvestable, por ora) ordena pelo
# CENTRO do tile (position.y puro) em vez do foot_y genérico.

def test_render_harvestable_com_sprite_nao_desenha_por_cima_do_personagem_no_mesmo_tile():
    from engine.world import World
    from engine.components import Position, Renderable
    from ui.systems import RenderSystem

    world = World()
    # Mesmo tile (3,3) -> centro em (112, 112) — TILE_SIZE=32
    harvestable = world.create_entity()
    world.add_component(harvestable, Position(x=112, y=112, prev_x=112, prev_y=112))
    world.add_component(harvestable, Renderable(color=(10, 20, 30), width=32, height=32,
                                                sprite_id="sprite_de_teste_inexistente_xyz"))
    player = world.create_entity()
    world.add_component(player, Position(x=112, y=112, prev_x=112, prev_y=112))
    world.add_component(player, Renderable(color=(40, 50, 60), width=24, height=24))

    screen = pygame.display.get_surface()
    rs = RenderSystem(world, screen)

    draw_order = []
    orig_rect = pygame.draw.rect
    def _spy_rect(surface, color, rect, *a, **k):
        draw_order.append(tuple(color[:3]))
        return orig_rect(surface, color, rect, *a, **k)
    pygame.draw.rect = _spy_rect
    try:
        rs.render(0, 0)
    finally:
        pygame.draw.rect = orig_rect

    assert (10, 20, 30) in draw_order and (40, 50, 60) in draw_order
    assert draw_order.index((40, 50, 60)) > draw_order.index((10, 20, 30)), \
        "personagem (sprite menor) deveria desenhar DEPOIS (por cima) do harvestable no mesmo tile"


# ── Shift+V: ciclo de 3 níveis de detalhe do nameplate (29/07/2026,
# pedido do usuário) — 0=completo (padrão), 1=só nome, 2=nome+barra de
# HP simples (build_simple_hp_bar, sem PNG/badge/efeitos). Cobre o HUD
# do próprio player (RenderSystem.render) e o badge de mob local; mob/
# player remoto ficam em test_nameplate_mode_*_player_remoto_* acima e
# em client/remote_entity_handlers.py.

def _make_nameplate_render_fixture():
    from engine.world import World
    from engine.components import (Position, Renderable, CombatStats,
                                   PlayerControlled, CharacterStats, EntityIdentity)
    from ui.systems import RenderSystem

    world = World()
    player = world.create_entity()
    world.add_component(player, Position(x=100, y=100, prev_x=100, prev_y=100))
    world.add_component(player, Renderable(color=(40, 50, 60), width=24, height=24))
    world.add_component(player, PlayerControlled())
    p_cs = CombatStats()
    p_cs.max_hp = 100
    p_cs.current_hp = 80
    world.add_component(player, p_cs)
    p_char = CharacterStats(name="Herói", class_id="guerreiro")
    p_char.level = 3
    world.add_component(player, p_char)

    mob = world.create_entity()
    world.add_component(mob, Position(x=300, y=100, prev_x=300, prev_y=100))
    world.add_component(mob, Renderable(color=(90, 10, 10), width=24, height=24))
    m_cs = CombatStats()
    m_cs.max_hp = 50
    m_cs.current_hp = 50
    world.add_component(mob, m_cs)
    world.add_component(mob, EntityIdentity(name="Lobo", race="Animal",
                                            entity_class="mob", level=2))

    screen = pygame.display.get_surface()
    rs = RenderSystem(world, screen)
    return rs


def test_render_system_nameplate_mode_0_desenha_badge_e_nome_pro_player_e_mob():
    from ui.world_labels import WORLD_LABELS
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    rs = _make_nameplate_render_fixture()
    rs.render(0, 0, nameplate_mode=0)
    # 2 elementos por entidade (badge/barra PNG + nome) x 2 entidades = 4
    assert len(WORLD_LABELS._pending) == 4


def test_render_system_nameplate_mode_1_mostra_so_nome():
    from ui.world_labels import WORLD_LABELS
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    rs = _make_nameplate_render_fixture()
    rs.render(0, 0, nameplate_mode=1)
    assert len(WORLD_LABELS._pending) == 2, "modo 1: só o nome de cada entidade, nada mais"


def test_render_system_nameplate_mode_2_mostra_barra_simples_sem_asset_png():
    from ui.world_labels import WORLD_LABELS
    from ui.hud_bars import P_SIZE, M_SIZE, SCALE
    WORLD_LABELS._pending.clear()
    WORLD_LABELS._stack_offset.clear()
    rs = _make_nameplate_render_fixture()
    rs.render(0, 0, nameplate_mode=2)
    assert len(WORLD_LABELS._pending) == 4, "modo 2: barra simples + nome por entidade"
    _icon_sizes = {WORLD_LABELS._pending[0][2].get_size(), WORLD_LABELS._pending[2][2].get_size()}
    _png_sizes = {(P_SIZE[0] * SCALE, P_SIZE[1] * SCALE), (M_SIZE[0] * SCALE, M_SIZE[1] * SCALE)}
    assert not (_icon_sizes & _png_sizes), \
        "modo 2 não deveria usar o tamanho nativo dos assets PNG (build_player_hud/build_mob_hud)"


# ── ui/ui_helpers.py::draw_stack_count — "1" é redundante, não mostra;
# número sem prefixo "x"; outline preto pra legibilidade (28/07/2026,
# pedido do usuário). Fonte única usada por inventário, barra de
# consumíveis, trade e crafting — um fix aqui cobre todos os lugares.

class _FakeStackFont:
    def __init__(self):
        self.rendered = []

    def render(self, text, aa, color):
        self.rendered.append((text, color))
        return pygame.Surface((10, 10), pygame.SRCALPHA)


class _SpySurf:
    """Fake surf com só o método que draw_stack_count usa (.blit) —
    pygame.Surface real não permite monkeypatch de .blit (atributo
    read-only, objeto C)."""
    def __init__(self):
        self.blits = []

    def blit(self, *a, **k):
        self.blits.append(a)


def test_draw_stack_count_nao_desenha_com_stack_1_mesmo_stackavel():
    from ui.ui_helpers import draw_stack_count
    from engine.components import Item
    item = Item("Poção de Vida", "consumable", slot=None, max_stack=10)
    item.stack = 1
    font = _FakeStackFont()
    surf = _SpySurf()
    draw_stack_count(surf, item, pygame.Rect(0, 0, 32, 32), font)
    assert surf.blits == []
    assert font.rendered == [], "stack=1 não deveria nem renderizar o texto"


def test_draw_stack_count_nao_desenha_se_nao_stackavel():
    from ui.ui_helpers import draw_stack_count
    from engine.components import Item
    item = Item("Espada de Treinamento", "weapon", slot="mainhand", max_stack=1)
    item.stack = 1
    font = _FakeStackFont()
    surf = _SpySurf()
    draw_stack_count(surf, item, pygame.Rect(0, 0, 32, 32), font)
    assert surf.blits == []


def test_draw_stack_count_desenha_numero_sem_x_com_outline_a_partir_de_2():
    from ui.ui_helpers import draw_stack_count
    from engine.components import Item
    item = Item("Poção de Vida", "consumable", slot=None, max_stack=10)
    item.stack = 3
    font = _FakeStackFont()
    surf = _SpySurf()
    draw_stack_count(surf, item, pygame.Rect(0, 0, 32, 32), font)
    texts = {t for t, _c in font.rendered}
    assert texts == {"3"}, "texto deve ser só o número, sem prefixo 'x'"
    assert len(surf.blits) == 9, "8 blits de outline preto + 1 do número branco"


# ── game.py::compute_window_geometry — fonte única de tamanho/flags de
# janela a partir do window_mode salvo, usada por GameEngine (troca em
# tempo real) E por main.py (login/seleção de personagem, 28/07/2026 —
# bug real: main.py sempre abria essas 2 telas numa janela de tamanho
# FIXO baseado só em `scale`, ignorando window_mode por completo — com
# scale=1.5 (1920x1080, resolução comum de monitor Full HD), a janela
# cobria a tela inteira sem nenhuma flag de fullscreen, visualmente
# indistinguível de tela cheia de verdade, mesmo com window_mode salvo
# como "windowed_fullsize").

def _with_desktop_size(size, fn):
    orig = pygame.display.get_desktop_sizes
    pygame.display.get_desktop_sizes = lambda: [size]
    try:
        return fn()
    finally:
        pygame.display.get_desktop_sizes = orig


# ── client/hotbar_handlers.py::_handle_hotbar_click/_handle_consumable_
# bar_click — agora retornam True quando o clique caiu num slot (mesmo
# sem a skill/consumível disparar de verdade — cooldown, talento
# bloqueado) — 29/07/2026, bug real relatado pelo usuário: clicar num
# slot da hotbar/barra de consumíveis desselecionava o alvo em combate.
# Causa: o clique esquerdo "vazava" pro MouseTargetingSystem mais
# abaixo no mesmo frame (que não sabia que o clique já tinha sido usado
# pela hotbar), e como nenhum inimigo existe na posição-de-mundo
# (calculada a partir de coordenadas de TELA de um clique de UI), caía
# no branch de "clique esquerdo no chão" — que desseleciona o alvo.
# game.py agora usa o retorno pra impedir que o MESMO clique chegue no
# MouseTargetingSystem (mesma classe de bug do vazamento do minimap/
# loot já corrigidos antes).

def _make_hotbar_click_fixture(skill_id="skill_teste_generico"):
    from engine.world import World
    from engine.components import Position, PlayerSkills, Skill, CombatState, CombatStats
    from client.hotbar_handlers import HotbarHandlers

    world = World()
    player = world.create_entity()
    world.add_component(player, Position(x=0, y=0))
    ps = PlayerSkills()
    sk = Skill("Skill de Teste", "desc", cooldown=1.0)
    sk.skill_id = skill_id
    ps.skills[0] = sk
    world.add_component(player, ps)
    world.add_component(player, CombatState())
    world.add_component(player, CombatStats())

    class _FakeSkillSystem:
        def __init__(self):
            self.used = []

        def _use_skill(self, idx, skill):
            self.used.append((idx, skill.skill_id))
            return True   # skill disparou com sucesso

    class _Fixture(HotbarHandlers):
        def __init__(self):
            self.world          = world
            self.player_entity  = player
            self.screen         = pygame.display.get_surface()
            self.systems        = []
            self._skill_system  = _FakeSkillSystem()

        def _u(self, px):
            return px

        def _set_panel_scale(self, *a, **k):
            pass

    return _Fixture()


def _slot0_center(fx) -> tuple:
    """Centro do rect do slot 0 — mesma geometria de _handle_hotbar_click
    (1 skill ocupada: n_occ=1)."""
    total_w = fx._HB_W
    x0 = fx.screen.get_width() // 2 - total_w // 2
    y0 = fx.screen.get_height() - fx._HB_H - fx._u(10)
    return x0 + fx._HB_W // 2, y0 + fx._HB_H // 2


def test_handle_hotbar_click_fora_dos_slots_retorna_false():
    fx = _make_hotbar_click_fixture()
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(0, 0))
    assert fx._handle_hotbar_click(ev) is False
    assert fx._skill_system.used == []


def test_handle_hotbar_click_em_cima_do_slot_aciona_skill_e_retorna_true():
    fx = _make_hotbar_click_fixture()
    pos = _slot0_center(fx)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)
    consumed = fx._handle_hotbar_click(ev)
    assert consumed is True
    assert fx._skill_system.used == [(0, "skill_teste_generico")]


def test_handle_hotbar_click_talento_bloqueado_retorna_true_sem_usar_skill():
    """golpe_debilitante exige o talento cav_golpe_debilitante alocado
    (_TALENT_SKILL_REQS) — _is_talent_locked "falha aberto" sem TalentTree
    nenhuma (assume não bloqueado), então o teste precisa de uma TalentTree
    de verdade com 0 pontos alocados no talento certo pra reproduzir o
    bloqueio. O clique ainda CONSOME (retorna True) mesmo bloqueado — é
    isso que impede o vazamento pro mundo."""
    from engine.components import TalentTree
    fx = _make_hotbar_click_fixture(skill_id="golpe_debilitante")
    fx.world.add_component(fx.player_entity, TalentTree())
    pos = _slot0_center(fx)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)
    consumed = fx._handle_hotbar_click(ev)
    assert consumed is True
    assert fx._skill_system.used == [], "skill bloqueada por talento não deveria disparar"


def test_handle_consumable_bar_click_fora_retorna_false():
    from engine.world import World
    from engine.components import ConsumableBar
    from client.hotbar_handlers import HotbarHandlers

    world = World()
    player = world.create_entity()
    cbar = ConsumableBar()
    world.add_component(player, cbar)

    class _Fixture(HotbarHandlers):
        def __init__(self):
            self.world         = world
            self.player_entity = player
            self.screen        = pygame.display.get_surface()
            self.systems       = []

        def _u(self, px):
            return px

        def _set_panel_scale(self, *a, **k):
            pass

    fx = _Fixture()
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(0, 0))
    assert fx._handle_consumable_bar_click(ev) is False


def test_handle_consumable_bar_click_em_cima_do_slot_retorna_true_mesmo_em_gcd():
    """Mesmo em cooldown global (não usa o consumível de verdade), o
    clique em cima do slot ainda CONSOME (retorna True) — é isso que
    impede o vazamento pro mundo."""
    from engine.world import World
    from engine.components import ConsumableBar
    from client.hotbar_handlers import HotbarHandlers

    world = World()
    player = world.create_entity()
    cbar = ConsumableBar()
    cbar.slots[0] = "Poção de Vida"
    cbar.global_cooldown = 1.0   # em GCD — não deve impedir o "consumo" do clique
    world.add_component(player, cbar)

    class _Fixture(HotbarHandlers):
        def __init__(self):
            self.world         = world
            self.player_entity = player
            self.screen        = pygame.display.get_surface()
            self.systems       = []

        def _u(self, px):
            return px

        def _set_panel_scale(self, *a, **k):
            pass

    fx = _Fixture()
    x0 = fx.screen.get_width() // 2 + 20
    y0 = fx.screen.get_height() - fx._HB_H - fx._u(10)
    pos = (x0 + fx._HB_W // 2, y0 + fx._HB_H // 2)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)
    assert fx._handle_consumable_bar_click(ev) is True


def test_compute_window_geometry_fullscreen_usa_resolucao_do_desktop():
    from game import compute_window_geometry
    w, h, flags = _with_desktop_size(
        (1920, 1080), lambda: compute_window_geometry("fullscreen", 1.0))
    assert (w, h) == (1920, 1080)
    assert flags & pygame.FULLSCREEN


def test_compute_window_geometry_windowed_fullsize_usa_desktop_com_folga():
    from game import compute_window_geometry
    w, h, flags = _with_desktop_size(
        (1920, 1080), lambda: compute_window_geometry("windowed_fullsize", 1.0))
    assert (w, h) == (1920 - 16, 1080 - 80)
    assert not (flags & pygame.FULLSCREEN)
    assert flags & pygame.RESIZABLE


# ── _play_attacker_mob_sound: escolha de som por EntityIdentity, não
# AIControlled (29/07/2026) — o espelho remoto de um mob/torre NUNCA tem
# AIControlled (removido de propósito em _spawn_remote_mob, servidor é
# autoritativo pra IA), então o código antigo (`if _atk_ai and ...`)
# nunca era True e todo ataque de mob/torre remoto contra o player caía
# sempre em "attack_melee" — bug crônico relatado pelo usuário testando
# torre de flechas (som de melee em vez de flecha), mas que afeta
# QUALQUER mob remoto ranged/mágico atacando o player, não só torre.

def _make_attacker_sound_fixture(entity_class: str, attack_impact: str = ""):
    from engine.world import World
    from engine.components import Position, NpcSounds, EntityIdentity
    import client.remote_entity_handlers as reh_mod

    class _Fixture(reh_mod.RemoteEntityHandlers):
        def __init__(self, world):
            self.world = world
            self._my_eid = 1
            self._remote_players = {}

    world = World()
    attacker_eid = world.create_entity()
    world.add_component(attacker_eid, Position(x=50.0, y=50.0))
    world.add_component(attacker_eid, NpcSounds(
        attack_melee="bite", attack_ranged="bow_shot", attack_magic="fireball_cast",
        attack_impact=attack_impact))
    world.add_component(attacker_eid, EntityIdentity(
        name="Torre de Teste", race="Construcao", entity_class=entity_class))

    fx = _Fixture(world)
    fx._remote_mobs = {99: attacker_eid}
    return fx, 99


def _spy_play_mob_sounds_at(monkeypatch_module):
    calls = []
    def _spy(comp, event, *a, **k):
        calls.append(event)
    monkeypatch_module.SOUNDS.play_mob_sounds_at = _spy
    return calls


def test_som_de_ataque_arqueiro_remoto_usa_attack_ranged_nao_melee():
    import client.remote_entity_handlers as reh_mod
    fx, server_attacker = _make_attacker_sound_fixture("Arqueiro")
    calls = _spy_play_mob_sounds_at(reh_mod)
    handled = fx._play_attacker_mob_sound(server_attacker, 0.0, 0.0)
    assert handled is True
    assert calls == ["attack_ranged"], "atacante Arqueiro/Hunter deveria tocar attack_ranged, nunca attack_melee"


def test_som_de_ataque_mago_remoto_usa_attack_magic_nao_melee():
    import client.remote_entity_handlers as reh_mod
    fx, server_attacker = _make_attacker_sound_fixture("Mago")
    calls = _spy_play_mob_sounds_at(reh_mod)
    handled = fx._play_attacker_mob_sound(server_attacker, 0.0, 0.0)
    assert handled is True
    assert calls == ["attack_magic"], "atacante Mago/Warlock deveria tocar attack_magic, nunca attack_melee"


def test_som_de_ataque_melee_de_verdade_continua_attack_melee():
    import client.remote_entity_handlers as reh_mod
    fx, server_attacker = _make_attacker_sound_fixture("Guerreiro")
    calls = _spy_play_mob_sounds_at(reh_mod)
    handled = fx._play_attacker_mob_sound(server_attacker, 0.0, 0.0)
    assert handled is True
    assert calls == ["attack_melee"]



# ── _apply_combat_result: silencia FLT/som de combate SEM player em
# nenhum dos dois lados, DENTRO da BG estilo MOBA (03/08/2026, pedido do
# usuário — "muito spam de dano e sons de batalha dos minions"). Torre
# entra no mesmo balde de "não-player" (torre×minion também some);
# qualquer lado sendo player (inclusive minion batendo NO player) mantém
# o feedback normal; fora da BG nada muda.

def _make_combat_result_fixture(in_bg: bool):
    from engine.world import World
    from engine.components import (Position, NpcSounds, EntityIdentity,
                             PlayerControlled, RemoteEntityMeta)
    from ui.ui_components import InstanceInventoryUIState
    import client.remote_entity_handlers as reh_mod
    from client.save_sync_handlers import SaveSyncHandlers

    class _NoopPnq:
        def _increment_pnq_counter(self, *a, **k):
            pass

    class _Fixture(reh_mod.RemoteEntityHandlers, SaveSyncHandlers):
        def __init__(self, world, player_entity):
            self.world = world
            self.player_entity = player_entity
            self._my_eid = 1
            self._remote_players = {}
            self._remote_mobs = {}
            self._mob_ghost_pos = {}
            self._player_input_system = _NoopPnq()

    world = World()
    player_eid = world.create_entity()
    world.add_component(player_eid, Position(x=0.0, y=0.0))
    world.add_component(player_eid, PlayerControlled())
    iius = InstanceInventoryUIState()
    iius.active = in_bg
    world.add_component(player_eid, iius)

    attacker_eid = world.create_entity()
    world.add_component(attacker_eid, Position(x=10.0, y=10.0))
    world.add_component(attacker_eid, NpcSounds(attack_melee="minion_hit"))
    world.add_component(attacker_eid, EntityIdentity(
        name="Minion A", race="Minion", entity_class="Guerreiro"))

    target_eid = world.create_entity()
    world.add_component(target_eid, Position(x=12.0, y=12.0))
    world.add_component(target_eid, NpcSounds())
    world.add_component(target_eid, EntityIdentity(
        name="Minion B", race="Minion", entity_class="Guerreiro"))
    world.add_component(target_eid, RemoteEntityMeta(server_eid=20, hp=100, hp_max=100))

    fx = _Fixture(world, player_eid)
    fx._remote_mobs = {10: attacker_eid, 20: target_eid}
    return fx


def _spy_flt_and_sounds(reh_mod_module):
    from ui.floating_text import FLT as _FLT_spy
    calls = {"flt": 0, "sound": 0}
    _FLT_spy.add = lambda *a, **k: calls.__setitem__("flt", calls["flt"] + 1)
    reh_mod_module.SOUNDS.play_mob_sounds_at = lambda *a, **k: calls.__setitem__("sound", calls["sound"] + 1)
    reh_mod_module.SOUNDS.play_random_at = lambda *a, **k: calls.__setitem__("sound", calls["sound"] + 1)
    reh_mod_module.SOUNDS.play_emote_at = lambda *a, **k: calls.__setitem__("sound", calls["sound"] + 1)
    return calls


def test_bg_silencia_flt_e_som_minion_vs_minion():
    import client.remote_entity_handlers as reh_mod
    fx = _make_combat_result_fixture(in_bg=True)
    calls = _spy_flt_and_sounds(reh_mod)
    fx._apply_combat_result({"attacker": 10, "target": 20, "damage": 15,
                             "outcome": "hit", "hp_after": 85, "source": "auto"})
    assert calls == {"flt": 0, "sound": 0}, \
        "minion vs minion na BG não deveria gerar FLT nem som"


def test_bg_mantem_flt_e_som_player_vs_minion():
    import client.remote_entity_handlers as reh_mod
    fx = _make_combat_result_fixture(in_bg=True)
    fx._my_eid = 10  # atacante É o player local
    calls = _spy_flt_and_sounds(reh_mod)
    fx._apply_combat_result({"attacker": 10, "target": 20, "damage": 15,
                             "outcome": "hit", "hp_after": 85, "source": "auto"})
    assert calls["flt"] > 0, "player atacando minion deveria continuar mostrando FLT"


def test_bg_mantem_flt_e_som_minion_vs_player():
    """Correção do usuário no mesmo pedido: "quando o minion bate no
    player, pode emitir o som e o FLT" — minion atacando o PLAYER (não o
    contrário) também não deveria ser silenciado."""
    import client.remote_entity_handlers as reh_mod
    from engine.components import CombatStats, CombatState
    fx = _make_combat_result_fixture(in_bg=True)
    _cs = CombatStats()
    _cs.current_hp, _cs.max_hp = 100, 100
    fx.world.add_component(fx.player_entity, _cs)
    fx.world.add_component(fx.player_entity, CombatState())
    fx._my_eid = 20  # alvo É o player local
    calls = _spy_flt_and_sounds(reh_mod)
    fx._apply_combat_result({"attacker": 10, "target": 20, "damage": 15,
                             "outcome": "hit", "hp_after": 85, "source": "auto"})
    assert calls["flt"] > 0, "minion batendo no player não deveria ser silenciado"


def test_fora_da_bg_minion_vs_minion_continua_com_flt_e_som():
    import client.remote_entity_handlers as reh_mod
    fx = _make_combat_result_fixture(in_bg=False)
    calls = _spy_flt_and_sounds(reh_mod)
    fx._apply_combat_result({"attacker": 10, "target": 20, "damage": 15,
                             "outcome": "hit", "hp_after": 85, "source": "auto"})
    assert calls["flt"] > 0, "fora da BG, minion vs minion não deveria ser afetado"


def test_compute_window_geometry_windowed_ignora_tamanho_do_monitor():
    """Núcleo da regressão: 'windowed' usa só 1280*scale/720*scale,
    NUNCA a resolução do monitor — antes desta fix, main.py achava que
    esse era o ÚNICO comportamento existente (daí o bug: login/seleção
    nunca aplicavam windowed_fullsize/fullscreen)."""
    from game import compute_window_geometry
    w, h, flags = _with_desktop_size(
        (3840, 2160), lambda: compute_window_geometry("windowed", 1.0))
    assert (w, h) == (1280, 720)
    assert not (flags & pygame.FULLSCREEN)


# ── Flecha carrega o PRÓPRIO resultado do servidor, nunca uma fila por alvo,
# e o HP nunca fica preso esperando o impacto visual (12-13/08/2026, ver
# PROBLEMAS_ARQUITETURA.md §44) — bug real relatado pelo usuário: HP de
# torre/mob "voltava" durante combate ativo, com magnitude variável.
#
# 2 causas encontradas e corrigidas em sequência:
# 1) `pending_arrow_impacts` era uma fila FIFO por ALVO; quando 2+ flechas
#    convergiam pro mesmo alvo, qualquer flecha que chegasse visualmente
#    primeiro consumia o item da FRENTE da fila, sem checar se era dela
#    mesma. Fix: cada flecha (`PlayerProjectile`) prende seu próprio
#    resultado em `deferred_result` no momento em que nasce.
# 2) Mesmo com (1) corrigido, o HP em si ainda só era aplicado quando a
#    flecha chegava visualmente (podia levar vários frames, dependendo da
#    distância) — se OUTRO ataque (magia, corpo-a-corpo, outra flecha já
#    corrigida) acertasse o MESMO alvo e aplicasse o HP dele na hora
#    ENQUANTO a primeira flecha ainda voava, o HP mais novo ficava
#    sobrescrito pelo `hp_after` desatualizado da flecha ao ela finalmente
#    chegar. Fix: HP aplicado sempre na confirmação do servidor, igual
#    magia/corpo-a-corpo — só FLT/som continuam esperando o impacto visual.

def _make_archer_projectile_fixture():
    """Player local arqueiro + 1 mob/torre alvo com RemoteEntityMeta —
    setup mínimo pra exercitar `_apply_combat_result` no caminho de
    auto-attack de flecha (`_resolve_archer_attack` exige CharacterStats.
    class_id=='arqueiro' no player local)."""
    from engine.world import World
    from engine.components import (Position, NpcSounds, EntityIdentity,
                             PlayerControlled, RemoteEntityMeta, CharacterStats,
                             PlayerProjectile)
    from ui.ui_components import InstanceInventoryUIState
    import client.remote_entity_handlers as reh_mod
    from client.save_sync_handlers import SaveSyncHandlers

    class _NoopPnq:
        def _increment_pnq_counter(self, *a, **k):
            pass

    class _Fixture(reh_mod.RemoteEntityHandlers, SaveSyncHandlers):
        def __init__(self, world, player_entity):
            self.world = world
            self.player_entity = player_entity
            self._my_eid = 1
            self._remote_players = {}
            self._remote_mobs = {}
            self._mob_ghost_pos = {}
            self._fr_pending_target = {}
            self._player_input_system = _NoopPnq()

    world = World()
    player_eid = world.create_entity()
    world.add_component(player_eid, Position(x=0.0, y=0.0))
    world.add_component(player_eid, PlayerControlled())
    world.add_component(player_eid, CharacterStats(class_id="arqueiro"))
    world.add_component(player_eid, InstanceInventoryUIState())

    target_eid = world.create_entity()
    world.add_component(target_eid, Position(x=12.0, y=12.0))
    world.add_component(target_eid, NpcSounds())
    world.add_component(target_eid, EntityIdentity(
        name="Torre de Fogo", race="Construcao", entity_class=""))
    world.add_component(target_eid, RemoteEntityMeta(server_eid=50, hp=1000, hp_max=1000))

    fx = _Fixture(world, player_eid)
    fx._remote_mobs = {50: target_eid}
    return fx, target_eid


def _spawned_arrows(world):
    from engine.components import PlayerProjectile
    return [(eid, pp) for eid, pp in world.get_entities_with(PlayerProjectile)]


def test_flecha_carrega_o_proprio_resultado_nunca_fila_por_alvo():
    """2 golpes de flecha confirmados em sequência contra o MESMO alvo devem
    produzir 2 flechas, CADA UMA com seu próprio outcome/damage — nunca uma
    fila genérica onde a ordem de CONSUMO pode se misturar. `deferred_result`
    não carrega mais hp_after (HP é aplicado na hora, não na flecha)."""
    import client.remote_entity_handlers as reh_mod
    fx, target_eid = _make_archer_projectile_fixture()
    calls = _spy_flt_and_sounds(reh_mod)

    fx._apply_combat_result({"attacker": 1, "target": 50, "damage": 20,
                             "outcome": "hit", "hp_after": 980, "source": "auto",
                             "is_ranged": True})
    fx._apply_combat_result({"attacker": 1, "target": 50, "damage": 30,
                             "outcome": "hit", "hp_after": 950, "source": "auto",
                             "is_ranged": True})

    arrows = _spawned_arrows(fx.world)
    assert len(arrows) == 2, "cada golpe deveria nascer com sua própria flecha"
    damages = sorted(pp.deferred_result["damage"] for _, pp in arrows)
    assert damages == [20, 30], \
        "cada flecha deveria carregar o damage DELA, não um valor genérico"
    assert all("hp_after" not in pp.deferred_result for _, pp in arrows), \
        "HP não deveria mais viajar preso na flecha — é aplicado na hora"

    from engine.components import RemoteEntityMeta
    meta = fx.world.get_component(target_eid, RemoteEntityMeta)
    assert meta.hp == 950, \
        "HP deveria já refletir o 2º golpe IMEDIATAMENTE, sem esperar nenhuma flecha chegar"


def test_hp_de_flecha_atrasada_nao_sobrescreve_dano_mais_novo_de_outra_fonte():
    """Diferencial do 2º achado (sessão 13/08/2026): uma flecha ainda em voo
    não pode, ao chegar visualmente, reverter um HP mais novo aplicado por
    OUTRO ataque (magia/corpo-a-corpo/outra flecha já corrigida) enquanto ela
    ainda estava no ar. Antes desta correção, `_on_hit` aplicava o hp_after
    "congelado" no instante em que o SERVIDOR resolveu aquele golpe
    específico — desatualizado se algo mais acertou o alvo depois."""
    import client.remote_entity_handlers as reh_mod
    import ui.spell_system as ss_mod
    from engine.components import RemoteEntityMeta, PlayerProjectile
    fx, target_eid = _make_archer_projectile_fixture()
    _spy_flt_and_sounds(reh_mod)
    ss_mod.SOUNDS.play_random_at   = lambda *a, **k: None
    ss_mod.SOUNDS.play_spell_at    = lambda *a, **k: None
    ss_mod.SOUNDS.play_spell       = lambda *a, **k: None
    ss_mod.SOUNDS.play_random      = lambda *a, **k: None

    # 1º golpe: flecha do player, confirmado — hp 1000 -> 980. A flecha
    # nasce e vai ficar "voando" (só chamamos _on_hit dela mais tarde).
    fx._apply_combat_result({"attacker": 1, "target": 50, "damage": 20,
                             "outcome": "hit", "hp_after": 980, "source": "auto",
                             "is_ranged": True})
    arrows = _spawned_arrows(fx.world)
    assert len(arrows) == 1
    arrow_eid, _ = arrows[0]

    # ENQUANTO a flecha ainda voa: outro ataque (atacante 2, não-arqueiro —
    # ex: minion batendo corpo-a-corpo) acerta o MESMO alvo e aplica na
    # hora — hp 980 -> 900. Mais novo, deveria "vencer".
    fx._apply_combat_result({"attacker": 2, "target": 50, "damage": 80,
                             "outcome": "hit", "hp_after": 900, "source": "auto",
                             "is_ranged": False})

    meta = fx.world.get_component(target_eid, RemoteEntityMeta)
    assert meta.hp == 900, "dano mais novo de outra fonte deveria já estar aplicado"

    # SÓ AGORA a flecha do 1º golpe chega visualmente (ex: atacante longe).
    proj_sys = ss_mod.PlayerProjectileSystem(fx.world, pygame.Surface((10, 10)),
                                             player_entity=fx.player_entity)
    proj = fx.world.get_component(arrow_eid, PlayerProjectile)
    proj_sys._on_hit(proj, 12.0, 12.0)

    assert meta.hp == 900, (
        "flecha atrasada não pode reverter o HP mais novo aplicado por outro "
        "ataque enquanto ela ainda estava voando — HP nunca deveria ter sido "
        "tocado por _on_hit, só FLT/som")


# ── Minimapa em tela cheia da BG (13/08/2026, pedido do usuário) — mostra o
# mapa INTEIRO (sem seguir/centralizar no player) com minion/torre/player
# coloridos por time, só dentro da instância. Ver ui/minimap.py::
# render_fullmap + game.py::_collect_bg_minimap_dots.

class _FakeMapOverlayMM:
    def __init__(self, cols, rows, bg_color=(40, 60, 40)):
        self._cols = cols
        self._rows = rows
        self._base_surf = pygame.Surface((cols, rows))
        self._base_surf.fill(bg_color)


def _make_minimap_fixture(cols=10, rows=10):
    from ui.minimap import Minimap
    # Surface OFFSCREEN própria (não a tela real de 320x240 já criada no
    # topo do arquivo) — o frame do minimap (SIZE=220 + MARGIN_TOP=68)
    # não cabe nessa tela pequena, e usar uma independente evita mexer no
    # display global compartilhado com o resto da suíte.
    screen = pygame.Surface((400, 400))
    overlay = _FakeMapOverlayMM(cols, rows)
    mm = Minimap(screen, overlay)
    return mm, screen


def test_render_fullmap_desenha_ponto_na_posicao_certa_do_tile():
    mm, screen = _make_minimap_fixture(cols=10, rows=10)
    all_tiles = {(x, y) for x in range(10) for y in range(10)}
    mm.render_fullmap(all_tiles, all_tiles, [(5, 5, (255, 0, 0), 2)])

    rect = mm.get_rect()
    scale = min(rect.width / 10, rect.height / 10)
    sx = rect.x + int(5 * scale)
    sy = rect.y + int(5 * scale)
    px = screen.get_at((sx, sy))
    assert (px.r, px.g, px.b) == (255, 0, 0), \
        f"esperava vermelho puro no centro do ponto, achou {(px.r, px.g, px.b)}"


def test_render_fullmap_nao_precisa_de_player_tx_ty_pra_nao_quebrar():
    """Diferencial do modo normal: nunca deveria exigir/usar posição do
    player pra centralizar — mapa fica fixo mesmo com dots em cantos
    opostos do mapa."""
    mm, screen = _make_minimap_fixture(cols=10, rows=10)
    all_tiles = {(x, y) for x in range(10) for y in range(10)}
    mm.render_fullmap(all_tiles, all_tiles, [(0, 0, (0, 200, 0), 2), (9, 9, (0, 0, 200), 2)])
    rect = mm.get_rect()
    scale = min(rect.width / 10, rect.height / 10)
    px0 = screen.get_at((rect.x, rect.y))
    px9 = screen.get_at((rect.x + int(9 * scale), rect.y + int(9 * scale)))
    assert (px0.r, px0.g, px0.b) == (0, 200, 0)
    assert (px9.r, px9.g, px9.b) == (0, 0, 200)


def test_render_fullmap_tile_nao_explorado_fica_preto():
    mm, screen = _make_minimap_fixture(cols=10, rows=10)
    mm.render_fullmap(explored=set(), visible=set(), dots=[])
    rect = mm.get_rect()
    px = screen.get_at((rect.x + 5, rect.y + 5))
    assert (px.r, px.g, px.b) == (0, 0, 0), "tile nunca explorado deveria ficar preto"


# ── GameEngine._collect_bg_minimap_dots ──────────────────────────────────────

from game import GameEngine as _GE_mm


class _MinimapDotsFixture:
    _BG_MINIMAP_TEAM_COLORS = _GE_mm._BG_MINIMAP_TEAM_COLORS
    _collect_bg_minimap_dots = _GE_mm._collect_bg_minimap_dots

    def __init__(self, world, player_entity, minimap):
        self.world = world
        self.player_entity = player_entity
        self._minimap = minimap


def _make_dots_world():
    from engine.world import World
    from engine.components import Faction, TileMovement, EntityIdentity, RemoteEntityMeta, RemoteControlled
    from ui.minimap import Minimap
    world = World()

    player = world.create_entity()
    world.add_component(player, Faction(faction_id="arena_time_a"))
    world.add_component(player, TileMovement(current_tile_x=1, current_tile_y=1))

    minion_a = world.create_entity()
    world.add_component(minion_a, Faction(faction_id="arena_time_a"))
    world.add_component(minion_a, TileMovement(current_tile_x=2, current_tile_y=2))
    world.add_component(minion_a, EntityIdentity(name="Minion", race="Humanoide", entity_class="", mob_key="minion_melee"))
    world.add_component(minion_a, RemoteEntityMeta(server_eid=10, hp=50, hp_max=50))

    minion_b = world.create_entity()
    world.add_component(minion_b, Faction(faction_id="arena_time_b"))
    world.add_component(minion_b, TileMovement(current_tile_x=3, current_tile_y=3))
    world.add_component(minion_b, EntityIdentity(name="Minion", race="Humanoide", entity_class="", mob_key="minion_ranged"))
    world.add_component(minion_b, RemoteEntityMeta(server_eid=11, hp=30, hp_max=30))

    tower_a = world.create_entity()
    world.add_component(tower_a, Faction(faction_id="arena_time_a"))
    world.add_component(tower_a, TileMovement(current_tile_x=4, current_tile_y=4))
    world.add_component(tower_a, EntityIdentity(name="Torre", race="Construcao", entity_class="", mob_key="torre_de_fogo"))
    world.add_component(tower_a, RemoteEntityMeta(server_eid=12, hp=2000, hp_max=2000))

    remote_player_b = world.create_entity()
    world.add_component(remote_player_b, Faction(faction_id="arena_time_b"))
    world.add_component(remote_player_b, TileMovement(current_tile_x=5, current_tile_y=5))
    world.add_component(remote_player_b, RemoteControlled(server_eid=20, name="Inimigo"))

    # Fora de visão — não deveria aparecer nos dots.
    minion_a_hidden = world.create_entity()
    world.add_component(minion_a_hidden, Faction(faction_id="arena_time_a"))
    world.add_component(minion_a_hidden, TileMovement(current_tile_x=8, current_tile_y=8))
    world.add_component(minion_a_hidden, EntityIdentity(name="Minion", race="Humanoide", entity_class="", mob_key="minion_melee"))
    world.add_component(minion_a_hidden, RemoteEntityMeta(server_eid=13, hp=50, hp_max=50))

    mm, _ = _make_minimap_fixture()
    fx = _MinimapDotsFixture(world, player, mm)
    visible_tiles = {(2, 2), (3, 3), (4, 4), (5, 5), (1, 1)}  # NÃO inclui (8,8)
    return fx, visible_tiles


def test_collect_bg_minimap_dots_cores_por_time():
    fx, visible_tiles = _make_dots_world()
    dots = fx._collect_bg_minimap_dots(visible_tiles)
    by_pos = {(x, y): color for x, y, color, _r in dots}
    assert by_pos[(2, 2)] == fx._BG_MINIMAP_TEAM_COLORS["arena_time_a"], "minion do time A deveria ser azul"
    assert by_pos[(3, 3)] == fx._BG_MINIMAP_TEAM_COLORS["arena_time_b"], "minion do time B deveria ser vermelho"
    assert by_pos[(4, 4)] == fx._BG_MINIMAP_TEAM_COLORS["arena_time_a"], "torre do time A deveria ser azul"
    assert by_pos[(5, 5)] == fx._BG_MINIMAP_TEAM_COLORS["arena_time_b"], "player remoto do time B deveria ser vermelho"


def test_collect_bg_minimap_dots_ignora_fora_da_visibilidade():
    fx, visible_tiles = _make_dots_world()
    dots = fx._collect_bg_minimap_dots(visible_tiles)
    positions = {(x, y) for x, y, _c, _r in dots}
    assert (8, 8) not in positions, "minion fora de visible_tiles não deveria aparecer"


def test_collect_bg_minimap_dots_sempre_inclui_o_proprio_player():
    fx, visible_tiles = _make_dots_world()
    # Tira o tile do player de visible_tiles de propósito — mesmo assim
    # ele tem que aparecer (sempre visível pra si mesmo).
    visible_tiles_sem_proprio = visible_tiles - {(1, 1)}
    dots = fx._collect_bg_minimap_dots(visible_tiles_sem_proprio)
    positions = {(x, y) for x, y, _c, _r in dots}
    assert (1, 1) in positions, "o próprio player deveria sempre aparecer, mesmo sem estar em visible_tiles"


def test_collect_bg_minimap_dots_raio_por_tipo():
    """Pedido do usuário (13/08/2026): minion do mesmo tamanho que torre
    confundia os dois — minion tem que ser bem menor (quase 1px), torre
    um pouco maior, player maior ainda."""
    fx, visible_tiles = _make_dots_world()
    dots = fx._collect_bg_minimap_dots(visible_tiles)
    by_pos = {(x, y): radius for x, y, _c, radius in dots}
    r_minion = by_pos[(2, 2)]
    r_tower  = by_pos[(4, 4)]
    r_player = by_pos[(5, 5)]
    assert r_minion == 1, "minion deveria ser quase 1px"
    assert r_minion < r_tower < r_player, \
        f"esperava minion({r_minion}) < torre({r_tower}) < player({r_player})"


# ── screen_to_tile_fullmap — clique no minimapa da BG (13/08/2026, bug real
# relatado pelo usuário: "não consigo mais clicar no minimapa pra andar" —
# o clique continuava passando pelo conversor do modo radar normal, que faz
# a conta errada nesta geometria diferente).

def test_screen_to_tile_fullmap_converte_clique_pro_tile_certo():
    mm, _screen = _make_minimap_fixture(cols=10, rows=10)
    rect = mm.get_rect()
    scale = min(rect.width / 10, rect.height / 10)
    # Clica bem no meio do tile (5,5) — soma meio tile pra não cair
    # exatamente na borda entre dois tiles.
    click_x = rect.x + int(5 * scale) + int(scale / 2)
    click_y = rect.y + int(5 * scale) + int(scale / 2)
    tile = mm.screen_to_tile_fullmap(click_x, click_y)
    assert tile == (5, 5), f"esperava (5, 5), achou {tile}"


def test_screen_to_tile_fullmap_fora_do_frame_retorna_none():
    mm, _screen = _make_minimap_fixture(cols=10, rows=10)
    assert mm.screen_to_tile_fullmap(0, 0) is None, \
        "clique bem no canto da tela (fora do frame do minimap) deveria retornar None"


def test_screen_to_tile_fullmap_nao_usa_geometria_do_modo_radar():
    """Diferencial direto do bug relatado: screen_to_tile (modo radar)
    e screen_to_tile_fullmap (modo BG) têm que dar resultados DIFERENTES
    pro MESMO clique — se estivessem usando a mesma conta, o bug não
    teria acontecido (ou não estaria corrigido)."""
    mm, _screen = _make_minimap_fixture(cols=10, rows=10)
    rect = mm.get_rect()
    # Um clique fixo qualquer dentro do frame.
    click_x = rect.x + 30
    click_y = rect.y + 40
    tile_radar   = mm.screen_to_tile(click_x, click_y, player_tx=50, player_ty=50)
    tile_fullmap = mm.screen_to_tile_fullmap(click_x, click_y)
    assert tile_radar != tile_fullmap, (
        "os 2 modos usam geometria diferente (radar centraliza no player e "
        "usa RADIUS fixo; fullmap escala cols/rows inteiros) — resultado "
        "pro mesmo clique não deveria bater")
