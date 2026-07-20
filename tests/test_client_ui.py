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
        self._pvp_respawn_target = -1
        # Estado de mobs remotos — não usado por estes testes (só players),
        # mas _handle_msg_aoi_update/_sync_mob_effects leem incondicionalmente.
        self._remote_mobs = {}
        self._mob_move_queues = {}
        self._pending_mob_despawn = {}
        self._mob_ghost_pos = {}
        self._pending_loot_redirect = {}


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


class _PvpCtxFixture(DuelHandlers, PvpZoneHandlers, PartyHandlers):
    def __init__(self, world, player_entity, my_eid=1):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = my_eid
        self._pvp_zones = []
        self._duel_opponent_local_val = -1
        self._party_id_val = -1
        self._party_members_val = []


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
    assert world.get_component(corpse, Corpse).coins == 0
