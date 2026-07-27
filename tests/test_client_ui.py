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
    loot.set_online_loot_requester(lambda local_eid, take, item_name: sent.append((local_eid, take, item_name)))
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
    loot.set_online_loot_requester(lambda local_eid, take, item_name: sent.append((local_eid, take, item_name)))
    _click_gold_row(loot, corpse)
    _click_gold_row(loot, corpse)   # 2º clique antes do LOOT_RESULT responder

    assert sent == [(corpse, "gold", "")], \
        "clique duplicado enquanto o request está em voo não deveria reenviar"


def test_online_loot_request_item_manda_take_item_com_nome():
    """Clicar num item (não no ouro) manda take='item' + item_name — não
    'all' (senão o request levaria o ouro junto, bug real relatado pelo
    usuário 17/07/2026: sacar parcial não deveria afetar o resto)."""
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
    loot.set_online_loot_requester(lambda local_eid, take, item_name: sent.append((local_eid, take, item_name)))
    result = _click_row(loot, corpse, 1)   # linha 1 = 1º item (linha 0 é o ouro)

    assert result is True
    assert sent == [(corpse, "item", item.name)]


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


# ── client/network_handlers.py::_handle_msg_loot_result — INV_SYNC ───────────
# Bug real relatado pelo usuário 18/07/2026: progresso de quest "colete N
# itens" parou de atualizar no HUD/diário (entrega ainda funcionava, só a
# EXIBIÇÃO travava). Causa raiz: a reescrita do loot granular/free-for-all
# (17/07/2026) passou a creditar itens direto em _handle_msg_loot_result,
# mas esqueceu de mandar INV_SYNC pro servidor depois — sem isso, o
# Inventory ECS do SERVIDOR nunca sabe do item novo, e
# sync_collect_progress (server/session.py::_handle_inventory_update)
# nunca roda. Mesmo padrão que _on_recarregar_changed já usava certo.

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


def test_loot_result_com_item_manda_inv_sync():
    fx = _make_loot_result_fixture()
    fx._handle_msg_loot_result({
        "corpse_id": 1, "coins": 0,
        "items": [{"name": "Pelo de Urso", "stack": 1}],
    })
    assert fx.loot_actions == ["item"], \
        "creditar item deveria disparar INV_SYNC (_on_loot_action) pro servidor saber do Inventory novo"
    assert fx.save_state_calls == 1


def test_loot_result_so_ouro_nao_manda_inv_sync():
    """Ouro já é server-authoritative (request_loot credita o Wallet do
    servidor direto) — não precisa de INV_SYNC, só itens passam pela
    Inventory local sem o servidor saber."""
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


class _PvpCtxFixture(DuelHandlers, PvpZoneHandlers, PartyHandlers, ArenaHandlers):
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
    from game import GameEngine as _GE_rp

    class _Fixture(reh_mod.RemoteEntityHandlers, DuelHandlers, PvpZoneHandlers,
                   PartyHandlers, ArenaHandlers):
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
