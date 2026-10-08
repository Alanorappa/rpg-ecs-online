"""
tests/test_remote_target_facing.py — 07/10/2026, bug real relatado pelo
usuário: entrar no meio de uma luta de outro player (ou só olhar pra ele
parado atacando) mostrava o personagem remoto virado pra direção ERRADA.

Causa raiz: `TileMovement.facing` só vira pro alvo quando a entidade tem
`CombatState.target_entity_id` setado e está parada (`engine/
world_systems.py::TileMovementSystem`, mecanismo já existia pro player
LOCAL desde 09/09/2026) — mas o espelho de um player REMOTO nunca
recebia esse componente, então nunca disparava.

Fix: servidor passa a sincronizar `CombatState.target_entity_id` de
cada player pros outros (`WorldServer.player_target_field` nos payloads
de ENTITY_SPAWN + `_sync_player_hp_dirty` pra TROCA de alvo depois).
Cliente anexa `CombatState` ao espelho remoto e aplica o campo — o
mecanismo de virar o personagem (`TileMovementSystem`) já é genérico,
nenhuma lógica de giro nova precisou ser escrita.

Decisão confirmada com o usuário: sincronizar o alvo de VERDADE (não só
reagir a golpes confirmados) — personagem vira pro alvo SELECIONADO.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import CombatState


# ── Servidor ─────────────────────────────────────────────────────────────────

class TestPlayerTargetField(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.player = spawn_player(self.ws, "s1", 130, 374)
        self.mob = spawn_player(self.ws, "s2", 131, 374)
        # set_player_target recusa alvo de facção "amigavel" (players são
        # amigáveis entre si por padrão) -- duelo libera can_engage entre
        # os dois pra poder testar a atribuição de alvo de verdade.
        self.assertIsNone(self.ws.request_duel(self.player, self.mob))
        self.ws.respond_duel_invite(self.mob, accept=True)

    def test_vazio_quando_sem_alvo(self):
        self.assertEqual(self.ws.player_target_field(self.player), {})

    def test_populado_quando_tem_alvo(self):
        self.ws.set_player_target("s1", self.mob)
        field = self.ws.player_target_field(self.player)
        self.assertEqual(field, {"target_eid": self.mob})

    def test_vazio_de_novo_apos_desmarcar(self):
        self.ws.set_player_target("s1", self.mob)
        self.ws.set_player_target("s1", -1)
        self.assertEqual(self.ws.player_target_field(self.player), {})


class TestSyncPlayerHpDirtyIncluiTarget(unittest.TestCase):
    """_sync_player_hp_dirty (server/world_server.py) já detecta troca de
    hp/max_hp/level genericamente — agora TAMBÉM target_entity_id, sem
    sistema novo."""

    def setUp(self):
        self.ws = make_world_server()
        self.player = spawn_player(self.ws, "s1", 130, 374)
        self.mob = spawn_player(self.ws, "s2", 131, 374)
        self.assertIsNone(self.ws.request_duel(self.player, self.mob))
        self.ws.respond_duel_invite(self.mob, accept=True)
        self.ws._sync_player_hp_dirty()  # baseline: registra estado atual no cache
        self.ws._player_hp_broadcasts_this_tick.clear()

    def _entry_for(self, eid):
        for e in self.ws._player_hp_broadcasts_this_tick:
            if e["eid"] == eid:
                return e
        return None

    def test_troca_de_alvo_sozinha_gera_broadcast_com_target_eid(self):
        """Sem NENHUMA mudança de hp/level -- só trocar de alvo já precisa
        disparar o broadcast, senão quem está perto nunca saberia."""
        self.ws.set_player_target("s1", self.mob)
        self.ws._sync_player_hp_dirty()
        entry = self._entry_for(self.player)
        self.assertIsNotNone(entry, "troca de alvo deveria ter gerado um broadcast")
        self.assertEqual(entry["target_eid"], self.mob)

    def test_sem_mudanca_nenhuma_nao_gera_broadcast(self):
        """Regressão: nada mudou (nem hp nem alvo) -- não deveria gerar
        broadcast nenhum, mesmo com target_eid agora fazendo parte da
        tupla comparada."""
        self.ws._sync_player_hp_dirty()
        self.assertIsNone(self._entry_for(self.player))

    def test_desmarcar_alvo_tambem_dispara_e_manda_minus_1(self):
        self.ws.set_player_target("s1", self.mob)
        self.ws._sync_player_hp_dirty()
        self.ws._player_hp_broadcasts_this_tick.clear()

        self.ws.set_player_target("s1", -1)
        self.ws._sync_player_hp_dirty()
        entry = self._entry_for(self.player)
        self.assertIsNotNone(entry, "desmarcar o alvo também precisa avisar quem está perto")
        self.assertEqual(entry["target_eid"], -1)

    def test_mudanca_de_hp_sozinha_ainda_inclui_target_eid_no_dict(self):
        """Regressão: dano normal (sem trocar de alvo) continua gerando
        broadcast de hp -- e o dict passa a ter target_eid (-1, sem alvo)
        junto, sem quebrar o teste antigo que só olhava hp/hp_max."""
        from engine.components import CombatStats
        cs = self.ws.world.get_component(self.player, CombatStats)
        cs.current_hp -= 10
        self.ws._sync_player_hp_dirty()
        entry = self._entry_for(self.player)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["hp"], cs.current_hp)
        self.assertEqual(entry["target_eid"], -1)


# ── Cliente ──────────────────────────────────────────────────────────────────

def _make_net_fixture(my_eid: int = -1):
    """Mesmo padrão de tests/test_client_ui.py::_make_net_fixture, só que
    parametrizado em `_my_eid` -- preciso de um valor real pra testar
    'o alvo do player remoto sou EU'."""
    from engine.world import World
    from client.network_handlers import NetworkHandlers as _NH
    from client.remote_entity_handlers import RemoteEntityHandlers as _REH

    class _Fixture(_NH, _REH):
        def __init__(self, world, player_entity):
            self.world = world
            self.player_entity = player_entity
            self._my_eid = my_eid
            self._remote_players = {}
            self._remote_player_move_queues = {}
            self._remote_step_timers = {}
            self._pvp_respawn_target = -1
            self._remote_mobs = {}
            self._remote_mob_projectiles = {}
            self._mob_move_queues = {}
            self._pending_mob_despawn = {}
            self._mob_ghost_pos = {}
            self._pending_loot_redirect = {}
            self._remote_harvestables = {}
            self._available_loot = {}
            self._remote_corpses = {}
            from ui.spell_system import PlayerProjectileSystem as _PPSysFixture
            self._player_proj_system = _PPSysFixture(world, pygame.Surface((10, 10)), player_entity)

        def _player_world_pos(self):
            return (0.0, 0.0)

    world = World()
    player = world.create_entity()
    return _Fixture(world, player)


class TestSpawnRemotoResolveTargetEid(unittest.TestCase):

    def test_sem_target_eid_no_payload_nasce_com_minus_1(self):
        fx = _make_net_fixture()
        fx._handle_msg_entity_spawn({
            "eid": 42, "kind": "player", "tx": 5, "ty": 5,
            "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
            "level": 1,
        })
        local_eid = fx._remote_players[42]
        cst = fx.world.get_component(local_eid, CombatState)
        self.assertIsNotNone(cst, "espelho remoto deveria nascer com CombatState")
        self.assertEqual(cst.target_entity_id, -1)

    def test_target_eid_apontando_pro_proprio_player_local(self):
        fx = _make_net_fixture(my_eid=999)
        fx._handle_msg_entity_spawn({
            "eid": 42, "kind": "player", "tx": 5, "ty": 5,
            "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
            "level": 1, "target_eid": 999,
        })
        local_eid = fx._remote_players[42]
        cst = fx.world.get_component(local_eid, CombatState)
        self.assertEqual(cst.target_entity_id, fx.player_entity)

    def test_target_eid_apontando_pra_outro_player_remoto_ja_conhecido(self):
        fx = _make_net_fixture()
        # Spawna primeiro o "alvo" (outro remoto), depois o atacante apontando pra ele.
        fx._handle_msg_entity_spawn({
            "eid": 7, "kind": "player", "tx": 1, "ty": 1,
            "name": "Alvo", "class_id": "guerreiro", "hp": 100, "hp_max": 100, "level": 1,
        })
        fx._handle_msg_entity_spawn({
            "eid": 42, "kind": "player", "tx": 5, "ty": 5,
            "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
            "level": 1, "target_eid": 7,
        })
        local_attacker = fx._remote_players[42]
        local_target = fx._remote_players[7]
        cst = fx.world.get_component(local_attacker, CombatState)
        self.assertEqual(cst.target_entity_id, local_target)

    def test_target_eid_desconhecido_cai_em_minus_1_sem_crash(self):
        fx = _make_net_fixture()
        fx._handle_msg_entity_spawn({
            "eid": 42, "kind": "player", "tx": 5, "ty": 5,
            "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
            "level": 1, "target_eid": 9999,  # nunca spawnado neste cliente
        })
        local_eid = fx._remote_players[42]
        cst = fx.world.get_component(local_eid, CombatState)
        self.assertEqual(cst.target_entity_id, -1)

    def test_eid_local_zero_nao_quebra_resolucao(self):
        """Regressão específica: a primeira entidade criada numa World tem
        eid LOCAL 0 -- um "a.get(x) or b.get(x) or -1" trataria 0 como
        falsy e cairia no fallback errado. Aqui forçamos esse cenário de
        propósito: a entidade "alvo" nasce com eid local 0."""
        fx = _make_net_fixture()
        # fx.player_entity já consumiu o eid 0 (criado em _make_net_fixture) --
        # força manualmente um remoto conhecido com eid local 0 mesmo assim,
        # simulando o pior caso (qualquer entidade podendo ser 0).
        fx._remote_players[7] = 0
        fx._handle_msg_entity_spawn({
            "eid": 42, "kind": "player", "tx": 5, "ty": 5,
            "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
            "level": 1, "target_eid": 7,
        })
        local_attacker = fx._remote_players[42]
        cst = fx.world.get_component(local_attacker, CombatState)
        self.assertEqual(cst.target_entity_id, 0,
            "eid local 0 é válido -- não deveria cair em -1 por ser 'falsy'")


class TestStatsUpdateAtualizaTargetDoRemoto(unittest.TestCase):

    def setUp(self):
        self.fx = _make_net_fixture()
        self.fx._handle_msg_entity_spawn({
            "eid": 42, "kind": "player", "tx": 5, "ty": 5,
            "name": "Fulano", "class_id": "mago", "hp": 80, "hp_max": 100,
            "level": 1,
        })
        self.fx._handle_msg_entity_spawn({
            "eid": 7, "kind": "player", "tx": 1, "ty": 1,
            "name": "Alvo", "class_id": "guerreiro", "hp": 100, "hp_max": 100, "level": 1,
        })
        self.local_attacker = self.fx._remote_players[42]
        self.local_target = self.fx._remote_players[7]

    def test_stats_update_seta_novo_alvo(self):
        self.fx._handle_msg_stats_update({"eid": 42, "target_eid": 7})
        cst = self.fx.world.get_component(self.local_attacker, CombatState)
        self.assertEqual(cst.target_entity_id, self.local_target)

    def test_stats_update_sem_target_eid_nao_altera_nada(self):
        """Regressão: um STATS_UPDATE só de hp (sem target_eid) não deve
        tocar no CombatState -- continua -1 de sempre."""
        self.fx._handle_msg_stats_update({"eid": 42, "hp": 50})
        cst = self.fx.world.get_component(self.local_attacker, CombatState)
        self.assertEqual(cst.target_entity_id, -1)

    def test_stats_update_minus_1_desmarca_alvo_anterior(self):
        self.fx._handle_msg_stats_update({"eid": 42, "target_eid": 7})
        self.fx._handle_msg_stats_update({"eid": 42, "target_eid": -1})
        cst = self.fx.world.get_component(self.local_attacker, CombatState)
        self.assertEqual(cst.target_entity_id, -1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
