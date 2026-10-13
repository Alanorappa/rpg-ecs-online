"""
tests/test_mob_target_facing.py — 11/10/2026, bug real relatado pelo
usuário: mob PARADO atacando continuava com a animação de idle/attack
"olhando" pra última direção que ele andou, mesmo com o player mudando
de lado — porque o mob nunca mandava pro cliente quem ele está
atacando.

Mesma causa raiz/mesmo mecanismo já corrigido pro player remoto
(ver tests/test_remote_target_facing.py) — `TileMovementSystem`
(engine/world_systems.py) já vira `facing` pro alvo quando a entidade
tem `CombatState.target_entity_id` setado e está parada; só faltava
o servidor sincronizar isso pra mob (fonte real é
`AIControlled.target_eid`, não `CombatState` — mob nunca escreve
`CombatState.target_entity_id` sozinho).

Fix: `WorldServer._collect_mob_targets()` (snapshot a cada tick, mesmo
padrão de `_collect_mob_effects`) + `target_eid` no payload de spawn
(`_build_mob_spawn_payload`). Cliente anexa `CombatState` ao espelho do
mob (`_spawn_remote_mob`) e sincroniza via `_sync_mob_targets`.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks, first_ai_mob
from engine.components import CombatState, AIControlled


# ── Servidor ─────────────────────────────────────────────────────────────────

class TestCollectMobTargets(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.player = spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)  # registra mobs reais do mapa em _mob_eids
        self.mob = first_ai_mob(self.ws)
        self.assertIsNotNone(self.mob, "mapa de teste precisa ter pelo menos 1 mob com AIControlled")

    def test_vazio_quando_mob_sem_alvo(self):
        # Mapa de teste tem outros mobs (jungle/minion) combatendo entre
        # si de verdade depois de 40 ticks -- o dict GLOBAL não fica
        # vazio por causa deles; só confere que O MOB DO TESTE não entra.
        ai = self.ws.world.get_component(self.mob, AIControlled)
        ai.target_eid = -1
        self.assertNotIn(str(self.mob), self.ws._collect_mob_targets())

    def test_populado_quando_mob_tem_alvo(self):
        ai = self.ws.world.get_component(self.mob, AIControlled)
        ai.target_eid = self.player
        result = self.ws._collect_mob_targets()
        self.assertEqual(result.get(str(self.mob)), self.player)

    def test_mob_sem_target_eid_nao_aparece_no_dict(self):
        """Só mobs com alvo ATIVO entram no snapshot — mesmo princípio de
        _collect_mob_effects (nunca lista mob sem nada pra reportar)."""
        ai = self.ws.world.get_component(self.mob, AIControlled)
        ai.target_eid = -1
        result = self.ws._collect_mob_targets()
        self.assertNotIn(str(self.mob), result)


class TestBuildMobSpawnPayloadIncluiTarget(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.player = spawn_player(self.ws, "s1", 130, 374)
        run_ticks(self.ws, 40)
        self.mob = first_ai_mob(self.ws)
        self.assertIsNotNone(self.mob)

    def test_sem_alvo_payload_nao_tem_o_campo(self):
        from engine.components import TileMovement
        ai = self.ws.world.get_component(self.mob, AIControlled)
        ai.target_eid = -1
        tm = self.ws.world.get_component(self.mob, TileMovement)
        payload = self.ws._build_mob_spawn_payload(self.mob, tm)
        self.assertNotIn("target_eid", payload)

    def test_com_alvo_payload_inclui_target_eid(self):
        from engine.components import TileMovement
        ai = self.ws.world.get_component(self.mob, AIControlled)
        ai.target_eid = self.player
        tm = self.ws.world.get_component(self.mob, TileMovement)
        payload = self.ws._build_mob_spawn_payload(self.mob, tm)
        self.assertEqual(payload["target_eid"], self.player)


class TestMergeDeltasIncluiMobTargets(unittest.TestCase):
    """server/session.py::SessionManager._merge_deltas_into — mob_targets
    precisa estar na MESMA regra de substituição "foto do estado atual"
    que effects/mob_effects já têm, nunca concatenar (senão um dict
    acumulado de 2+ ticks ficaria com entradas desatualizadas de mobs que
    já perderam o alvo)."""

    def test_mob_targets_substitui_nunca_concatena(self):
        from server.session import SessionManager
        acc = {"mob_targets": {"5": 10}}
        new = {"mob_targets": {"7": 20}}
        SessionManager._merge_deltas_into(acc, new)
        self.assertEqual(acc["mob_targets"], {"7": 20},
                         "deveria SUBSTITUIR o dict antigo, nunca somar as 2 entradas")


# ── Cliente ──────────────────────────────────────────────────────────────────

def _make_net_fixture(my_eid: int = -1):
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
            self._dying_mobs = {}
            self._pending_death_race_by_tile = {}
            self._mob_ghost_pos = {}
            self._pending_loot_redirect = {}
            self._remote_harvestables = {}
            self._available_loot = {}
            self._remote_corpses = {}

        def _player_world_pos(self):
            return (0.0, 0.0)

    world = World()
    player = world.create_entity()
    return _Fixture(world, player)


class TestResolveServerEid(unittest.TestCase):

    def test_minus_1_resolve_pra_minus_1(self):
        fx = _make_net_fixture()
        self.assertEqual(fx._resolve_server_eid(-1), -1)

    def test_proprio_eid_resolve_pro_player_local(self):
        fx = _make_net_fixture(my_eid=999)
        self.assertEqual(fx._resolve_server_eid(999), fx.player_entity)

    def test_eid_desconhecido_resolve_pra_minus_1(self):
        fx = _make_net_fixture()
        self.assertEqual(fx._resolve_server_eid(12345), -1)

    def test_eid_local_zero_nao_quebra_resolucao(self):
        """Mesma armadilha já documentada pro player (eid local 0 é
        válido, não pode cair no fallback por ser 'falsy')."""
        fx = _make_net_fixture()
        fx._remote_mobs[7] = 0
        self.assertEqual(fx._resolve_server_eid(7), 0)


class TestSpawnRemoteMobResolveTarget(unittest.TestCase):

    def test_sem_target_eid_nasce_com_combat_state_minus_1(self):
        fx = _make_net_fixture()
        fx._spawn_remote_mob(55, {
            "tx": 5, "ty": 5, "race": "Goblin", "entity_class": "Guerreiro",
            "tier": "normal", "hp": 50, "hp_max": 50, "level": 1,
        })
        local_eid = fx._remote_mobs[55]
        cst = fx.world.get_component(local_eid, CombatState)
        self.assertIsNotNone(cst, "mob remoto deveria nascer com CombatState (11/10/2026)")
        self.assertEqual(cst.target_entity_id, -1)

    def test_com_target_eid_apontando_pro_player_local(self):
        fx = _make_net_fixture(my_eid=999)
        fx._spawn_remote_mob(55, {
            "tx": 5, "ty": 5, "race": "Goblin", "entity_class": "Guerreiro",
            "tier": "normal", "hp": 50, "hp_max": 50, "level": 1,
            "target_eid": 999,
        })
        local_eid = fx._remote_mobs[55]
        cst = fx.world.get_component(local_eid, CombatState)
        self.assertEqual(cst.target_entity_id, fx.player_entity)


class TestSyncMobTargets(unittest.TestCase):

    def setUp(self):
        self.fx = _make_net_fixture(my_eid=999)
        self.fx._spawn_remote_mob(55, {
            "tx": 5, "ty": 5, "race": "Goblin", "entity_class": "Guerreiro",
            "tier": "normal", "hp": 50, "hp_max": 50, "level": 1,
        })
        self.local_mob = self.fx._remote_mobs[55]

    def test_seta_alvo_novo(self):
        self.fx._sync_mob_targets({"55": 999})
        cst = self.fx.world.get_component(self.local_mob, CombatState)
        self.assertEqual(cst.target_entity_id, self.fx.player_entity)

    def test_mob_nao_reportado_limpa_alvo(self):
        """Reprodução direta do "senão fica travado": mob que PARA de
        reportar alvo (perdeu o player de vista/saiu de combate) precisa
        voltar pra -1, não ficar com o último alvo conhecido pra sempre."""
        self.fx._sync_mob_targets({"55": 999})
        self.fx._sync_mob_targets({})  # tick seguinte: servidor não reportou mais
        cst = self.fx.world.get_component(self.local_mob, CombatState)
        self.assertEqual(cst.target_entity_id, -1)


class TestFacingRecalcFuncionaParaMobComCombatState(unittest.TestCase):
    """Reprodução fim-a-fim do bug relatado: mob PARADO com CombatState.
    target_entity_id setado (via o mecanismo novo) vira de frente pro
    alvo quando o alvo muda de lado — usando o MESMO TileMovementSystem
    genérico que já faz isso pro player, sem nenhuma lógica de giro
    nova."""

    def test_mob_parado_vira_de_frente_pro_alvo_que_mudou_de_lado(self):
        from engine.world import World
        from engine.components import Position, TileMovement
        from engine.world_systems import TileMovementSystem

        world = World()
        mob = world.create_entity()
        world.add_component(mob, Position(x=100, y=100))
        world.add_component(mob, TileMovement(is_moving=False, facing="down"))
        world.add_component(mob, CombatState())

        player = world.create_entity()
        # Alvo começa ABAIXO do mob (facing="down" já bate) --
        world.add_component(player, Position(x=100, y=132))
        cst = world.get_component(mob, CombatState)
        cst.target_entity_id = player

        tms = TileMovementSystem(world)
        tms.update([], dt=0.05)
        self.assertEqual(world.get_component(mob, TileMovement).facing, "down")

        # Player anda pro lado DIREITO do mob -- facing precisa acompanhar,
        # mesmo com o mob continuando parado (nunca mais pisa/anda).
        world.get_component(player, Position).x = 132
        world.get_component(player, Position).y = 100
        tms.update([], dt=0.05)
        self.assertEqual(world.get_component(mob, TileMovement).facing, "right",
            "mob parado deveria virar de frente pro alvo que mudou de lado")


if __name__ == "__main__":
    unittest.main(verbosity=2)
