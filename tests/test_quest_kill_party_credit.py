"""
tests/test_quest_kill_party_credit.py — bug real relatado pelo usuário
(03/10/2026): matar um mob só contava pra quest de QUEM BATEU PRIMEIRO
(first-attacker), nunca pro resto do grupo — mesmo que o portador da
quest estivesse no mesmo grupo e não tivesse participado da luta.

Causa raiz: `server/server_death_handler.py` disparava o evento de
quest "kill" só com `player_eid=first_attacker_eid`
(`engine/quest_events.py::fire`), sem nenhum fan-out pro grupo --
diferente do loot condicional de quest (collect_item,
`server/loot_processor.py::_resolve_conditional_loot_for`), que já
usa `get_party_members` pra compartilhar com o grupo inteiro desde a
Fase L1 (25/07/2026). Fix: mesmo `get_party_members` aplicado ao
evento "kill", sem gate de distância/AOI (mesmo precedente do
collect_item -- diferente do XP, que usa PARTY_XP_SHARE_RADIUS_TILES;
pedido do usuário foi explícito: conta mesmo sem ter participado da
luta, sem mencionar restrição de posição).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

import unittest

from tests.helpers import make_world_server, spawn_player
from engine.components import CombatStats, QuestLog
from engine.entity_factory import create_enemy
from engine.core_systems import apply_damage_core


def _kill_mob_with(ws, killer_eid: int, tx: int = 150, ty: int = 150) -> None:
    """Cria um mob e mata com `killer_eid` como killer -- mesmo padrão
    já usado em tests/test_on_kill_charge_skills.py. `_process_quest_events()`
    drena a fila de eventos pra dentro do QuestLog de cada um -- passo
    SEPARADO do tick normal (`WorldServer._tick`), nunca automático
    dentro de `_death_handler.update()`."""
    mob = create_enemy(ws.world, tx, ty, faction="monstros_hostis")
    mob_cs = ws.world.get_component(mob, CombatStats)
    apply_damage_core(ws.world, mob, mob_cs.current_hp + 999, killer_eid=killer_eid)
    ws._death_handler.update()
    ws._process_quest_events()


class TestQuestKillCompartilhadoComOGrupo(unittest.TestCase):
    """O cenário exato do bug relatado: A mata, B (no mesmo grupo, SEM
    participar da luta) tem a quest "first_blood" (kill, target="*",
    count=1) -- precisa progredir pra B mesmo assim."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", 130, 374)   # mata
        self.b = spawn_player(self.ws, "s2", 131, 374)   # só tem a quest
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        _req, started = self.ws.respond_party_invite(self.b, accept=True)
        self.assertTrue(started, "setup: grupo precisa ter sido formado")

    def _give_quest(self, eid: int, qid: str = "first_blood") -> None:
        ql = self.ws.world.get_component(eid, QuestLog)
        ql.active[qid] = [0]

    def test_b_progride_mesmo_sem_participar_da_luta(self):
        self._give_quest(self.b)
        _kill_mob_with(self.ws, killer_eid=self.a)

        ql_b = self.ws.world.get_component(self.b, QuestLog)
        self.assertEqual(ql_b.active["first_blood"][0], 1,
                         "B tem a quest e está no grupo de A -- precisa progredir "
                         "mesmo sem ter batido no mob")

    def test_a_tambem_progride_normalmente_quem_matou_de_verdade(self):
        """Regressão -- o killer de verdade continua sendo creditado,
        igual sempre foi (não é só o resto do grupo que passou a
        ganhar, o próprio killer não pode parar de ganhar)."""
        self._give_quest(self.a)
        _kill_mob_with(self.ws, killer_eid=self.a)

        ql_a = self.ws.world.get_component(self.a, QuestLog)
        self.assertEqual(ql_a.active["first_blood"][0], 1)

    def test_os_dois_progridem_juntos_quando_ambos_tem_a_mesma_quest(self):
        self._give_quest(self.a)
        self._give_quest(self.b)
        _kill_mob_with(self.ws, killer_eid=self.a)

        self.assertEqual(self.ws.world.get_component(self.a, QuestLog).active["first_blood"][0], 1)
        self.assertEqual(self.ws.world.get_component(self.b, QuestLog).active["first_blood"][0], 1)

    def test_quest_ja_completa_nao_passa_do_count(self):
        """match_objective já ignora objetivo saturado (prog[i]>=count)
        -- confirma que o fan-out novo não quebra esse guard existente."""
        self._give_quest(self.b)
        ql_b = self.ws.world.get_component(self.b, QuestLog)
        ql_b.active["first_blood"][0] = 1   # já no count máximo (count=1)
        _kill_mob_with(self.ws, killer_eid=self.a)
        self.assertEqual(ql_b.active["first_blood"][0], 1, "não deve passar do count")


class TestQuestKillSemGrupoNaoCompartilha(unittest.TestCase):
    """Controle negativo -- sem estarem no MESMO grupo, a quest de um
    estranho não deve progredir com o kill de outro player."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", 130, 374)
        self.stranger = spawn_player(self.ws, "s2", 200, 200)   # nunca agrupado com A

    def test_estranho_fora_do_grupo_nao_progride(self):
        ql_s = self.ws.world.get_component(self.stranger, QuestLog)
        ql_s.active["first_blood"] = [0]
        _kill_mob_with(self.ws, killer_eid=self.a)
        self.assertEqual(ql_s.active["first_blood"][0], 0,
                         "quem não está no grupo do killer não deveria progredir")


class TestQuestKillGrupoDe3(unittest.TestCase):
    """Garante que o fan-out cobre o grupo inteiro, não só 2 pessoas --
    3º membro, que também não participou da luta, também progride."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", 130, 374)
        self.b = spawn_player(self.ws, "s2", 131, 374)
        self.c = spawn_player(self.ws, "s3", 132, 374)
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        self.ws.respond_party_invite(self.b, accept=True)
        self.assertIsNone(self.ws.request_party_invite(self.a, self.c))
        _req, started = self.ws.respond_party_invite(self.c, accept=True)
        self.assertTrue(started)

    def test_terceiro_membro_tambem_progride(self):
        for eid in (self.a, self.b, self.c):
            ql = self.ws.world.get_component(eid, QuestLog)
            ql.active["first_blood"] = [0]
        _kill_mob_with(self.ws, killer_eid=self.b)   # quem mata agora é B

        for eid in (self.a, self.b, self.c):
            ql = self.ws.world.get_component(eid, QuestLog)
            self.assertEqual(ql.active["first_blood"][0], 1,
                             f"eid {eid} deveria ter progredido (grupo de 3)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
